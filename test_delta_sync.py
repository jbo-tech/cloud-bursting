#!/usr/bin/env python3
"""
test_delta_sync.py - Test local du workflow de synchronisation incrémentale

Ce script implémente une stratégie de synchronisation delta (incrémentale):
1. Injecte une base de données Plex existante (du ZimaBoard)
2. Monte le bucket S3 via rclone en local
3. Démarre Plex avec la DB existante (métadonnées déjà présentes)
4. Lance un scan incrémental pour détecter uniquement les nouveaux fichiers
5. Lance l'analyse Sonic sur les pistes non encore analysées
6. Exporte la DB enrichie avec les nouvelles métadonnées

Objectif: Mise à jour incrémentale plutôt que scan complet depuis zéro.
Gain de temps considérable pour les grosses bibliothèques (~9 To).

Prérequis:
- Docker installé et démarré (pour conteneur Plex)
- rclone installé et configuré (fichier rclone.conf)
- Fichier .env avec S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, PLEX_VERSION
- FUSE configuré avec user_allow_other (/etc/fuse.conf)
- Archive DB exportée depuis ZimaBoard (via export_zimaboard_db.sh)
  Format: plex_db_only_XXXXXX.tar.gz ou plex_metadata_XXXXXX.tar.gz

Usage:
    # Delta sync avec auto-détection de l'archive la plus récente
    python test_delta_sync.py --instance lite

    # Spécifier une archive DB particulière
    python test_delta_sync.py --archive ./plex_db_only_20251220.tar.gz

    # Forcer un scan complet au lieu d'incrémental
    python test_delta_sync.py --force-scan

    # Test avec filtre sur bibliothèque musicale (artistes commençant par Q)
    python test_delta_sync.py --filter Q

    # Mode test rapide : skip Sonic, validation workflow uniquement
    python test_delta_sync.py --quick-test

    # Traiter uniquement certaines sections (filtrage par nom)
    python test_delta_sync.py --section Movies
    python test_delta_sync.py --section Movies --section "TV Shows"

    # Combinaison : test minimal (juste scan Movies, sans Sonic)
    python test_delta_sync.py --quick-test --section Movies

    # Monitoring cloud : timeouts étendus (24h Sonic) + exports intermédiaires
    python test_delta_sync.py --monitoring cloud

    # Récupérer les logs Plex en fin de run (debug)
    python test_delta_sync.py --collect-logs

    # Garder le conteneur après test (debug)
    python test_delta_sync.py --keep
"""

# === IMPORTS ===
import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# Imports modules common
from common.config import load_env, get_docker_limits, print_phase_header
from common.executor import execute_command, docker_exec
from common.local import setup_local_test_env, cleanup_local_test_env, find_latest_db_archive
from common.plex_setup import (
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
    scan_section_incrementally,
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
    verify_paths_match
)

# === CONFIGURATION ===
# Dossiers de travail pour environnement de test local
TEST_DIR = Path(__file__).parent / "tmp"
MOUNT_DIR = TEST_DIR / "s3-media"
PLEX_CONFIG = TEST_DIR / "plex-config"
CACHE_DIR = TEST_DIR / "rclone-cache"
LOG_FILE = TEST_DIR / "rclone.log"

# ============================================================================
# MAIN
# ============================================================================

