#!/usr/bin/env python3
"""
automate_scan.py - Scan Plex complet dans le cloud (Scaleway)

Ce script orchestre un workflow de cloud bursting pour Plex:
1. Crée une instance Scaleway éphémère (GP1-S/M pour puissance CPU)
2. Monte le bucket S3 contenant les médias via rclone
3. Démarre un conteneur Plex frais et scanne toute la bibliothèque
4. Lance l'analyse Sonic (si Plex Pass actif)
5. Exporte et rapatrie les métadonnées générées
6. Détruit l'instance cloud pour stopper la facturation

Objectif: Déléguer le travail intensif d'indexation à une instance puissante,
puis utiliser le ZimaBoard local uniquement pour le streaming.

Prérequis:
- scw CLI installé et configuré (scw init)
- Fichier .env avec S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, PLEX_VERSION
- Fichier plex_libraries.json avec la liste des bibliothèques
- Fichier rclone.conf avec la config S3

Usage:
    # Scan complet en production (profil power recommandé)
    python automate_scan.py --instance power

    # Test avec 2 bibliothèques sur instance économique
    python automate_scan.py --instance standard --test 2

    # Scan sans analyse Sonic (gain de temps)
    python automate_scan.py --instance power --skip-analysis

    # Garder l'instance après scan (debug)
    python automate_scan.py --instance power --keep
"""

# === IMPORTS ===
import argparse
import re
import sys
import time
from datetime import datetime

