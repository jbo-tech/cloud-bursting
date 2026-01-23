#!/usr/bin/env python3
"""
test_scan_local.py - Test local du workflow de scan Plex complet

Ce script réplique le workflow d'automate_scan.py en environnement local:
1. Prépare un environnement de test isolé (./tmp/)
2. Monte le bucket S3 via rclone en local
3. Démarre un conteneur Plex Docker frais
4. Scanne toutes les bibliothèques configurées depuis zéro
5. Lance l'analyse Sonic (si Plex Pass actif)
6. Exporte les métadonnées générées

Objectif: Tester et debugger le workflow complet sans créer d'instance cloud.
Utilise EXACTEMENT le même code que automate_scan.py (modules common/).

Prérequis:
- Docker installé et démarré (pour conteneur Plex)
- rclone installé et configuré (fichier rclone.conf)
- Fichier .env avec S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, PLEX_VERSION
- Fichier plex_libraries.json avec la liste des bibliothèques
- FUSE configuré avec user_allow_other (/etc/fuse.conf)

Usage:
    # Test complet avec profil lite (économique en local)
    python test_scan_local.py --instance lite

    # Test limité à 2 bibliothèques
    python test_scan_local.py --instance lite --test 2

    # Test avec filtre sur bibliothèque musicale (artistes commençant par Q)
    python test_scan_local.py --instance lite --filter Q

    # Skip analyse Sonic pour accélérer les tests
    python test_scan_local.py --instance lite --skip-analysis

    # Garder le conteneur après test (debug)
    python test_scan_local.py --instance lite --keep
"""

# === IMPORTS ===
import argparse
import os
import re
import time
from datetime import datetime
from pathlib import Path

# Imports modules common
from common.config import load_env, load_libraries, get_docker_limits, print_phase_header
from common.executor import execute_command, download_file_from_remote, docker_exec
from common.local import setup_local_test_env, cleanup_local_test_env
from common.plex_setup import (
    apply_system_optimizations,
    setup_rclone_config,
    mount_s3,
    start_plex_container,
    wait_plex_fully_ready,
    get_plex_token,
    add_library,
    stop_plex,
    wait_plex_ready_for_libraries,
    enable_plex_analysis_via_api,
    verify_plex_pass_active,
    collect_plex_logs,
    disable_all_background_tasks,
    enable_music_analysis_only,
    enable_all_analysis
)
from common.plex_scan import (
    trigger_sonic_analysis,
    get_monitoring_params,
    export_metadata,
    debug_library_creation,
    scan_section_incrementally,
    wait_section_idle,
    wait_plex_stabilized,
    trigger_section_scan,
    trigger_section_analyze,
    wait_sonic_complete,
    export_intermediate
)

# === CONFIGURATION ===
# Dossiers de travail pour environnement de test local
TEST_DIR = Path(__file__).parent / "tmp"
MOUNT_DIR = TEST_DIR / "s3-media"
PLEX_CONFIG = TEST_DIR / "plex-config"
CACHE_DIR = TEST_DIR / 'rclone-cache'
LOG_FILE = TEST_DIR / 'rclone.log'

# ============================================================================
# MAIN
# ============================================================================

