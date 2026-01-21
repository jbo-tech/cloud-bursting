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
import sys
import time
from datetime import datetime

# Imports modules common
from common.config import load_env, load_libraries, get_docker_limits
from common.executor import execute_command, download_file_from_remote, read_state_file
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
    enable_plex_analysis_via_api,
    collect_plex_logs
)
from common.plex_scan import (
    monitor_discovery_phase,
    trigger_sonic_analysis,
    get_monitoring_params,
    export_metadata
)
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

    args = parser.parse_args()

    # === TERMINAL LOGGING ===
    tee_logger = None
    if args.save_output:
        from common.tee_logger import TeeLogger
        tee_logger = TeeLogger()
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

        # Claim token
        plex_claim = input("\n🔑 Entrez votre PLEX_CLAIM (depuis https://www.plex.tv/claim) : ").strip()
        if not plex_claim:
            print("❌ PLEX_CLAIM requis")
            sys.exit(1)

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
        print("\n" + "=" * 60)
        print(f"PHASE 5: CONFIGURATION BIBLIOTHÈQUES ({len(libraries)})")
        print("=" * 60)

        for lib in libraries:
            add_library(instance_ip, 'plex', lib)
            time.sleep(2)

        # === PHASE 6: SCAN ===
        if not args.skip_scan:
            print("\n" + "=" * 60)
            print("PHASE 6: SCAN (DÉCOUVERTE)")
            print("=" * 60)

            trigger_scan_all(instance_ip, container='plex', plex_token=plex_token, force=True)
            monitor_discovery_phase(
                instance_ip,
                container='plex',
                plex_token=plex_token,
                check_interval=30,
                max_idle=10
            )

            # === PHASE 7: ANALYSE ===
            print("\n" + "=" * 60)
            print("PHASE 7: ANALYSE")
            print("=" * 60)

            if args.skip_analysis:
                print("⏭️  Analyse ignorée (--skip-analysis)")
            elif not can_do_sonic:
                print("⏭️  Analyse Sonic ignorée (Plex Pass non actif)")
            else:
                if plex_token:
                    enable_plex_analysis_via_api(instance_ip, 'plex', plex_token)

                trigger_deep_analysis(instance_ip, container='plex', plex_token=plex_token)

                # Sélection du profil de monitoring selon la puissance de l'instance
                monitoring_profile = 'cloud_intensive' if args.instance in ['power', 'superpower'] else 'cloud_standard'
                print(f"   📋 Profil monitoring: {monitoring_profile}")

                monitor_analysis_phase(
                    instance_ip,
                    container='plex',
                    plex_token=plex_token,
                    **get_monitoring_params(monitoring_profile)
                )
        else:
            print("\n⏭️  Scan ignoré (--skip-scan)")

        # === PHASE 8: EXPORT ===
        print("\n" + "=" * 60)
        print("PHASE 8: EXPORT MÉTADONNÉES")
        print("=" * 60)

        # Arrêter Plex
        execute_command(instance_ip, "docker stop -t 60 plex || docker kill plex", check=False)
        time.sleep(5)

        # Export
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f'plex_metadata_{timestamp}.tar.gz'

        archive_remote = export_metadata(
            instance_ip,
            container='plex',
            archive_name=archive_name,
            config_path='/opt/plex_data/config'
        )

        # Télécharger
        local_archive = f'./{archive_name}'
        download_file_from_remote(instance_ip, archive_remote, local_archive)

        # === SUCCÈS ===
        print("\n" + "=" * 60)
        print("✅ SCAN CLOUD TERMINÉ AVEC SUCCÈS")
        print("=" * 60)
        print(f"📦 Archive: {local_archive}")
        print("")
        print("🔄 Pour appliquer sur ZimaBoard:")
        print(f"   scp {archive_name} jbo@zimaboard:/tmp/")
        print(f"   ssh jbo@zimaboard './import_db.sh /tmp/{archive_name}'")

    except KeyboardInterrupt:
        print("\n\n⚠️  Workflow interrompu par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
    finally:
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
                collect_plex_logs(instance_ip, 'plex', prefix="final", terminal_log=terminal_log)

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
