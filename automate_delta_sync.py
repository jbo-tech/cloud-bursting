#!/usr/bin/env python3
"""
automate_delta_sync.py - Delta Sync Plex dans le cloud (Scaleway)

Ce script:
1. Crée une instance cloud puissante
2. Injecte la DB Plex existante du ZimaBoard
3. Lance un scan incrémental pour les nouveaux fichiers
4. Complète l'analyse Sonic sur les pistes non analysées
5. Rapatrie la DB enrichie

Avantage vs automate_scan.py:
- Ne rescanne pas les 456k+ pistes existantes
- Conserve toutes les métadonnées et analyses Sonic existantes
- Beaucoup plus rapide et économique

Prérequis:
- Archive DB exportée depuis ZimaBoard (./export_zimaboard_db.sh)
- Archive présente localement (ex: ./plex_db_only_XXXXXX.tar.gz)

Usage:
    # Production complète
    python automate_delta_sync.py --instance superpower

    # Mode test rapide : skip Sonic, validation workflow uniquement
    python automate_delta_sync.py --instance power --quick-test

    # Traiter uniquement certaines sections (filtrage par nom)
    python automate_delta_sync.py --instance superpower --section Movies
    python automate_delta_sync.py --section Movies --section "TV Shows"

    # Combinaison : test minimal (juste scan Movies, sans Sonic)
    python automate_delta_sync.py --quick-test --section Movies

    # Sauvegarder l'output terminal + collecter les logs Plex
    python automate_delta_sync.py --save-output --collect-logs

    # Garder l'instance après le scan (debug)
    python automate_delta_sync.py --instance power --keep

    # Détruire une instance existante
    python automate_delta_sync.py --destroy
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

# Import des modules common
from common.config import load_env, get_docker_limits, print_phase_header
from common.executor import execute_command, download_file_from_remote, docker_exec, read_state_file
from common.local import find_latest_db_archive
from common.plex_setup import (
    apply_system_optimizations,
    setup_rclone_config,
    mount_s3,
    start_plex_container,
    wait_plex_fully_ready,
    get_plex_token,
    verify_plex_pass_active,
    stop_plex,
    disable_all_background_tasks,
    enable_music_analysis_only,
    enable_all_analysis,
    collect_plex_logs
)
from common.mount_monitor import MountHealthMonitor
from common.plex_scan import (
    trigger_sonic_analysis,
    get_monitoring_params,
    export_metadata,
    wait_section_idle,
    wait_sonic_complete,
    wait_plex_stabilized,
    trigger_section_scan,
    trigger_section_analyze,
    export_intermediate
)
from common.delta_sync import (
    inject_existing_db,
    get_library_stats_from_db,
    print_injection_stats,
    verify_paths_match,
    load_path_mappings,
    remap_library_paths
)
from common.scaleway import (
    INSTANCE_PROFILES,
    create_instance,
    destroy_instance,
    wait_ssh_ready,
    wait_cloud_init,
    test_mega_bandwidth
)

# === FICHIERS D'ÉTAT ===
INSTANCE_ID_FILE = ".current_instance_id"
INSTANCE_IP_FILE = ".current_instance_ip"

# === CHEMINS CLOUD ===
CLOUD_MOUNT_POINT = "/mnt/s3-media"
CLOUD_CACHE_DIR = "/tmp/rclone-cache"
CLOUD_LOG_FILE = "/var/log/rclone.log"

# ============================================================================
# MAIN
# ============================================================================

def main():
    # === ARGUMENTS CLI ===
    parser = argparse.ArgumentParser(
        description='Delta Sync Plex dans le cloud (Scaleway)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ce script injecte une DB Plex existante et ne scanne que le delta.
Beaucoup plus rapide que automate_scan.py pour les mises à jour.

Profils d'instance:
  lite       : DEV1-S  (2 vCPU, 2GB)  - Tests uniquement
  standard   : DEV1-M  (3 vCPU, 4GB)  - Petites mises à jour
  power      : GP1-S   (8 vCPU, 16GB) - Mises à jour moyennes
  superpower : GP1-M   (8 vCPU, 32GB) - Analyse Sonic complète
        """
    )
    parser.add_argument('--archive', type=str, metavar='PATH',
                        help='Chemin vers l\'archive DB (auto-détecté si non spécifié)')
    parser.add_argument('--instance', choices=list(INSTANCE_PROFILES.keys()),
                        default='superpower', help='Profil d\'instance (default: superpower)')
    parser.add_argument('--quick-test', action='store_true',
                        help='Mode test rapide : skip Sonic, scan validation uniquement')
    parser.add_argument('--section', type=str, action='append', metavar='SECTION',
                        help='Traiter uniquement ces sections (répétable, ex: --section Movies)')
    parser.add_argument('--force-scan', action='store_true',
                        help='Forcer un scan complet au lieu d\'incrémental')
    parser.add_argument('--collect-logs', action='store_true',
                        help='Récupérer les logs Plex en fin de run')
    parser.add_argument('--save-output', action='store_true',
                        help='Sauvegarder l\'output terminal dans logs/')
    parser.add_argument('--force-refresh', action='store_true',
                        help='Refresh Metadata avant Sonic (invalide le cache interne Plex)')
    parser.add_argument('--keep', action='store_true',
                        help='Garder l\'instance après le scan')
    parser.add_argument('--destroy', action='store_true',
                        help='Détruire une instance existante et quitter')
    parser.add_argument('--test-mega', action='store_true',
                        help='Tester la bande passante MEGA avant de continuer')
    parser.add_argument('--monitoring', choices=['local', 'cloud'],
                        default='cloud', help='Profil monitoring: local (timeouts courts), cloud (patient)')
    parser.add_argument('--path-mappings', type=str, metavar='FILE',
                        help='Fichier de remapping des chemins (défaut: path_mappings.json)')

    args = parser.parse_args()

    # Mode destruction seule
    if args.destroy:
        destroy_instance()
        return

    # === VARIABLES GLOBALES POUR FINALLY ===
    tee_logger = None
    plex_logs_archive = None
    should_process_music = True  # Par défaut, sera mis à jour en phase 7
    RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
    instance_ip = None
    stats_before = None

    # === TERMINAL LOGGING ===
    if args.save_output:
        from common.tee_logger import TeeLogger
        tee_logger = TeeLogger(timestamp=RUN_TIMESTAMP)
        tee_logger.start()
        print(f"📝 Output terminal sauvegardé dans: {tee_logger.log_path}")

    try:
        # === TROUVER L'ARCHIVE ===
        archive_path = args.archive or find_latest_db_archive()

        if not archive_path:
            print("❌ Aucune archive DB trouvée.")
            print("")
            print("Pour créer une archive depuis le ZimaBoard:")
            print("  1. Copiez export_zimaboard_db.sh sur le ZimaBoard")
            print("  2. Exécutez: ./export_zimaboard_db.sh")
            print("  3. Récupérez l'archive: scp jbo@zimaboard:~/plex_db_only_*.tar.gz ./")
            print("")
            print("Ou spécifiez le chemin: --archive /path/to/archive.tar.gz")
            sys.exit(1)

        if not os.path.exists(archive_path):
            print(f"❌ Archive introuvable: {archive_path}")
            sys.exit(1)

        archive_size = os.path.getsize(archive_path) / (1024 * 1024 * 1024)

        # === CONFIGURATION ===
        env = load_env()
        profile = args.instance
        docker_limits = get_docker_limits(profile)

        print("=" * 60)
        print("🔄 CLOUD BURSTING - DELTA SYNC")
        print("=" * 60)
        print(f"Archive         : {archive_path} ({archive_size:.2f} GB)")
        print(f"Profil instance : {profile} ({INSTANCE_PROFILES[profile]['description']})")
        print(f"Mode scan       : {'FORCÉ' if args.force_scan else 'INCRÉMENTAL'}")
        print("=" * 60)

        # Variables pour le suivi
        plex_token = None
        can_do_sonic = False
        mount_monitor = None

        # === PHASE 1: CRÉATION INSTANCE ===
        instance_ip = create_instance(env, profile)

        # === PHASE 2: ATTENTE INITIALISATION ===
        wait_ssh_ready(instance_ip)
        wait_cloud_init(instance_ip)

        # === PHASE 3: CONFIGURATION ===
        print_phase_header(3, "CONFIGURATION ENVIRONNEMENT")

        apply_system_optimizations(instance_ip)
        setup_rclone_config(instance_ip)

        # Test MEGA si demandé
        if args.test_mega:
            mega_result = test_mega_bandwidth(instance_ip,"Music/9m88", timeout=300)
            if not mega_result['success']:
                print("\n⚠️  Test MEGA échoué!")
                if input("   Continuer quand même? (o/N) ").lower() != 'o':
                    sys.exit(1)


        # Préparer les dossiers Plex
        execute_command(instance_ip, "mkdir -p /opt/plex_data/{config,transcode}")
        execute_command(instance_ip, "chmod -R 777 /opt/plex_data")

        # === PHASE 4: INJECTION DB ===
        print_phase_header(4, "INJECTION DB EXISTANTE")

        # Injection (inject_existing_db gère le transfert de l'archive)
        success = inject_existing_db(
            instance_ip,
            archive_path,  # Chemin LOCAL - inject_existing_db gère le transfert
            '/opt/plex_data/config',
            container='plex'
        )

        if not success:
            print("❌ Échec de l'injection")
            sys.exit(1)

        # Stats avant démarrage Plex
        stats_before = get_library_stats_from_db(instance_ip, '/opt/plex_data/config')
        print_injection_stats(stats_before)

        # === PHASE 5: MONTAGE S3 ===
        print_phase_header(5, "MONTAGE S3")

        mount_s3(
            instance_ip,
            env['S3_BUCKET'],
            profile=profile,
            mount_point='/mnt/s3-media',
            cache_dir='/tmp/rclone-cache',
            log_file='/var/log/rclone.log'
        )

        # Vérifier les chemins
        path_check = verify_paths_match(instance_ip, '/opt/plex_data/config', '/mnt/s3-media')

        if not path_check['match']:
            print("\n⚠️  ATTENTION: Certains chemins ne correspondent pas!")
            for suggestion in path_check['suggestions']:
                print(f"   • {suggestion}")

            # Charger les mappings (si fichier existe et a des entrées)
            mappings_config = load_path_mappings(args.path_mappings)

            if mappings_config['mappings']:
                print(f"\n   📂 Fichier de mappings: {mappings_config['file']}")

                # Tenter le remapping automatique (backup dans /tmp sur l'instance)
                remap_result = remap_library_paths(
                    instance_ip,
                    '/opt/plex_data/config',
                    '/mnt/s3-media',
                    mappings_config['mappings'],
                    backup_dir='/tmp'
                )

                if remap_result['sections_remapped'] > 0:
                    # Re-vérifier après remapping
                    path_check = verify_paths_match(instance_ip, '/opt/plex_data/config', '/mnt/s3-media')
                    if path_check['match']:
                        print("\n   ✅ Tous les chemins correspondent après remapping")
                    else:
                        print("\n   ⚠️  Certains chemins restent incompatibles")
                elif remap_result['errors']:
                    print("\n   ⚠️  Remapping échoué, continuons quand même")
            else:
                print("\n   ⏭️  Pas de fichier path_mappings.json ou aucun mapping défini")
                print("   Continuons quand même - certaines bibliothèques fonctionneront.")

        # === PHASE 6: DÉMARRAGE PLEX ===
        print_phase_header(6, "DÉMARRAGE PLEX")

        # Claim token AVANT de démarrer le monitoring
        # (évite les deadlocks et messages parasites pendant l'input)
        plex_claim = input("\n🔑 Entrez votre PLEX_CLAIM (depuis https://www.plex.tv/claim) : ").strip()
        if not plex_claim:
            print("❌ PLEX_CLAIM requis")
            sys.exit(1)

        # Démarrer le monitoring du montage APRÈS avoir le claim
        mount_monitor = MountHealthMonitor(
            ip=instance_ip,
            mount_point=CLOUD_MOUNT_POINT,
            rclone_remote=env['S3_BUCKET'],
            profile=profile,
            cache_dir=CLOUD_CACHE_DIR,
            log_file=CLOUD_LOG_FILE,
            check_interval=60  # Vérification toutes les minutes
        )
        mount_monitor.start()

        start_plex_container(
            instance_ip,
            plex_claim,
            version=env.get('PLEX_VERSION', 'latest'),
            memory=docker_limits['memory'],
            memory_swap=docker_limits['memory_swap'],
            cpus=docker_limits['cpus'],
            config_path='/opt/plex_data/config',
            media_path='/mnt/s3-media',
            transcode_path='/opt/plex_data/transcode'
        )

        # Attente init Plex (timeout plus long en cloud avec DB injectée)
        plex_ready = wait_plex_fully_ready(instance_ip, container='plex', timeout=600)

        # Token API (avec retry intégré)
        plex_token = get_plex_token(instance_ip, container='plex', timeout=180)

        if not plex_ready:
            print("\n⚠️  Plex n'est pas complètement initialisé")
            if plex_token:
                print("   Token disponible - on tente de continuer...")
            else:
                print("   Token absent - fonctionnalités limitées")

        if not plex_token:
            print("\n⚠️  Token Plex non disponible - certaines fonctions seront limitées")

        # Vérifier Plex Pass (requis pour Sonic)
        can_do_sonic = False
        if plex_token and not args.quick_test:
            pass_status = verify_plex_pass_active(instance_ip, 'plex', plex_token, timeout=120)
            can_do_sonic = pass_status.get('active', False)
            if not can_do_sonic:
                print("⚠️  Plex Pass non actif - analyse Sonic indisponible")

        # Note: Les analyses Sonic seront activées en Phase 8 par enable_music_analysis_only()
        # Ne PAS appeler enable_plex_analysis_via_api() ici car cela déclenche le Butler
        # et interfère avec wait_section_idle() pendant le scan

        # === PHASE 7: VÉRIFICATION BIBLIOTHÈQUES ===
        print_phase_header(7, "VÉRIFICATION BIBLIOTHÈQUES")

        # Récupérer les sections depuis l'API
        section_info = {}
        api_cmd = f"curl -s 'http://localhost:32400/library/sections' -H 'X-Plex-Token: {plex_token}'"
        result = docker_exec(instance_ip, 'plex', api_cmd, capture_output=True, check=False)

        if result.stdout:
            for match in re.finditer(r'key="(\d+)".*?type="([^"]+)".*?title="([^"]+)"', result.stdout):
                s_id, s_type, s_title = match.group(1), match.group(2), match.group(3)
                section_info[s_title] = {"id": s_id, "type": s_type}

        print(f"📚 Sections trouvées: {len(section_info)}")
        for name, info in section_info.items():
            print(f"   [{info['id']}] {name} ({info['type']})")

        # Validation des sections demandées via --section
        if args.section:
            requested = set(args.section)
            available = set(section_info.keys())
            unknown = requested - available
            if unknown:
                print(f"\n⚠️  Sections ignorées: {unknown}")
                print(f"   Disponibles: {list(available)}")

        if not section_info:
            print("❌ Aucune section trouvée!")
            sys.exit(1)

        # === PHASE 8: TRAITEMENT MUSIQUE (Sonic) ===
        # Déterminer si on doit traiter la section Musique
        should_process_music = (
            not args.section
            or any(section_info.get(s, {}).get('type') == 'artist' for s in args.section)
        )

        # Initialiser stats_after_scan pour le cas où la phase Music est skippée
        stats_after_scan = stats_before

        if should_process_music:
            print_phase_header(8, "TRAITEMENT MUSIQUE (Sonic)")

            # 8.1 Désactivation tâches de fond
            print("\n8.1 Désactivation des tâches de fond...")
            disable_all_background_tasks(instance_ip, 'plex', plex_token)

            # 8.2 Trouver la section Musique
            music_section_id = None
            music_section_name = None
            for name, info in section_info.items():
                if info['type'] == 'artist':
                    music_section_id = info['id']
                    music_section_name = name
                    break

            if music_section_id:
                print(f"\n8.2 Scan de la section Musique [{music_section_id}] {music_section_name}...")

                trigger_section_scan(instance_ip, 'plex', plex_token, music_section_id, force=args.force_scan)

                # Attendre que le scan soit terminé
                wait_section_idle(instance_ip, 'plex', plex_token, music_section_id,
                                  section_type='artist', phase='scan', config_path='/opt/plex_data/config')

                # Analyse du delta de scan
                print("\n📊 Analyse du delta de scan:")
                stats_after_scan = get_library_stats_from_db(instance_ip, '/opt/plex_data/config')
                delta_tracks = stats_after_scan['tracks'] - stats_before['tracks']
                delta_artists = stats_after_scan['artists'] - stats_before['artists']
                print(f"   Nouvelles pistes   : +{delta_tracks}")
                print(f"   Nouveaux artistes  : +{delta_artists}")
            else:
                print("   ⚠️  Aucune section Musique trouvée")
                stats_after_scan = stats_before

            # 8.3 Analyse Sonic (sauf si --quick-test)
            if not args.quick_test:
                print("\n8.3 Analyse Sonic...")

                if not can_do_sonic:
                    print("   ⏭️  Analyse Sonic IGNORÉE (Plex Pass non actif)")
                elif not music_section_id:
                    print("   ⏭️  Analyse Sonic IGNORÉE (pas de section Musique)")
                else:
                    # Activer uniquement les analyses musicales
                    enable_music_analysis_only(instance_ip, 'plex', plex_token)

                    # 8.3a Refresh Metadata si demandé (images, paroles, matching)
                    # Important: ceci peut prendre plusieurs heures sur une grosse bibliothèque
                    if args.force_refresh:
                        print("\n8.3a Refresh Metadata (images, paroles, matching)...")
                        print("   ⚠️  Cette phase peut prendre plusieurs heures sur une grosse bibliothèque")
                        trigger_section_scan(instance_ip, 'plex', plex_token, music_section_id, force=True)

                        # Utiliser le profil metadata_refresh avec timeout étendu (4h)
                        metadata_params = get_monitoring_params('metadata_refresh')
                        print(f"   ⏳ Attente fin du refresh (timeout: {metadata_params['absolute_timeout']//3600}h)...")
                        wait_section_idle(instance_ip, 'plex', plex_token, music_section_id,
                                          section_type='artist', phase='scan', config_path='/opt/plex_data/config',
                                          timeout=metadata_params['absolute_timeout'],
                                          check_interval=metadata_params['check_interval'])
                        print("   ✅ Refresh metadata terminé.")

                        # 8.3b Stabilisation avant Sonic
                        # Attendre que toutes les tâches de fond (téléchargements, etc.) soient vraiment finies
                        print("\n8.3b Stabilisation avant Sonic...")
                        wait_plex_stabilized(instance_ip, 'plex', plex_token,
                                             cooldown_checks=3,
                                             check_interval=60,
                                             cpu_threshold=20.0,
                                             timeout=1800)

                    # 8.3c Lancer Sonic (sans --force, le refresh a été fait séparément)
                    print("\n8.3c Lancement analyse Sonic...")

                    trigger_sonic_analysis(instance_ip, music_section_id, 'plex')

                    # Monitoring avec profil cloud (24h timeout)
                    monitoring_profile = 'cloud_intensive' if args.monitoring == 'cloud' else 'local_delta'
                    monitoring_params = get_monitoring_params(monitoring_profile)

                    # Utiliser le monitor global démarré en phase 5
                    sonic_result = wait_sonic_complete(
                        instance_ip,
                        '/opt/plex_data/config',
                        music_section_id,
                        container='plex',
                        timeout=monitoring_params['absolute_timeout'],
                        check_interval=monitoring_params['check_interval'],
                        health_check_fn=mount_monitor.get_health_check_fn()
                    )

                    print(f"\n📊 Résultat analyse Sonic:")
                    print(f"   Initial  : {sonic_result['initial_count']} pistes")
                    print(f"   Final    : {sonic_result['final_count']} pistes")
                    print(f"   Delta    : +{sonic_result['delta']}")
                    print(f"   Durée    : {sonic_result['duration_minutes']} min")
                    print(f"   Raison   : {sonic_result['reason']}")
            else:
                print("\n8.3 Analyse Sonic SKIPPÉE (--quick-test)")

            # 8.4 Export intermédiaire (sécurisation après Sonic)
            print("\n8.4 Export intermédiaire...")
            export_intermediate(instance_ip, 'plex', '/opt/plex_data/config', '.', label="post_sonic")

            # Collecte logs intermédiaire si demandé
            if args.collect_logs or args.save_output:
                terminal_log = tee_logger.log_path if tee_logger else None
                collect_plex_logs(instance_ip, 'plex', prefix="phase8",
                                  terminal_log=terminal_log, rclone_log=CLOUD_LOG_FILE,
                                  timestamp=RUN_TIMESTAMP, keep_terminal_log=True)
        else:
            print_phase_header(8, "TRAITEMENT MUSIQUE (Sonic) - SKIPPÉE")
            print(f"⏭️  Aucune section musicale dans le filtre --section {args.section}")

        # === PHASE 9: VALIDATION AUTRES SECTIONS ===
        # Déterminer les sections à traiter (autres que Music)
        other_sections = [(name, info) for name, info in section_info.items()
                          if info['type'] != 'artist']

        if args.section:
            other_sections = [(name, info) for name, info in other_sections
                              if name in args.section]

        if other_sections:
            print_phase_header(9, "VALIDATION AUTRES SECTIONS")

            # 9.1 Réactivation analyses
            print("\n9.1 Réactivation des analyses (Photos/Vidéos)...")
            enable_all_analysis(instance_ip, 'plex', plex_token)

            # 9.2 Scan sections restantes (SÉQUENTIEL)
            print("\n9.2 Scan et analyse des sections restantes (séquentiel)...")

            for section_name, info in other_sections:
                # Scan de la section
                print(f"\n   🔍 Scan de '{section_name}' (ID: {info['id']}, type: {info['type']})")
                trigger_section_scan(instance_ip, 'plex', plex_token, info['id'], force=False)
                wait_section_idle(instance_ip, 'plex', plex_token, info['id'],
                                  section_type=info['type'], phase='scan',
                                  config_path='/opt/plex_data/config', timeout=3600)

                # Analyse de la section
                print(f"\n   🔬 Analyse de '{section_name}' (ID: {info['id']})")
                trigger_section_analyze(instance_ip, 'plex', plex_token, info['id'])
                wait_section_idle(instance_ip, 'plex', plex_token, info['id'],
                                  section_type=info['type'], phase='analyze',
                                  config_path='/opt/plex_data/config', timeout=3600)

            print("\n✅ Scan et analyse autres sections terminés")

            # 9.3 Récapitulatif
            print("\n9.3 Récapitulatif...")
            final_stats = get_library_stats_from_db(instance_ip, '/opt/plex_data/config')

            print(f"   Musique   : {final_stats['tracks']} pistes ({final_stats['artists']} artistes)")
            if final_stats.get('movies', 0) > 0 or stats_before.get('movies', 0) > 0:
                delta_movies = final_stats.get('movies', 0) - stats_before.get('movies', 0)
                print(f"   Films     : {final_stats.get('movies', 0)} (+{delta_movies})")
            if final_stats.get('episodes', 0) > 0 or stats_before.get('episodes', 0) > 0:
                delta_episodes = final_stats.get('episodes', 0) - stats_before.get('episodes', 0)
                print(f"   Épisodes  : {final_stats.get('episodes', 0)} (+{delta_episodes})")
            if final_stats.get('photos', 0) > 0 or stats_before.get('photos', 0) > 0:
                delta_photos = final_stats.get('photos', 0) - stats_before.get('photos', 0)
                print(f"   Photos    : {final_stats.get('photos', 0)} (+{delta_photos})")
        else:
            print_phase_header(9, "VALIDATION AUTRES SECTIONS - SKIPPÉE")
            print("⏭️  Aucune section à traiter")

        # === PHASE 10: EXPORT FINAL ===
        print_phase_header(10, "EXPORT FINAL")

        # 10.1 Collecte logs Plex AVANT arrêt (le conteneur doit tourner)
        if args.collect_logs or args.save_output:
            print("\n10.1 Collecte des logs Plex (conteneur actif)...")
            plex_logs_archive = collect_plex_logs(instance_ip, 'plex', prefix="final",
                                                   rclone_log=CLOUD_LOG_FILE, timestamp=RUN_TIMESTAMP)

        # 10.2 Arrêt Plex
        print("\n10.2 Arrêt de Plex...")
        stop_plex(instance_ip, container='plex')
        time.sleep(3)

        # 10.3 Export complet
        print("\n10.3 Export complet...")
        archive_name = f'plex_delta_sync_{RUN_TIMESTAMP}.tar.gz'

        archive_remote = export_metadata(
            instance_ip,
            container='plex',
            archive_name=archive_name,
            config_path='/opt/plex_data/config'
        )

        # Télécharger
        local_archive = f'./{archive_name}'
        download_file_from_remote(instance_ip, archive_remote, local_archive)

        # 10.4 Résumé final
        print("\n10.4 Résumé final...")
        print("\n" + "=" * 60)
        print("✅ DELTA SYNC TERMINÉ")
        print("=" * 60)
        print(f"📦 Archive principale : {local_archive}")

        final_stats = get_library_stats_from_db(instance_ip, '/opt/plex_data/config')
        sonic_delta = final_stats['tracks_with_sonic'] - stats_before['tracks_with_sonic']
        print(f"\n📊 Statistiques:")
        print(f"   Pistes totales    : {final_stats['tracks']}")
        print(f"   Pistes Sonic      : {final_stats['tracks_with_sonic']} (+{sonic_delta})")
        print(f"   Artistes          : {final_stats['artists']}")
        if final_stats.get('movies', 0) > 0:
            print(f"   Films             : {final_stats.get('movies', 0)}")
        if final_stats.get('episodes', 0) > 0:
            print(f"   Épisodes          : {final_stats.get('episodes', 0)}")
        if final_stats.get('photos', 0) > 0:
            print(f"   Photos            : {final_stats.get('photos', 0)}")

        print("\n🔄 Pour appliquer sur le serveur Plex local:")
        print(f"   ./update_to_distant_plex.sh {archive_name}")

    except KeyboardInterrupt:
        print("\n\n⚠️  Workflow interrompu par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Arrêter le monitor de montage s'il est actif
        if mount_monitor is not None:
            mount_monitor.stop()

        # === DIAGNOSTIC POST-MORTEM ===
        print("\n" + "=" * 60)
        print("🔍 DIAGNOSTIC POST-MORTEM")
        print("=" * 60)

        if instance_ip:
            # Vérifier OOM
            oom_cmd = "docker inspect plex --format '{{.State.OOMKilled}}' 2>/dev/null || echo 'N/A'"
            oom_result = execute_command(instance_ip, oom_cmd, capture_output=True, check=False)
            is_oom = oom_result.stdout.strip() == 'true'

            if is_oom:
                print("🚨 ALERTE: Conteneur tué par manque de mémoire (OOM)")
            else:
                print("✅ Pas de kill mémoire (OOM)")

            # Diagnostic Sonic (seulement si musique sélectionnée)
            if should_process_music:
                print("\n🎹 DIAGNOSTIC SONIC:")
                db_path = "/config/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

                sonic_count_cmd = f'''docker exec plex sqlite3 "{db_path}" "
                    SELECT
                        (SELECT COUNT(*) FROM media_item_settings WHERE loudness != 0) as loudness_count,
                        (SELECT COUNT(*) FROM media_parts WHERE extra_data LIKE '%hasSonicAnalysis%1%') as sonic_flag_count
                " 2>/dev/null || echo "DB inaccessible"'''
                sonic_result = execute_command(instance_ip, sonic_count_cmd, capture_output=True, check=False)
                print(f"   Comptage Sonic (loudness vs extra_data):")
                print(f"   {sonic_result.stdout.strip()}")

            # Dernières logs
            print("\n📋 Dernières logs Docker:")
            execute_command(instance_ip, "docker logs plex --tail 20 2>&1 || true", check=False)
        else:
            print("⚠️  Instance non disponible pour diagnostic")

        # Arrêter le TeeLogger et créer l'archive finale combinée
        if tee_logger:
            terminal_log_path = tee_logger.log_path
            tee_logger.stop()

            # Créer l'archive finale combinée (Plex logs + terminal complet)
            if os.path.exists(terminal_log_path):
                import tarfile
                import tempfile
                import shutil

                final_archive = f"logs/{RUN_TIMESTAMP}_logs_final_all.tar.gz"
                os.makedirs("logs", exist_ok=True)

                print(f"\n📦 Création archive finale combinée...")
                temp_dir = tempfile.mkdtemp(prefix="final_logs_")
                try:
                    # Extraire les logs Plex si disponibles
                    if plex_logs_archive and os.path.exists(plex_logs_archive):
                        with tarfile.open(plex_logs_archive, 'r:gz') as tar:
                            tar.extractall(temp_dir)
                        os.remove(plex_logs_archive)

                    # Ajouter le terminal log complet
                    shutil.copy(terminal_log_path, os.path.join(temp_dir, f"output_{RUN_TIMESTAMP}.txt"))

                    # Créer l'archive finale
                    with tarfile.open(final_archive, 'w:gz') as tar:
                        for item in os.listdir(temp_dir):
                            tar.add(os.path.join(temp_dir, item), arcname=item)

                    size_mb = os.path.getsize(final_archive) / (1024*1024)
                    print(f"   ✅ Archive finale: {final_archive} ({size_mb:.1f} MB)")

                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)

                # Supprimer le fichier terminal brut
                os.remove(terminal_log_path)

        # Destruction de l'instance
        if not args.keep:
            destroy_instance()
        else:
            instance_ip_saved = read_state_file(INSTANCE_IP_FILE)
            print(f"\n💾 Instance conservée (--keep)")
            print(f"   SSH: ssh root@{instance_ip_saved}")
            print(f"   Destruction: python automate_delta_sync.py --destroy")

        print("\n👋 Terminé")


if __name__ == "__main__":
    main()