# Imports modules common
from common.config import load_env, load_libraries, get_docker_limits, print_phase_header
from common.executor import execute_command, download_file_from_remote, read_state_file, docker_exec
from common.plex_setup import (
    apply_system_optimizations,
    cleanup_plex_data,
    setup_rclone_config,
    mount_s3,
    start_plex_container,
    wait_plex_fully_ready,
    get_plex_token,
    add_library,
    verify_plex_pass_active,
    collect_plex_logs,
    wait_plex_ready_for_libraries,
    disable_all_background_tasks,
    enable_music_analysis_only,
    enable_all_analysis
)
from common.plex_scan import (
    trigger_sonic_analysis,
    get_monitoring_params,
    export_metadata,
    scan_section_incrementally,
    wait_section_idle,
    wait_plex_stabilized,
    wait_sonic_complete,
    trigger_section_scan,
    trigger_section_analyze,
    export_intermediate
)
from common.mount_monitor import MountHealthMonitor
from common.scaleway import (
    INSTANCE_PROFILES,
    INSTANCE_ID_FILE,
    INSTANCE_IP_FILE,
    create_instance,
    destroy_instance,
    wait_ssh_ready,
    wait_cloud_init,
    test_mega_bandwidth
)


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Point d'entrée principal du script."""
    # === ARGUMENTS CLI ===
    parser = argparse.ArgumentParser(
        description='Scan Plex complet dans le cloud (Scaleway)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Profils d'instance:
  lite       : DEV1-S  (2 vCPU, 2GB)  - Tests uniquement
  standard   : DEV1-M  (3 vCPU, 4GB)  - Petites bibliothèques
  power      : GP1-S   (8 vCPU, 16GB) - Bibliothèques moyennes
  superpower : GP1-M   (8 vCPU, 32GB) - Grosses bibliothèques + Sonic
        """
    )
    parser.add_argument('--instance', choices=list(INSTANCE_PROFILES.keys()),
                        default='power', help='Profil d\'instance (default: power)')
    parser.add_argument('--test', type=int, metavar='N',
                        help='Mode test: limiter à N bibliothèques')
    parser.add_argument('--skip-scan', action='store_true',
                        help='Sauter le scan (setup uniquement)')
    parser.add_argument('--skip-analysis', action='store_true',
                        help='Sauter l\'analyse Sonic')
    parser.add_argument('--keep', action='store_true',
                        help='Garder l\'instance après le scan')
    parser.add_argument('--save-output', action='store_true',
                        help='Sauvegarder l\'output terminal dans logs/')
    parser.add_argument('--collect-logs', action='store_true',
                        help='Récupérer les logs Plex en fin de run')
    parser.add_argument('--test-mega', action='store_true',
                        help='Tester la bande passante MEGA avant de continuer')
    parser.add_argument('--filter', type=str, metavar='PREFIX',
                        help='Filtrer le scan music par préfixe (ex: --filter Q)')
    parser.add_argument('--force-refresh', action='store_true',
                        help='Refresh Metadata avant Sonic (images, paroles, matching)')
    parser.add_argument('--section', type=str, action='append', metavar='SECTION',
                        help='Traiter uniquement ces sections (répétable, ex: --section Movies)')
    parser.add_argument('--quick-test', action='store_true',
                        help='Mode test rapide : skip Sonic, scan validation uniquement')

    args = parser.parse_args()

    # === VARIABLES GLOBALES ===
    tee_logger = None
    RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

    # === TERMINAL LOGGING ===
    if args.save_output:
        from common.tee_logger import TeeLogger
        tee_logger = TeeLogger(timestamp=RUN_TIMESTAMP)
        tee_logger.start()
        print(f"📝 Output terminal sauvegardé dans: {tee_logger.log_path}")

    # === CONFIGURATION ===
    env = load_env()
    libraries = load_libraries(limit=args.test)
    profile = args.instance
    docker_limits = get_docker_limits(profile)

    print("=" * 60)
    print("☁️  CLOUD BURSTING - PLEX SCAN COMPLET")
    print("=" * 60)
    print(f"Profil instance : {profile} ({INSTANCE_PROFILES[profile]['description']})")
    print(f"Profil rclone   : {profile}")
    print(f"Bibliothèques   : {len(libraries)}")
    if args.test:
        print(f"Mode test       : {args.test} bibliothèque(s)")
    print("=" * 60)

    instance_ip = None
    mount_monitor = None

    try:
        # === PHASE 1: CRÉATION INSTANCE ===
        instance_ip = create_instance(env, profile)

        # === PHASE 2: ATTENTE INITIALISATION ===
        wait_ssh_ready(instance_ip)
        wait_cloud_init(instance_ip)

        # === PHASE 3: CONFIGURATION ===
        print("\n" + "=" * 60)
        print("PHASE 3: CONFIGURATION ENVIRONNEMENT")
        print("=" * 60)

        apply_system_optimizations(instance_ip)
        cleanup_plex_data(instance_ip)
        setup_rclone_config(instance_ip)

        # Test MEGA si demandé
        if args.test_mega:
            mega_result = test_mega_bandwidth(instance_ip)
            if not mega_result['success']:
                print("\n⚠️  Test MEGA échoué!")
                if input("   Continuer quand même? (o/N) ").lower() != 'o':
                    sys.exit(1)

        # Montage S3
        mount_s3(
            instance_ip,
            env['S3_BUCKET'],
            profile=profile,
            mount_point='/mnt/s3-media',
            cache_dir='/tmp/rclone-cache',
            log_file='/var/log/rclone.log'
        )

        # === PHASE 4: DÉMARRAGE PLEX ===
        print("\n" + "=" * 60)
        print("PHASE 4: DÉMARRAGE PLEX")
        print("=" * 60)

        # Claim token AVANT de démarrer le monitoring
        # (évite les deadlocks et messages parasites pendant l'input)
        plex_claim = input("\n🔑 Entrez votre PLEX_CLAIM (depuis https://www.plex.tv/claim) : ").strip()
        if not plex_claim:
            print("❌ PLEX_CLAIM requis")
            sys.exit(1)

        # Démarrer le monitoring du montage APRÈS avoir le claim
        mount_monitor = MountHealthMonitor(
            ip=instance_ip,
            mount_point='/mnt/s3-media',
            rclone_remote=env['S3_BUCKET'],
            profile=profile,
            cache_dir='/tmp/rclone-cache',
            log_file='/var/log/rclone.log',
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

        wait_plex_fully_ready(instance_ip, container='plex', timeout=300)

        # Token API
        plex_token = get_plex_token(instance_ip, container='plex')
        if not plex_token:
            print("⚠️  Token Plex non disponible")

        # Vérifier Plex Pass
        can_do_sonic = False
        if plex_token and not args.skip_analysis:
            pass_status = verify_plex_pass_active(instance_ip, 'plex', plex_token, timeout=60)
            can_do_sonic = pass_status.get('active', False)

        # === PHASE 5: CONFIGURATION BIBLIOTHÈQUES ===
        print_phase_header(5, f"CONFIGURATION BIBLIOTHÈQUES ({len(libraries)})")

        if not wait_plex_ready_for_libraries(instance_ip, 'plex', plex_token):
            print("⚠️ Plex pas prêt, on continue quand même...")

        success_count = 0
        for lib in libraries:
            if add_library(instance_ip, 'plex', lib, plex_token):
                success_count += 1
            time.sleep(2)

        print(f"\n📊 Résumé: {success_count}/{len(libraries)} bibliothèques créées")

        if success_count > 0:
            print("⏳ Pause de 10s pour finalisation des bibliothèques...")
            time.sleep(10)

        # === PHASE 6: TRAITEMENT MUSIQUE (Sonic) ===
        if not args.skip_scan:
            # Récupérer les sections réelles de Plex (avant filtrage --section)
            print("\n📚 Identification des sections...")
            section_info = {}
            api_cmd = f"curl -s 'http://localhost:32400/library/sections' -H 'X-Plex-Token: {plex_token}'"
            result = docker_exec(instance_ip, 'plex', api_cmd, capture_output=True, check=False)

            for match in re.finditer(r'key="(\d+)".*?type="([^"]+)".*?title="([^"]+)"', result.stdout):
                s_id, s_type, s_title = match.group(1), match.group(2), match.group(3)
                section_info[s_title] = {"id": s_id, "type": s_type}

            print(f"   📚 Sections trouvées: {len(section_info)}")
            for name, info in section_info.items():
                print(f"      [{info['id']}] {name} ({info['type']})")

            # Validation des sections demandées via --section
            if args.section:
                requested = set(args.section)
                available = set(section_info.keys())
                unknown = requested - available
                if unknown:
                    print(f"\n   ⚠️  Sections ignorées: {unknown}")
                    print(f"      Disponibles: {list(available)}")

            # Déterminer si on doit traiter la section Musique
            should_process_music = (
                not args.section
                or any(section_info.get(s, {}).get('type') == 'artist' for s in args.section)
            )

            if should_process_music:
                print_phase_header(6, "TRAITEMENT MUSIQUE (Sonic)")

                # 6.1 Désactivation tâches de fond
                print("\n6.1 Désactivation des tâches de fond...")
                disable_all_background_tasks(instance_ip, 'plex', plex_token)

                # 6.2 Scanner la section Musique
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
                        filter_prefixes = [args.filter.upper()]
                        print(f"   📂 Scan avec filtre {filter_prefixes}")

                        # Trouver le chemin container correspondant
                        container_path = None
                        for lib in libraries:
                            if lib['title'] == music_section_name:
                                container_path = lib['paths'][0]
                                break

                        if container_path:
                            # En cloud, le mount local est /mnt/s3-media
                            local_mount_path = container_path.replace('/Media', '/mnt/s3-media')
                            scan_section_incrementally(
                                instance_ip, 'plex', plex_token,
                                music_section_id, 'artist',
                                container_path, local_mount_path,
                                filter_prefixes=filter_prefixes
                            )
                        else:
                            print(f"   ⚠️  Chemin container non trouvé, fallback sur API refresh")
                            trigger_section_scan(instance_ip, 'plex', plex_token, music_section_id, force=False)
                    else:
                        # Scan global via API
                        trigger_section_scan(instance_ip, 'plex', plex_token, music_section_id, force=False)

                    # Attendre fin du scan
                    wait_section_idle(instance_ip, 'plex', plex_token, music_section_id,
                                      section_type='artist', phase='scan', config_path='/opt/plex_data/config')
                    print("   ✅ Scan Musique terminé")
                else:
                    print("   ⚠️  Aucune section Musique trouvée")

                # 6.3 Analyse Sonic (sauf si --quick-test ou --skip-analysis)
                if args.quick_test or args.skip_analysis:
                    print("\n6.3 Analyse Sonic SKIPPÉE (--quick-test ou --skip-analysis)")
                elif not can_do_sonic:
                    print("\n6.3 Analyse Sonic IGNORÉE (Plex Pass non actif)")
                elif not music_section_id:
                    print("\n6.3 Analyse Sonic IGNORÉE (pas de section Musique)")
                else:
                    print("\n6.3 Analyse Sonic...")
                    enable_music_analysis_only(instance_ip, 'plex', plex_token)

                    # 6.3a Refresh Metadata si demandé (images, paroles, matching)
                    if args.force_refresh:
                        print("\n6.3a Refresh Metadata (images, paroles, matching)...")
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

                        # 6.3b Stabilisation avant Sonic
                        print("\n6.3b Stabilisation avant Sonic...")
                        wait_plex_stabilized(instance_ip, 'plex', plex_token,
                                             cooldown_checks=3,
                                             check_interval=60,
                                             cpu_threshold=20.0,
                                             timeout=1800)

                    # 6.3c Lancer Sonic (sans --force, le refresh a été fait séparément)
                    print("\n6.3c Lancement analyse Sonic...")
                    trigger_sonic_analysis(instance_ip, music_section_id, 'plex')

                    # Monitoring avec profil cloud (24h timeout)
                    monitoring_profile = 'cloud_intensive' if args.instance in ['power', 'superpower'] else 'cloud_standard'
                    monitoring_params = get_monitoring_params(monitoring_profile)

                    sonic_result = wait_sonic_complete(
                        instance_ip,
                        '/opt/plex_data/config',
                        music_section_id,
                        container='plex',
                        timeout=monitoring_params['absolute_timeout'],
                        check_interval=monitoring_params['check_interval']
                    )

                    print(f"\n   📊 Résultat Sonic:")
                    print(f"      Initial  : {sonic_result['initial_count']} pistes")
                    print(f"      Final    : {sonic_result['final_count']} pistes")
                    print(f"      Delta    : +{sonic_result['delta']}")
                    print(f"      Durée    : {sonic_result['duration_minutes']} min")
                    print(f"      Raison   : {sonic_result['reason']}")

                # 6.4 Export intermédiaire
                print("\n6.4 Export intermédiaire...")
                export_intermediate(instance_ip, 'plex', '/opt/plex_data/config', '.', label="post_sonic")
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
                enable_all_analysis(instance_ip, 'plex', plex_token)

                # 7.2 Scan séquentiel des autres sections
                print("\n7.2 Scan des sections restantes (séquentiel)...")

                for section_name, info in other_sections:
                    # Étape 1: Scan
                    print(f"\n   🔍 Scan '{section_name}' (ID: {info['id']}, type: {info['type']})")
                    trigger_section_scan(instance_ip, 'plex', plex_token, info['id'], force=False)
                    wait_section_idle(instance_ip, 'plex', plex_token, info['id'],
                                      section_type=info['type'], phase='scan',
                                      config_path='/opt/plex_data/config', timeout=3600)

                    # Étape 2: Analyse (thumbnails, chapitres, etc.)
                    if not args.skip_analysis and not args.quick_test:
                        print(f"   🔬 Analyse '{section_name}'")
                        trigger_section_analyze(instance_ip, 'plex', plex_token, info['id'])
                        wait_section_idle(instance_ip, 'plex', plex_token, info['id'],
                                          section_type=info['type'], phase='analyze',
                                          config_path='/opt/plex_data/config', timeout=3600)

                print("\n   ✅ Autres sections terminées")
            else:
                print_phase_header(7, "VALIDATION AUTRES SECTIONS - SKIPPÉE")
                print("⏭️  Aucune section à traiter")
        else:
            print("\n⏭️  Scan ignoré (--skip-scan)")

        # === PHASE 8: EXPORT ===
        print_phase_header(8, "EXPORT MÉTADONNÉES")

        # 8.1 Collecte logs Plex AVANT arrêt
        if args.collect_logs or args.save_output:
            print("\n8.1 Collecte des logs Plex (conteneur actif)...")
            collect_plex_logs(instance_ip, 'plex', prefix="final",
                              rclone_log='/var/log/rclone.log', timestamp=RUN_TIMESTAMP)

        # 8.2 Arrêter Plex
        print("\n8.2 Arrêt de Plex...")
        execute_command(instance_ip, "docker stop -t 60 plex || docker kill plex", check=False)
        time.sleep(5)

        # 8.3 Export complet
        print("\n8.3 Export complet...")
        archive_name = f'plex_metadata_{RUN_TIMESTAMP}.tar.gz'

        archive_remote = export_metadata(
            instance_ip,
            container='plex',
            archive_name=archive_name,
            config_path='/opt/plex_data/config'
        )

        # 8.4 Télécharger l'archive
        print("\n8.4 Téléchargement de l'archive...")
        local_archive = f'./{archive_name}'
        download_file_from_remote(instance_ip, archive_remote, local_archive)

        # === SUCCÈS ===
        print("\n" + "=" * 60)
        print("✅ SCAN CLOUD TERMINÉ AVEC SUCCÈS")
        print("=" * 60)
        print(f"📦 Archive: {local_archive}")
        print("")
        print("🔄 Pour appliquer sur le serveur Plex local:")
        print(f"   ./update_to_distant_plex.sh {archive_name}")

    except KeyboardInterrupt:
        print("\n\n⚠️  Workflow interrompu par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Arrêter le monitoring du montage
        if mount_monitor:
            mount_monitor.stop()

        # === DIAGNOSTIC POST-MORTEM ===
        if instance_ip:
            print("\n" + "=" * 60)
            print("🔍 DIAGNOSTIC POST-MORTEM")
            print("=" * 60)

            # Vérifier si le conteneur a été tué par manque de RAM (OOM Killer)
            oom_cmd = "docker inspect plex --format '{{.State.OOMKilled}}' 2>/dev/null || echo 'N/A'"
            oom_result = execute_command(instance_ip, oom_cmd, capture_output=True, check=False)
            is_oom = oom_result.stdout.strip() == 'true'

            if is_oom:
                print("🚨 ALERTE: Conteneur tué par manque de mémoire (OOM)")
                print("   👉 Solution: Augmentez la RAM allouée ou passez à un profil d'instance supérieur")
            else:
                print("✅ Pas de kill mémoire (OOM)")

            # Afficher les dernières logs système du conteneur
            print("\n📋 Dernières logs Docker:")
            execute_command(instance_ip, "docker logs plex --tail 50 2>&1 || true", check=False)

            # Collecter les logs si demandé
            if args.collect_logs or args.save_output:
                terminal_log = tee_logger.log_path if tee_logger else None
                collect_plex_logs(instance_ip, 'plex', prefix="final", terminal_log=terminal_log,
                                  rclone_log='/var/log/rclone.log')

        # Arrêter le TeeLogger
        if tee_logger:
            tee_logger.stop()
            print(f"✅ Log terminal: {tee_logger.log_path}")

        # Nettoyage ou conservation de l'instance
        if not args.keep:
            destroy_instance()
        else:
            # Récupérer l'IP si pas encore chargée
            if not instance_ip:
                instance_ip = read_state_file(INSTANCE_IP_FILE)

            print(f"\n💾 Instance conservée (--keep)")
            if instance_ip:
                print(f"   SSH: ssh root@{instance_ip}")
            print(f"   Destruction manuelle: scw instance server delete $(cat {INSTANCE_ID_FILE}) with-ip=true with-volumes=local")

        print("\n👋 Terminé")


if __name__ == "__main__":
    main()