def main():
    """Point d'entrée principal du script."""
    # === ARGUMENTS CLI ===
    parser = argparse.ArgumentParser(description='Test local scan Plex')
    parser.add_argument('--test', type=int, metavar='N',
                       help='Mode test : limiter à N bibliothèques maximum')
    parser.add_argument('--instance', choices=['lite', 'standard', 'power', 'superpower'],
                        default='lite', help='Profil d\'instance (détermine config rclone)')
    parser.add_argument('--skip-scan', action='store_true',
                       help='Sauter la phase de scan')
    parser.add_argument('--skip-analysis', action='store_true',
                       help='Sauter l\'analyse Sonic')
    parser.add_argument('--filter', type=str, metavar='PREFIX',
                       help='Filtrer le scan music par préfixe (ex: --filter Q)')
    parser.add_argument('--keep', action='store_true',
                       help='Garder le conteneur après test')
    parser.add_argument('--collect-logs', action='store_true',
                       help='Récupérer les logs Plex en fin de run')
    parser.add_argument('--save-output', action='store_true',
                       help='Sauvegarder l\'output terminal dans logs/')
    parser.add_argument('--quick-test', action='store_true',
                       help='Mode test rapide : skip Sonic, scan validation uniquement')
    parser.add_argument('--music-only', action='store_true',
                       help='Traiter uniquement la section Musique (skip autres sections)')
    parser.add_argument('--force-refresh', action='store_true',
                       help='Refresh Metadata avant Sonic (images, paroles, matching)')
    parser.add_argument('--profile', choices=['local', 'cloud'],
                        default='local', help='Profil d\'exécution (timeouts, monitoring)')

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

    # === CONFIGURATION ===
    ip = 'localhost'
    env = load_env()
    libraries = load_libraries(limit=args.test)

    # Profil rclone basé sur le type d'instance
    rclone_profile = args.instance

    print("=" * 60)
    print("🧪 TEST LOCAL SCAN PLEX - VERSION REFACTORISÉE")
    print("=" * 60)
    print(f"Profil instance : {args.instance}")
    print(f"Profil rclone   : {rclone_profile}")
    print(f"Bibliothèques   : {len(libraries)}")
    if args.test:
        print(f"Mode test       : limité à {args.test} bibliothèque(s)")
    print("=" * 60)

    try:
        # 1. Setup des dossiers
        setup_local_test_env(TEST_DIR, MOUNT_DIR, PLEX_CONFIG)

        # Flag pour l'analyse Sonic (mis à jour après vérification Plex Pass)
        CAN_DO_SONIC = False

        # 3. Préparation environnement
        print_phase_header(1, "PRÉPARATION")
        print(f"✅ Dossiers de travail dans {TEST_DIR}")

        # 4. Configuration rclone
        setup_rclone_config(ip)

        # 5. Montage S3
        print_phase_header(2, "MONTAGE S3")
        mount_s3(
            ip,
            env['S3_BUCKET'],
            profile=rclone_profile,
            mount_point=str(MOUNT_DIR),
            cache_dir=str(CACHE_DIR),
            log_file=str(LOG_FILE)
        )

        # Vérifier le montage
        result = execute_command(ip, f"ls {MOUNT_DIR} | head -10", capture_output=True)
        print(f"\n📂 Contenu du montage S3:\n{result.stdout}")

        ### Abandonné suite au changement de stratégie ###
        # # Pré-chauffage du cache rclone
        # print("⏳ Pré-chauffage du cache rclone...")
        # # On préchauffe pour éviter que Plex ne timeout sur les séries
        # paths_to_warm = []
        # for lib in libraries:
        #      # lib['paths'] est une liste, on l'étend
        #      if isinstance(lib['paths'], list):
        #          paths_to_warm.extend(lib['paths'])
        #      else:
        #          paths_to_warm.append(lib['paths'])

        # # Dédoublonner
        # paths_to_warm = list(set(paths_to_warm))

        # for path in paths_to_warm:
        #     # Attention: les chemins dans 'libraries' sont souvent absolus pour le conteneur (ex: /Media/TV)
        #     # Il faut les convertir en chemins locaux (ex: ./tmp/s3-media/TV)

        #     # Hack rapide pour le test local : remplacer /Media par MOUNT_DIR
        #     local_path = path.replace('/Media', str(MOUNT_DIR))

        #     if os.path.exists(local_path):
        #         prewarm_rclone_cache(ip, local_path, max_depth=3)
        #     else:
        #         print(f"⚠️ Chemin introuvable pour préchauffage: {local_path}")

        # 6. Préparation des volumes Plex
        print_phase_header(3, "PRÉPARATION DES VOLUMES PLEX")

        print("📁 Création des volumes Plex...")
        execute_command(ip, f"mkdir -p {PLEX_CONFIG}", check=False)
        execute_command(ip, f"mkdir -p {PLEX_CONFIG / 'transcode'}", check=False)
        execute_command(ip, f"chmod -R 777 {PLEX_CONFIG}", check=False)
        print(f"  ✅ {PLEX_CONFIG}")
        print(f"  ✅ {PLEX_CONFIG / 'transcode'}")

        # 2. Claim Token
        plex_claim = input("\n🔑 Entrez votre PLEX_CLAIM (depuis https://www.plex.tv/claim) : ").strip()
        if not plex_claim:
            print("❌ PLEX_CLAIM requis")
            return

        # 6. Lancement Plex
        print_phase_header(4, "DÉMARRAGE PLEX")

        # Optimisations système (désactivées en local)
        apply_system_optimizations(ip)

        # Récupérer les limites Docker selon le profil
        docker_limits = get_docker_limits(args.instance)

        start_plex_container(
            ip=ip,
            claim_token=plex_claim,
            version=env['PLEX_VERSION'],
            container_name='plex',
            config_path=str(PLEX_CONFIG.absolute()),
            media_path=str(MOUNT_DIR.absolute()),
            transcode_path=str(PLEX_CONFIG.absolute() / 'transcode'),
            memory=docker_limits['memory'],
            memory_swap=docker_limits['memory_swap'],
            cpus=docker_limits['cpus']
        )

        # ATTENTION : Attendre que Plex soit COMPLÈTEMENT initialisé
        if not wait_plex_fully_ready(ip, container='plex', timeout=180):
            print("⚠️  Continuation malgré l'initialisation incomplète")

        # Récupérer le token Plex pour les appels API
        time.sleep(5) # Sécurité pour l'écriture du Preferences.xml
        plex_token = get_plex_token(ip, container='plex')
        if not plex_token:
            print("⚠️  Attention : token Plex non disponible, les requêtes API pourraient échouer")

        # Vérification forcée du Pass
        print("\n" + "VÉRIFICATION PLEX PASS")
        print("=" * 60)

        pass_status = verify_plex_pass_active(
            ip,
            container='plex',
            plex_token=plex_token,
            timeout=120,
            check_interval=10
        )

        CAN_DO_SONIC = pass_status['active']

        if not CAN_DO_SONIC:
            print("\\n⚠️  L'analyse Sonic sera IGNORÉE (feature Plex Pass)")
            print("   Le scan de découverte reste fonctionnel.")

        # Diagnostic avant création des bibliothèques
        debug_library_creation(ip, container='plex', plex_token=plex_token)

        # Activer les analyses
        if plex_token:
            enable_plex_analysis_via_api(ip, 'plex', plex_token)

        # === PHASE 5: Ajout des bibliothèques ===
        print_phase_header(5, "CONFIGURATION BIBLIOTHÈQUES")

        if not wait_plex_ready_for_libraries(ip, 'plex', plex_token):
            print("⚠️ Plex pas prêt, on continue quand même...")

        # Phase 5 avec la fonction simplifiée
        success_count = 0
        for lib in libraries:
            if add_library(ip, 'plex', lib, plex_token):
                success_count += 1
            time.sleep(2)  # Pause entre chaque création

        print(f"\n📊 Résumé: {success_count}/{len(libraries)} bibliothèques créées")

        # Attendre que Plex finisse d'initialiser les bibliothèques
        if success_count > 0:
            print("⏳ Pause de 10s pour finalisation des bibliothèques...")
            time.sleep(10)

        if args.skip_scan:
            print("\n⏭️  Phase de scan désactivée (--skip-scan)")
        else:
            # === PHASE 6: TRAITEMENT MUSIQUE (Sonic) ===
            print_phase_header(6, "TRAITEMENT MUSIQUE (Sonic)")

            # 6.1 Désactivation tâches de fond
            print("\n6.1 Désactivation des tâches de fond...")
            disable_all_background_tasks(ip, 'plex', plex_token)

            # 6.2 Récupérer les sections réelles de Plex
            print("\n6.2 Identification des sections...")
            section_info = {}
            api_cmd = f"curl -s 'http://localhost:32400/library/sections' -H 'X-Plex-Token: {plex_token}'"
            result = docker_exec(ip, 'plex', api_cmd, capture_output=True, check=False)

            for match in re.finditer(r'key="(\d+)".*?type="([^"]+)".*?title="([^"]+)"', result.stdout):
                s_id, s_type, s_title = match.group(1), match.group(2), match.group(3)
                section_info[s_title] = {"id": s_id, "type": s_type}

            print(f"   📚 Sections trouvées: {len(section_info)}")
            for name, info in section_info.items():
                print(f"      [{info['id']}] {name} ({info['type']})")

            # 6.3 Scanner la section Musique
            print("\n6.3 Scan de la section Musique...")
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
                    # Scan filtré via scan_section_incrementally
                    local_mount_path = MOUNT_DIR / music_section_name
                    if local_mount_path.exists():
                        filter_prefixes = [args.filter.upper()]
                        print(f"   📂 Scan avec filtre {filter_prefixes} dans {local_mount_path}")

                        # Trouver le chemin container correspondant
                        container_path = None
                        for lib in libraries:
                            if lib['title'] == music_section_name:
                                container_path = lib['paths'][0]
                                break

                        if container_path:
                            scan_section_incrementally(
                                ip, 'plex', plex_token,
                                music_section_id, 'artist',
                                container_path, str(local_mount_path),
                                filter_prefixes=filter_prefixes
                            )
                        else:
                            print(f"   ⚠️  Chemin container non trouvé, fallback sur API refresh")
                            trigger_section_scan(ip, 'plex', plex_token, music_section_id, force=False)
                    else:
                        print(f"   ⚠️  Dossier local non trouvé: {local_mount_path}, fallback sur API refresh")
                        trigger_section_scan(ip, 'plex', plex_token, music_section_id, force=False)
                else:
                    # Scan global via API
                    trigger_section_scan(ip, 'plex', plex_token, music_section_id, force=False)

                # Attendre fin du scan
                wait_section_idle(ip, 'plex', plex_token, music_section_id,
                                  section_type='artist', phase='scan', config_path=str(PLEX_CONFIG))
                print("   ✅ Scan Musique terminé")
            else:
                print("   ⚠️  Aucune section Musique trouvée")

            # 6.4 Analyse Sonic (sauf si --quick-test ou --skip-analysis)
            if args.quick_test or args.skip_analysis:
                print("\n6.4 Analyse Sonic SKIPPÉE (--quick-test ou --skip-analysis)")
            elif not CAN_DO_SONIC:
                print("\n6.4 Analyse Sonic IGNORÉE (Plex Pass non actif)")
                print("   → Les métadonnées et artworks ont été récupérés")
                print("   → Seul le fingerprinting audio est indisponible")
            elif not music_section_id:
                print("\n6.4 Analyse Sonic IGNORÉE (pas de section Musique)")
            else:
                print("\n6.4 Analyse Sonic...")
                enable_music_analysis_only(ip, 'plex', plex_token)

                # 6.4a Refresh Metadata si demandé (images, paroles, matching)
                if args.force_refresh:
                    print("\n6.4a Refresh Metadata (images, paroles, matching)...")
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

                    # 6.4b Stabilisation avant Sonic
                    print("\n6.4b Stabilisation avant Sonic...")
                    wait_plex_stabilized(ip, 'plex', plex_token,
                                         cooldown_checks=3,
                                         check_interval=60,
                                         cpu_threshold=20.0,
                                         timeout=1800)

                # 6.4c Lancer Sonic (sans --force, le refresh a été fait séparément)
                print("\n6.4c Lancement analyse Sonic...")
                trigger_sonic_analysis(ip, music_section_id, 'plex')

                # Monitoring DB-based avec profil adapté
                monitoring_profile = 'cloud_intensive' if args.profile == 'cloud' else 'local_quick'
                monitoring_params = get_monitoring_params(monitoring_profile)

                sonic_result = wait_sonic_complete(
                    ip, str(PLEX_CONFIG), music_section_id,
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

            # 6.5 Export intermédiaire (si profil cloud)
            if args.profile == 'cloud':
                print("\n6.5 Export intermédiaire...")
                export_intermediate(ip, 'plex', str(PLEX_CONFIG), '.', label="post_sonic")

            # === PHASE 7: VALIDATION AUTRES SECTIONS ===
            if not args.music_only:
                print_phase_header(7, "VALIDATION AUTRES SECTIONS")

                # 7.1 Réactivation analyses
                print("\n7.1 Réactivation des analyses (Photos/Vidéos)...")
                enable_all_analysis(ip, 'plex', plex_token)

                # 7.2 Scan séquentiel des autres sections
                print("\n7.2 Scan des sections restantes (séquentiel)...")
                other_sections = [(name, info) for name, info in section_info.items()
                                  if info['type'] != 'artist']

                if other_sections:
                    for section_name, info in other_sections:
                        # Étape 1: Scan
                        print(f"\n   🔍 Scan '{section_name}' (ID: {info['id']}, type: {info['type']})")
                        trigger_section_scan(ip, 'plex', plex_token, info['id'], force=False)
                        wait_section_idle(ip, 'plex', plex_token, info['id'],
                                          section_type=info['type'], phase='scan',
                                          config_path=str(PLEX_CONFIG), timeout=3600)

                        # Étape 2: Analyse (thumbnails, chapitres, etc.)
                        if not args.skip_analysis and not args.quick_test:
                            print(f"   🔬 Analyse '{section_name}'")
                            trigger_section_analyze(ip, 'plex', plex_token, info['id'])
                            wait_section_idle(ip, 'plex', plex_token, info['id'],
                                              section_type=info['type'], phase='analyze',
                                              config_path=str(PLEX_CONFIG), timeout=3600)

                    print("\n   ✅ Autres sections terminées")
                else:
                    print("   Aucune autre section à traiter")
            else:
                print("\n⏭️  Phase 7 SKIPPÉE (--music-only)")

        # 10. Export (ALIGNÉ avec automate_scan.py + horodatage)
        print_phase_header(8, "EXPORT MÉTADONNÉES")

        # 8.1 Collecte logs Plex AVANT arrêt (le conteneur doit tourner)
        # Note: Le terminal log sera ajouté dans finally après tee_logger.stop()
        if args.collect_logs or args.save_output:
            print("\n8.1 Collecte des logs Plex (conteneur actif)...")
            plex_logs_archive = collect_plex_logs(ip, 'plex', prefix="final", timestamp=RUN_TIMESTAMP)

        # 8.2 Arrêter Plex
        print("\n8.2 Arrêt de Plex...")
        stop_plex(ip, container='plex')
        time.sleep(3)

        # 8.3 Export complet (utilise RUN_TIMESTAMP pour cohérence)
        print("\n8.3 Export complet...")
        archive_name = f'plex_metadata_local_{RUN_TIMESTAMP}.tar.gz'

        # ✅ APRÈS - Logique conditionnelle
        archive_path = export_metadata(
            ip,
            container='plex',
            archive_name=archive_name,
            config_path=str(PLEX_CONFIG)
        )

        if ip == 'localhost':
            # Déjà local, pas besoin de télécharger
            print(f"✅ Archive disponible localement : {archive_path}")
            local_archive = archive_path
        else:
            # Remote : télécharger l'archive
            local_archive = f'./{archive_name}'
            download_file_from_remote(ip, archive_path, local_archive)

        print("\n" + "=" * 60)
        print("✅ TEST TERMINÉ AVEC SUCCÈS")
        print("=" * 60)
        print(f"📦 Archive exportée : {local_archive}")

    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrompu par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # === DIAGNOSTIC POST-MORTEM ===
        print("\n" + "=" * 60)
        print("🔍 DIAGNOSTIC POST-MORTEM")
        print("=" * 60)

        # Vérifier si le conteneur a été tué par manque de RAM (OOM Killer)
        oom_cmd = "docker inspect plex --format '{{.State.OOMKilled}}' 2>/dev/null"
        oom_result = execute_command(ip, oom_cmd, capture_output=True, check=False)
        is_oom = oom_result.stdout.strip() == 'true'

        if is_oom:
            print("🚨 ALERTE: Conteneur tué par manque de mémoire (OOM)")
            print("   👉 Solution: Augmentez la RAM allouée ou passez à un profil d'instance supérieur")
        else:
            print("✅ Pas de kill mémoire (OOM)")

        # Diagnostic Sonic
        print("\n🎹 DIAGNOSTIC SONIC:")
        db_path = "/config/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

        sonic_count_cmd = f'''docker exec plex sqlite3 "{db_path}" "
            SELECT
                (SELECT COUNT(*) FROM media_item_settings WHERE loudness != 0) as loudness_count,
                (SELECT COUNT(*) FROM media_parts WHERE extra_data LIKE '%hasSonicAnalysis%1%') as sonic_flag_count
        " 2>/dev/null || echo "DB inaccessible"'''
        sonic_result = execute_command(ip, sonic_count_cmd, capture_output=True, check=False)
        print(f"   Comptage Sonic (loudness vs extra_data):")
        print(f"   {sonic_result.stdout.strip()}")

        # Afficher les dernières logs système du conteneur
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


if __name__ == "__main__":
    main()