def main():
    """Point d'entrée principal du script."""
    # === ARGUMENTS CLI ===
    parser = argparse.ArgumentParser(description='Test Delta Sync - injection DB existante')
    parser.add_argument('--archive', type=str, metavar='PATH',
                        help='Chemin vers l\'archive DB (auto-détecté si non spécifié)')
    parser.add_argument('--instance', choices=['lite', 'standard', 'power', 'superpower'],
                        default='standard', help='Profil rclone (lite=conservateur, standard=équilibré)')
    parser.add_argument('--keep', action='store_true',
                        help='Garder le conteneur après test')
    parser.add_argument('--force-scan', action='store_true',
                        help='Forcer un scan complet (force=1) au lieu d\'incrémental')
    parser.add_argument('--filter', type=str, metavar='PREFIX',
                        help='Filtrer le scan music par préfixe (ex: --filter Q)')
    parser.add_argument('--quick-test', action='store_true',
                        help='Mode test rapide : skip Sonic, scan validation uniquement')
    parser.add_argument('--section', type=str, action='append', metavar='SECTION',
                        help='Traiter uniquement ces sections (répétable, ex: --section Movies)')
    parser.add_argument('--force-refresh', action='store_true',
                        help='Refresh Metadata avant Sonic (invalide le cache interne Plex)')
    parser.add_argument('--collect-logs', action='store_true',
                        help='Récupérer les logs Plex en fin de run')
    parser.add_argument('--save-output', action='store_true',
                        help='Sauvegarder l\'output terminal dans logs/')
    parser.add_argument('--monitoring', choices=['local', 'cloud'],
                        default='local', help='Profil monitoring: local (timeouts courts), cloud (patient)')

    args = parser.parse_args()

    # === VARIABLES GLOBALES POUR FINALLY ===
    tee_logger = None
    plex_logs_archive = None
    RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

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
            print("  3. Récupérez l'archive: scp jbo@zimaboard:/tmp/plex_db_only_*.tar.gz ./")
            print("")
            print("Ou spécifiez le chemin: --archive /path/to/archive.tar.gz")
            sys.exit(1)

        if not os.path.exists(archive_path):
            print(f"❌ Archive introuvable: {archive_path}")
            sys.exit(1)

        archive_size = os.path.getsize(archive_path) / (1024 * 1024 * 1024)

        # === CONFIGURATION ===
        ip = 'localhost'
        env = load_env()
        rclone_profile = args.instance

        # Limites Docker selon profil (augmentées pour grosse DB)
        docker_limits = get_docker_limits(rclone_profile)
        # Override pour test local avec grosse DB
        docker_limits['memory'] = '8g'
        docker_limits['memory_swap'] = '12g'

        print("=" * 60)
        print("🔄 TEST DELTA SYNC - INJECTION DB EXISTANTE")
        print("=" * 60)
        print(f"Archive        : {archive_path} ({archive_size:.2f} GB)")
        print(f"Profil rclone  : {rclone_profile}")
        print(f"Mode scan      : {'FORCÉ' if args.force_scan else 'INCRÉMENTAL'}")
        if args.filter:
            print(f"Filtre         : {args.filter}")
        print("=" * 60)

        # Variables pour le suivi
        plex_token = None
        stats_before = None
        can_do_sonic = False
        mount_monitor = None

        # === PHASE 1: PRÉPARATION ===
        print_phase_header(1, "PRÉPARATION")

        setup_local_test_env(TEST_DIR, MOUNT_DIR, PLEX_CONFIG)
        setup_rclone_config(ip)

        # === PHASE 2: INJECTION DB ===
        print_phase_header(2, "INJECTION DB EXISTANTE")

        success = inject_existing_db(
            ip,
            archive_path,
            str(PLEX_CONFIG),
            container='plex'
        )

        if not success:
            print("❌ Échec de l'injection")
            sys.exit(1)

        # Lire les stats AVANT de démarrer Plex
        stats_before = get_library_stats_from_db(ip, str(PLEX_CONFIG))
        print_injection_stats(stats_before)

        # === PHASE 3: MONTAGE S3 ===
        print_phase_header(3, "MONTAGE S3")

        mount_s3(
            ip,
            env['S3_BUCKET'],
            profile=rclone_profile,
            mount_point=str(MOUNT_DIR),
            cache_dir=str(CACHE_DIR),
            log_file=str(LOG_FILE)
        )

        # Vérifier que les chemins correspondent
        path_check = verify_paths_match(ip, str(PLEX_CONFIG), str(MOUNT_DIR))

        if not path_check['match']:
            print("\n⚠️  ATTENTION: Certains chemins ne correspondent pas!")
            for suggestion in path_check['suggestions']:
                print(f"   • {suggestion}")
            print("")
            print("   Continuons quand même - certaines bibliothèques fonctionneront.")

        # === PHASE 4: DÉMARRAGE PLEX ===
        print_phase_header(4, "DÉMARRAGE PLEX")

        # Claim token AVANT de démarrer le monitoring
        # (évite les deadlocks et messages parasites pendant l'input)
        plex_claim = input("\n🔑 Entrez votre PLEX_CLAIM (depuis https://www.plex.tv/claim) : ").strip()
        if not plex_claim:
            print("❌ PLEX_CLAIM requis")
            sys.exit(1)

        # Démarrer le monitoring du montage APRÈS avoir le claim
        mount_monitor = MountHealthMonitor(
            ip=ip,
            mount_point=str(MOUNT_DIR),
            rclone_remote=env['S3_BUCKET'],
            profile=rclone_profile,
            cache_dir=str(CACHE_DIR),
            log_file=str(LOG_FILE),
            check_interval=60  # Vérification toutes les minutes
        )
        mount_monitor.start()

        # Démarrer Plex avec la DB injectée
        start_plex_container(
            ip,
            plex_claim,
            version=env.get('PLEX_VERSION', 'latest'),
            memory=docker_limits['memory'],
            memory_swap=docker_limits['memory_swap'],
            cpus=docker_limits['cpus'],
            config_path=str(PLEX_CONFIG),
            media_path=str(MOUNT_DIR),
            transcode_path=str(PLEX_CONFIG / 'transcode')
        )

        wait_plex_fully_ready(ip, container='plex', timeout=300)

        # Token pour API
        plex_token = get_plex_token(ip, container='plex')
        if not plex_token:
            print("⚠️  Token Plex non disponible - certaines fonctions seront limitées")

        # Vérifier Plex Pass (requis pour Sonic)
        if plex_token and not args.quick_test:
            pass_status = verify_plex_pass_active(ip, 'plex', plex_token, timeout=60)
            can_do_sonic = pass_status.get('active', False)
            if not can_do_sonic:
                print("⚠️  Plex Pass non actif - analyse Sonic indisponible")

        # Note: Les analyses Sonic seront activées en Phase 6.3 par enable_music_analysis_only()
        # Ne PAS appeler enable_plex_analysis_via_api() ici car cela déclenche le Butler
        # et interfère avec wait_section_idle() pendant le scan

        # === PHASE 5: VÉRIFICATION BIBLIOTHÈQUES ===
        print_phase_header(5, "VÉRIFICATION BIBLIOTHÈQUES")

        # Récupérer les sections depuis l'API Plex
        section_info = {}
        api_cmd = f"curl -s 'http://localhost:32400/library/sections' -H 'X-Plex-Token: {plex_token}'"
        result = docker_exec(ip, 'plex', api_cmd, capture_output=True, check=False)

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
            print("❌ Aucune section trouvée! La DB n'a peut-être pas été chargée correctement.")
            print("\n📋 Logs Plex:")
            execute_command(ip, "docker logs plex --tail 30", check=False)
            sys.exit(1)

        # === PHASE 6: TRAITEMENT MUSIQUE (Sonic) ===
        # Déterminer si on doit traiter la section Musique
        should_process_music = (
            not args.section
            or any(section_info.get(s, {}).get('type') == 'artist' for s in args.section)
        )

        # Initialiser stats_after_scan pour le cas où la phase Music est skippée
        stats_after_scan = stats_before

        if should_process_music:
            print_phase_header(6, "TRAITEMENT MUSIQUE (Sonic)")

            # 6.1 Désactivation tâches de fond
            print("\n6.1 Désactivation des tâches de fond...")
            disable_all_background_tasks(ip, 'plex', plex_token)

            # 6.2 Scan section Musique
            print("\n6.2 Scan de la section Musique...")

            music_section_id = None
            music_section_name = None
            for name, info in section_info.items():
                if info['type'] == 'artist':
                    music_section_id = info['id']
                    music_section_name = name
                    break

            if music_section_id:
                print(f"   Section Musique trouvée: [{music_section_id}] {music_section_name}")

                # Scan avec ou sans filtre
                if args.filter:
                    # Scan filtré via CLI
                    local_mount_path = MOUNT_DIR / "Music"
                    possible_paths = [
                        MOUNT_DIR / "Music",
                        MOUNT_DIR / "Music-Various-Artists",
                        MOUNT_DIR / music_section_name,
                    ]

                    for p in possible_paths:
                        if p.exists():
                            local_mount_path = p
                            break

                    if local_mount_path.exists():
                        filter_prefixes = [args.filter.upper()]
                        print(f"   📂 Scan avec filtre {filter_prefixes} dans {local_mount_path}")

                        scan_section_incrementally(
                            ip,
                            'plex',
                            plex_token,
                            music_section_id,
                            'artist',
                            f"/Media/{local_mount_path.name}",
                            str(local_mount_path),
                            filter_prefixes=filter_prefixes
                        )
                    else:
                        print(f"   ⚠️  Dossier local non trouvé, fallback sur API refresh")
                        trigger_section_scan(ip, 'plex', plex_token, music_section_id, force=args.force_scan)
                else:
                    # Scan global via API
                    trigger_section_scan(ip, 'plex', plex_token, music_section_id, force=args.force_scan)

                # Attendre que le scan soit terminé
                wait_section_idle(ip, 'plex', plex_token, music_section_id,
                                  section_type='artist', phase='scan', config_path=str(PLEX_CONFIG))

                # Analyse du delta de scan
                print("\n📊 Analyse du delta de scan:")
                stats_after_scan = get_library_stats_from_db(ip, str(PLEX_CONFIG))
                delta_tracks = stats_after_scan['tracks'] - stats_before['tracks']
                delta_artists = stats_after_scan['artists'] - stats_before['artists']
                print(f"   Nouvelles pistes   : +{delta_tracks}")
                print(f"   Nouveaux artistes  : +{delta_artists}")
            else:
                print("   ⚠️  Aucune section Musique trouvée")
                stats_after_scan = stats_before

            # 6.3 Analyse Sonic (sauf si --quick-test)
            if not args.quick_test:
                print("\n6.3 Analyse Sonic...")

                if not can_do_sonic:
                    print("   ⏭️  Analyse Sonic IGNORÉE (Plex Pass non actif)")
                elif not music_section_id:
                    print("   ⏭️  Analyse Sonic IGNORÉE (pas de section Musique)")
                else:
                    # Activer uniquement les analyses musicales
                    enable_music_analysis_only(ip, 'plex', plex_token)

                    # 6.3a Refresh Metadata si demandé (images, paroles, matching)
                    # Important: ceci peut prendre plusieurs heures sur une grosse bibliothèque
                    if args.force_refresh:
                        print("\n6.3a Refresh Metadata (images, paroles, matching)...")
                        print("   ⚠️  Cette phase peut prendre plusieurs heures sur une grosse bibliothèque")
                        trigger_section_scan(ip, 'plex', plex_token, music_section_id, force=True)

                        # Utiliser le profil metadata_refresh avec timeout étendu (4h)
                        metadata_params = get_monitoring_params('metadata_refresh')
                        print(f"   ⏳ Attente fin du refresh (timeout: {metadata_params['absolute_timeout']//3600}h)...")
                        wait_section_idle(ip, 'plex', plex_token, music_section_id,
                                          section_type='artist', phase='scan', config_path=str(PLEX_CONFIG),
                                          timeout=metadata_params['absolute_timeout'],
                                          check_interval=metadata_params['check_interval'])
                        print("   ✅ Refresh metadata terminé.")

                        # 6.3b Stabilisation avant Sonic
                        # Attendre que toutes les tâches de fond (téléchargements, etc.) soient vraiment finies
                        print("\n6.3b Stabilisation avant Sonic...")
                        wait_plex_stabilized(ip, 'plex', plex_token,
                                             cooldown_checks=3,
                                             check_interval=60,
                                             cpu_threshold=20.0,
                                             timeout=1800)

                    # 6.3c Lancer Sonic (sans --force, le refresh a été fait séparément)
                    print("\n6.3c Lancement analyse Sonic...")
                    trigger_sonic_analysis(ip, music_section_id, 'plex')

                    # Monitoring avec profil adapté (centralisé dans MONITORING_PROFILES)
                    monitoring_profile = 'cloud_intensive' if args.monitoring == 'cloud' else 'local_delta'
                    monitoring_params = get_monitoring_params(monitoring_profile)

                    # Utiliser le monitor global démarré en phase 3
                    sonic_result = wait_sonic_complete(
                        ip,
                        str(PLEX_CONFIG),
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
                print("\n6.3 Analyse Sonic SKIPPÉE (--quick-test)")

            # 6.4 Export intermédiaire (si monitoring cloud)
            if args.monitoring == 'cloud':
                print("\n6.4 Export intermédiaire...")
                export_intermediate(ip, 'plex', str(PLEX_CONFIG), '.', label="post_sonic")

            if args.collect_logs or args.save_output:
                terminal_log = tee_logger.log_path if tee_logger else None
                # keep_terminal_log=True car TeeLogger écrit encore (snapshot intermédiaire)
                collect_plex_logs(ip, 'plex', prefix="phase6", terminal_log=terminal_log,
                                  rclone_log=str(LOG_FILE), timestamp=RUN_TIMESTAMP, keep_terminal_log=True)
        else:
            print_phase_header(6, "TRAITEMENT MUSIQUE (Sonic) - SKIPPÉE")
            print(f"⏭️  Aucune section musicale dans le filtre --section {args.section}")

        # === PHASE 7: VALIDATION AUTRES SECTIONS ===
        # Déterminer les sections à traiter (autres que Music)
        other_sections = [(name, info) for name, info in section_info.items()
                          if info['type'] != 'artist']

        if args.section:
            other_sections = [(name, info) for name, info in other_sections
                              if name in args.section]

        if other_sections:
            print_phase_header(7, "VALIDATION AUTRES SECTIONS")

            # 7.1 Réactivation analyses
            print("\n7.1 Réactivation des analyses (Photos/Vidéos)...")
            enable_all_analysis(ip, 'plex', plex_token)

            # 7.2 Scan sections restantes (SÉQUENTIEL)
            print("\n7.2 Scan et analyse des sections restantes (séquentiel)...")

            for section_name, info in other_sections:
                # Étape 1: Scan de la section
                print(f"\n   🔍 Scan de '{section_name}' (ID: {info['id']}, type: {info['type']})")
                trigger_section_scan(ip, 'plex', plex_token, info['id'], force=False)

                # Attendre que le scan soit terminé
                # section_type permet un timeout adaptatif (4h pour photos)
                wait_section_idle(ip, 'plex', plex_token, info['id'],
                                  section_type=info['type'], phase='scan',
                                  config_path=str(PLEX_CONFIG), timeout=3600)

                # Étape 2: Analyse de la section (thumbnails, chapitres, intros...)
                print(f"\n   🔬 Analyse de '{section_name}' (ID: {info['id']})")
                trigger_section_analyze(ip, 'plex', plex_token, info['id'])

                # Attendre que l'analyse soit terminée
                wait_section_idle(ip, 'plex', plex_token, info['id'],
                                  section_type=info['type'], phase='analyze',
                                  config_path=str(PLEX_CONFIG), timeout=3600)

            print("\n✅ Scan et analyse autres sections terminés")

            # 7.3 Affichage récapitulatif
            print("\n7.3 Récapitulatif...")
            final_stats = get_library_stats_from_db(ip, str(PLEX_CONFIG))

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
            print_phase_header(7, "VALIDATION AUTRES SECTIONS - SKIPPÉE")
            print("⏭️  Aucune section à traiter")

        # === PHASE 8: EXPORT FINAL ===
        print_phase_header(8, "EXPORT FINAL")

        # 8.1 Collecte logs Plex AVANT arrêt (le conteneur doit tourner)
        # Note: Le terminal log sera ajouté dans finally après tee_logger.stop()
        if args.collect_logs or args.save_output:
            print("\n8.1 Collecte des logs Plex (conteneur actif)...")
            plex_logs_archive = collect_plex_logs(ip, 'plex', prefix="final",
                                                   rclone_log=str(LOG_FILE), timestamp=RUN_TIMESTAMP)

        # 8.2 Arrêt Plex
        print("\n8.2 Arrêt de Plex...")
        stop_plex(ip, container='plex')
        time.sleep(3)

        # 8.3 Export complet (utilise RUN_TIMESTAMP pour cohérence)
        print("\n8.3 Export complet...")
        archive_name = f'plex_delta_sync_{RUN_TIMESTAMP}.tar.gz'

        archive_path_out = export_metadata(
            ip,
            container='plex',
            archive_name=archive_name,
            config_path=str(PLEX_CONFIG)
        )

        # 8.4 Résumé final
        print("\n8.4 Résumé final...")
        print("\n" + "=" * 60)
        print("✅ DELTA SYNC TERMINÉ")
        print("=" * 60)
        print(f"📦 Archive principale : {archive_path_out}")

        final_stats = get_library_stats_from_db(ip, str(PLEX_CONFIG))
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

        print("\n🔄 Pour appliquer sur ZimaBoard:")
        print(f"   scp {archive_name} jbo@zimaboard:/tmp/")
        print(f"   # Puis sur ZimaBoard: ./import_db.sh /tmp/{archive_name}")

    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrompu par l'utilisateur")
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

        # Vérifier OOM
        oom_cmd = "docker inspect plex --format '{{.State.OOMKilled}}' 2>/dev/null || echo 'N/A'"
        oom_result = execute_command(ip, oom_cmd, capture_output=True, check=False)
        is_oom = oom_result.stdout.strip() == 'true'

        if is_oom:
            print("🚨 ALERTE: Conteneur tué par manque de mémoire (OOM)")
        else:
            print("✅ Pas de kill mémoire (OOM)")

        # === DIAGNOSTIC SONIC ===
        print("\n🎹 DIAGNOSTIC SONIC:")
        db_path = "/config/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

        # 1. Comparer les méthodes de comptage Sonic
        sonic_count_cmd = f'''docker exec plex sqlite3 "{db_path}" "
            SELECT
                (SELECT COUNT(*) FROM media_item_settings WHERE loudness != 0) as loudness_count,
                (SELECT COUNT(*) FROM media_parts WHERE extra_data LIKE '%hasSonicAnalysis%1%') as sonic_flag_count
        " 2>/dev/null || echo "DB inaccessible"'''
        sonic_result = execute_command(ip, sonic_count_cmd, capture_output=True, check=False)
        print(f"   Comptage Sonic (loudness vs extra_data):")
        print(f"   {sonic_result.stdout.strip()}")

        # 2. Logs Sonic spécifiques
        print("\n   Logs Sonic (dernières entrées):")
        sonic_logs_cmd = '''docker exec plex sh -c "grep -i 'sonic\\|fingerprint\\|audio.analysis\\|chromaprint' '/config/Library/Application Support/Plex Media Server/Logs/Plex Media Server.log' 2>/dev/null | tail -10 || echo 'Aucun log Sonic trouvé'"'''
        execute_command(ip, sonic_logs_cmd, check=False)

        # 3. Vérifier si le moteur Sonic/analyse est actif
        print("\n   Processus d'analyse actifs:")
        process_cmd = "docker exec plex ps aux 2>/dev/null | grep -iE 'sonic|scanner|transcode' | grep -v grep || echo 'Aucun processus analyse'"
        execute_command(ip, process_cmd, check=False)

        # 4. Vérifier l'état des préférences Sonic
        print("\n   Préférences analyse Plex:")
        prefs_cmd = f'''docker exec plex sqlite3 "{db_path}" "
            SELECT name, value FROM preferences
            WHERE name LIKE '%sonic%' OR name LIKE '%loudness%' OR name LIKE '%musicAnalysis%'
            LIMIT 10
        " 2>/dev/null || echo "Préférences inaccessibles"'''
        execute_command(ip, prefs_cmd, check=False)

        # Dernières logs
        print("\n📋 Dernières logs Docker:")
        execute_command(ip, "docker logs plex --tail 20 2>&1 || true", check=False)

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
                        # Supprimer l'archive Plex seule (remplacée par la combinée)
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
            else:
                print(f"⚠️  Terminal log non trouvé: {terminal_log_path}")

        # Nettoyage
        if not args.keep:
            cleanup_local_test_env(TEST_DIR, MOUNT_DIR)
        else:
            print("\n💾 Conteneur conservé (--keep)")
            print(f"   Config Plex : {PLEX_CONFIG}")
            print(f"   Montage S3  : {MOUNT_DIR}")
            print(f"   Pour cleanup: docker stop plex && docker rm plex && sudo rm -rf {TEST_DIR}")


if __name__ == "__main__":
    main()
