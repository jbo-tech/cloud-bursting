#!/usr/bin/env python3
"""
Gestion du cycle de vie Plex : montage, démarrage, configuration
"""
import time
import os
import re
from datetime import datetime
from .executor import execute_command, docker_exec, transfer_file_to_remote, execute_script
from .config import get_rclone_config_path, get_rclone_profile, get_rclone_remote_name

def apply_system_optimizations(ip):
    """
    Applique les optimisations système (sysctl, ulimits) pour l'environnement de production.
    Uniquement sur les instances distantes (pas en local).

    Args:
        ip: 'localhost' ou IP remote
    """
    if ip == 'localhost':
        print("⏭️  Optimisations système désactivées en mode local")
        return

    print("⚙️  Application des optimisations système...")

    sysctl_script = """#!/bin/bash
# Optimisations sysctl pour Plex
cat << 'EOF' | sudo tee -a /etc/sysctl.conf
fs.file-max=500000
vm.swappiness=10
vm.vfs_cache_pressure=50
EOF

sudo sysctl -p

# Optimisations ulimits
cat << 'EOF' | sudo tee -a /etc/security/limits.conf
* soft nofile 100000
* hard nofile 500000
EOF

echo "✅ Optimisations système appliquées"
"""

    execute_script(ip, sysctl_script, '/tmp/apply_system_optimizations.sh')
    print("✅ Optimisations système appliquées")


def cleanup_plex_data(ip):
    """
    Supprime les données Plex pour repartir sur un état propre.

    Args:
        ip: 'localhost' ou IP remote
    """
    print("🧹 Nettoyage des données Plex...")
    execute_command(ip, "rm -rf /opt/plex_data/*", check=False)
    execute_command(ip, "mkdir -p /opt/plex_data/{config,transcode}")
    execute_command(ip, "chmod -R 777 /opt/plex_data")


def setup_rclone_config(ip):
    """
    Copie le fichier rclone.conf vers /tmp.
    - Local: utilise ./tmp du projet pour ne pas écraser ~/.config/rclone/
    - Remote: utilise /tmp de l'instance (éphémère)

    Args:
        ip: 'localhost' ou IP remote

    Returns:
        str: Chemin du fichier rclone.conf copié
    """
    rclone_config = get_rclone_config_path()

    if ip == 'localhost':
        # Utiliser ./tmp du projet pour ne pas interférer avec la config système
        local_tmp = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tmp')
        print(f"📋 [LOCAL] Copie rclone.conf vers {local_tmp}/")
        execute_command(ip, f"mkdir -p {local_tmp}", check=False)
        execute_command(ip, f"cp {rclone_config} {local_tmp}/rclone.conf")
        print(f"✅ rclone.conf copié vers {local_tmp}/rclone.conf")
        return f"{local_tmp}/rclone.conf"

    print("📤 Copie de rclone.conf sur l'instance...")
    transfer_file_to_remote(rclone_config, ip, '/tmp/rclone.conf')
    print("✅ rclone.conf copié vers /tmp/rclone.conf")
    return '/tmp/rclone.conf'


def mount_s3(ip, rclone_remote, profile='lite', mount_point='/mnt/s3-media', cache_dir=None, log_file=None, config_path=None):
    """
    Monte le bucket S3 via rclone avec une configuration optimisée.

    Args:
        ip: 'localhost' ou IP remote
        rclone_remote: Nom du bucket/chemin S3
        profile: Profil de configuration (lite, balanced, performance)
        mount_point: Point de montage
        cache_dir: Répertoire de cache rclone
        log_file: Fichier de logs rclone
        config_path: Chemin du fichier rclone.conf (si None, utilise ~/.config/rclone/rclone.conf par défaut)
    """
    print(f"📦 Montage S3 : {rclone_remote} → {mount_point} (profil: {profile})")

    # Récupération du nom du remote rclone
    remote_name = get_rclone_remote_name()

    # Détecter le chemin de config si non fourni
    if config_path is None:
        if ip == 'localhost':
            local_tmp = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tmp')
            config_path = f"{local_tmp}/rclone.conf"
        else:
            config_path = '/tmp/rclone.conf'

    print(f"🔧 Utilisation de la config rclone : {config_path}")

    # Auto-détection des chemins selon le contexte
    if cache_dir is None:
        if ip == 'localhost':
            cache_dir = os.path.expanduser('~/tmp/rclone-cache')
        else:
            cache_dir = '/mnt/rclone-cache'

    if log_file is None:
        if ip == 'localhost':
            log_file = os.path.expanduser('~/tmp/rclone.log')
        else:
            log_file = '/var/log/rclone.log'

    config = get_rclone_profile(profile)

    mount_script = f"""#!/bin/bash
set -e

echo "🔧 Nettoyage préalable..."
pkill -f "rclone mount" || true
sleep 2
fusermount3 -uz {mount_point} || true

echo "🔧 Création des répertoires..."
mkdir -p {mount_point} {cache_dir}

echo "🔧 Vérification du remote rclone '{remote_name}'..."
if ! rclone --config {config_path} listremotes | grep -q "^{remote_name}:$"; then
    echo "❌ Remote '{remote_name}' non trouvé"
    rclone --config {config_path} listremotes
    exit 1
fi

echo "🔧 Test de connexion au remote..."
if ! rclone --config {config_path} lsd {remote_name}:{rclone_remote} --max-depth 1 2>&1 | head -10; then
    echo "❌ Impossible de lister le contenu du remote"
    exit 1
fi

echo "🔧 Vérification de /etc/fuse.conf..."
if ! grep -q "^user_allow_other" /etc/fuse.conf 2>/dev/null; then
    echo "⚠️  ATTENTION: user_allow_other n'est pas activé dans /etc/fuse.conf"
    echo "   Cela peut causer des problèmes avec Docker + FUSE"
    echo "   Solution: sudo nano /etc/fuse.conf et décommenter 'user_allow_other'"
fi

echo "🔧 Lancement du montage rclone..."
nohup rclone mount {remote_name}:{rclone_remote} {mount_point} \\
  --config {config_path} \\
  --cache-dir={cache_dir} \\
  --vfs-cache-mode full \\
  --vfs-cache-max-size {config['cache_size']} \\
  --vfs-cache-max-age 72h \\
  --vfs-read-ahead {config['buffer_size']} \\
  --vfs-read-chunk-size {config['read_chunk']} \\
  --vfs-read-chunk-size-limit 1G \\
  --buffer-size {config['buffer_size']} \\
  --transfers {config['transfers']} \\
  --checkers {config['checkers']} \\
  --timeout {config['timeout']} \\
  --contimeout {config['contimeout']} \\
  --low-level-retries {config['low_level_retries']} \\
  --retries {config['retries']} \\
  --retries-sleep {config['retries_sleep']} \\
  --dir-cache-time {config['dir_cache']} \\
  --attr-timeout {config['attr_timeout']} \\
  --poll-interval 1m \\
  --s3-no-head \\
  --no-checksum \\
  --allow-other \\
  --uid 1000 \\
  --gid 1000 \\
  --log-level INFO \\
  --log-file={log_file} \\
  --stats 5m \\
  --stats-log-level INFO \\
  --daemon </dev/null >/dev/null 2>&1 &

echo "⏳ Attente de stabilisation du montage (10s)..."
sleep 10

# Vérifications multiples
echo "🔍 Vérification 1: mountpoint"
if ! mountpoint -q {mount_point}; then
    echo "❌ Le point de montage n'est pas actif"
    exit 1
fi
echo "✅ mountpoint OK"

echo "🔍 Vérification 2: stat"
if ! stat {mount_point} > /dev/null 2>&1; then
    echo "❌ Impossible de faire stat sur le point de montage"
    exit 1
fi
echo "✅ stat OK"

echo "🔍 Vérification 3: ls (test de lecture)"
if ! ls {mount_point} > /dev/null 2>&1; then
    echo "❌ Impossible de lire le contenu du point de montage"
    tail -20 {log_file}
    exit 1
fi
echo "✅ ls OK"

echo "🔍 Vérification 4: Permissions"
ls -la {mount_point} | head -5

echo "✅ Montage S3 complet et validé"
"""

    print(f"""
rclone mount {remote_name}:{rclone_remote} {mount_point} \\
    --config {config_path} \\
    --cache-dir={cache_dir} \\
    --vfs-cache-mode full \\
    --vfs-cache-max-size {config['cache_size']} \\
    --vfs-cache-max-age 72h \\
    --vfs-read-ahead {config['buffer_size']} \\
    --vfs-read-chunk-size {config['read_chunk']} \\
    --vfs-read-chunk-size-limit 1G \\
    --buffer-size {config['buffer_size']} \\
    --transfers {config['transfers']} \\
    --checkers {config['checkers']} \\
    --timeout {config['timeout']} \\
    --contimeout {config['contimeout']} \\
    --low-level-retries {config['low_level_retries']} \\
    --retries {config['retries']} \\
    --retries-sleep {config['retries_sleep']} \\
    --dir-cache-time {config['dir_cache']} \\
    --attr-timeout {config['attr_timeout']} \\
    --poll-interval 1m \\
    --s3-no-head \\
    --no-checksum \\
    --allow-other \\
    --uid 1000 \\
    --gid 1000 \\
    --log-level INFO \\
    --log-file={log_file} \\
    --stats 5m \\
    --stats-log-level INFO \\
    --daemon
    """)

    execute_script(ip, mount_script, '/tmp/mount_s3.sh')
    print(f"✅ S3 monté et accessible par Docker sur {mount_point}")


def start_plex_container(ip, claim_token, version='latest', container_name='plex',
                        config_path='/opt/plex_data/config',
                        media_path='/mnt/s3-media',
                        transcode_path='/opt/plex_data/transcode',
                        memory='4g',
                        memory_swap='6g',
                        cpus='2.0',
                        wait_for_s3=True):
    """
    Lance le conteneur Plex avec des chemins de volumes configurables.
    Version alignée avec setup-plex-unified.sh fonctionnel.

    Args:
        ip: 'localhost' ou IP remote
        claim_token: Token Plex Claim
        version: Version de Plex (default: 'latest')
        container_name: Nom du conteneur Docker
        config_path: Chemin de configuration Plex
        media_path: Chemin du montage S3
        transcode_path: Chemin de transcodage
        memory: Limite mémoire Docker (ex: '4g')
        memory_swap: Limite swap Docker (ex: '6g')
        cpus: Nombre de CPUs (ex: '2.0')
        wait_for_s3: Attendre que S3 soit prêt avant de lancer Plex
    """
    print(f"🚀 Démarrage du conteneur Plex (version: {version})...")

    if wait_for_s3:
        print("   ⏳ Vérification de la disponibilité du montage S3...")
        # Attendre que le S3 soit vraiment prêt
        s3_ready_script = f"""#!/bin/bash
for i in {{1..30}}; do
    if mountpoint -q {media_path} && timeout 5 ls {media_path} > /dev/null 2>&1; then
        echo "✅ S3 prêt après $i secondes"
        exit 0
    fi
    echo "   ... attente du S3 ($i/30)"
    sleep 2
done
echo "❌ Timeout - S3 non prêt après 60 secondes"
exit 1
"""
        execute_script(ip, s3_ready_script, '/tmp/wait_for_s3.sh')

    # Vérifier que les volumes existent
    print(f"   📁 Vérification des volumes...")
    for path, name in [(config_path, 'config'), (media_path, 'media'), (transcode_path, 'transcode')]:
        result = execute_command(ip, f"test -d {path}", check=False, capture_output=True)
        if result.returncode == 0:
            print(f"   ✅ {name}: {path}")
        else:
            print(f"   ⚠️  {name}: {path} n'existe pas, création...")
            execute_command(ip, f"mkdir -p {path}")

    # Script de lancement aligné avec setup-plex-unified.sh
    launch_script = f"""#!/bin/bash
set -euo pipefail

echo "🧹 Nettoyage d'éventuels conteneurs existants..."
docker stop "{container_name}" 2>/dev/null || true
docker rm -f "{container_name}" 2>/dev/null || true

echo "🐳 Lancement du conteneur Plex..."
docker run -d \\
    --name "{container_name}" \\
    --restart unless-stopped \\
    --memory={memory} \\
    --memory-swap={memory_swap} \\
    --cpus={cpus} \\
    -p 32400:32400 \\
    -e TZ="Europe/Paris" \\
    -e PLEX_UID=1000 \\
    -e PLEX_GID=1000 \\
    -e PLEX_CLAIM="{claim_token}" \\
    -v "{media_path}:/Media:ro" \\
    -v "{config_path}:/config" \\
    -v "{transcode_path}:/transcode" \\
    plexinc/pms-docker:{version}

echo "⏳ Attente de stabilisation (60s)..."
sleep 60

echo "✅ Conteneur Plex lancé"
docker logs "{container_name}" 2>&1 | tail -20
"""

    print(f"""
docker run -d \\
    --name "{container_name}" \\
    --restart unless-stopped \\
    --memory={memory} \\
    --memory-swap={memory_swap} \\
    --cpus={cpus} \\
    -p 32400:32400 \\
    -e TZ="Europe/Paris" \\
    -e PLEX_UID=1000 \\
    -e PLEX_GID=1000 \\
    -e PLEX_CLAIM="{claim_token}" \\
    -v "{media_path}:/Media:ro" \\
    -v "{config_path}:/config" \\
    -v "{transcode_path}:/transcode" \\
    plexinc/pms-docker:{version}
    """)

    try:
        execute_script(ip, launch_script, '/tmp/start_plex.sh')

    except Exception as e:
        print(f"❌ Erreur lors du démarrage: {e}")
        # Tenter de récupérer les logs pour debug
        print("🔍 Tentative de récupération des logs...")
        logs_result = execute_command(ip, f"docker logs {container_name}", check=False, capture_output=True)
        if logs_result.returncode == 0:
            print(f"📋 Logs du conteneur:\n{logs_result.stdout}")
        raise


def configure_plex_via_api(ip, container, plex_token):
    """
    Configure les préférences Plex via API pour désactiver TOUTES les analyses.
    Plus rapide et sûr que l'édition XML.
    """
    print("⚙️  Configuration des préférences Plex via API (Mode Scan Only)...")

    # Liste des paramètres critiques pour éviter le verrouillage DB
    # On désactive tout ce qui consomme du CPU ou écrit en DB pendant le scan
    params = {
        "GenerateBIFBehavior": "never",           # Pas de thumbnails vidéo (Gros consommateur)
        "GenerateIntroMarkerBehavior": "never",   # Pas de détection d'intro (Gros consommateur)
        "GenerateChapterBehavior": "never",       # Pas de chapitres
        "LoudnessAnalysisBehavior": "never",      # Pas d'analyse sonique
        "MusicAnalysisBehavior": "never",         # Pas d'analyse musique
        "AutoScan": "0",                          # Pas de scan auto
        "ButlerTaskDeepAnalysis": "0",            # Pas d'analyse profonde programmée
        "BackgroundQueueIdlePaused": "1"          # (Ton idée) On pause la queue par sécurité
    }

    # Construction de la query string
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])

    # URL de l'endpoint préférences
    url = f"http://localhost:32400/:/prefs?{query_string}"

    # Appel via curl dans le conteneur
    cmd = f"curl -s -X PUT '{url}' -H 'X-Plex-Token: {plex_token}'"

    result = docker_exec(ip, container, cmd, capture_output=True, check=False)

    if result.returncode == 0:
        print("✅ Préférences appliquées avec succès via API")
        # On vérifie une clé pour être sûr
        check_cmd = f"curl -s 'http://localhost:32400/:/prefs' -H 'X-Plex-Token: {plex_token}' | grep 'GenerateIntroMarkerBehavior'"
        docker_exec(ip, container, check_cmd, check=False)
    else:
        print(f"❌ Erreur lors de la configuration API : {result.stderr}")
        # Fallback si l'API échoue : on prévient l'utilisateur
        print("⚠️  ATTENTION : Le serveur risque de lancer des analyses concurrentes.")


def enable_plex_analysis_via_api(ip, container, plex_token):
    """
    Réactive les tâches d'analyse (Sonic, Loudness) pour la Phase 7.
    """
    print("🧠 Configuration des analyses Plex...")

    # On remet les valeurs par défaut (ou "scheduled" / "asap")
    params = {
        "GenerateBIFBehavior": "scheduled",       # Thumbnails
        "GenerateIntroMarkerBehavior": "asap",    # Intros
        "GenerateChapterBehavior": "scheduled",   # Chapitres
        "LoudnessAnalysisBehavior": "asap",       # Analyse sonique (CRUCIAL pour vous)
        "MusicAnalysisBehavior": "asap",          # Analyse musique
        "AutoScan": "0",                          # On garde l'autoscan OFF
        "ButlerTaskDeepAnalysis": "1",            # On réactive le Butler
        "BackgroundQueueIdlePaused": "0"          # On relance la queue
    }

    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    url = f"http://localhost:32400/:/prefs?{query_string}"

    cmd = f"curl -s -X PUT '{url}' -H 'X-Plex-Token: {plex_token}'"
    result = docker_exec(ip, container, cmd, capture_output=True, check=False)

    if result.returncode == 0:
        print("✅ Préférences d'analyse configurées.")

        # Déclencher le Butler pour lancer les analyses immédiatement
        # Note (2026-01-23): Sans cet appel, les préférences "asap" ne déclenchent
        # PAS l'analyse sur les items existants. Le Butler doit être appelé
        # explicitement pour scanner la bibliothèque et lancer les tâches.
        butler_cmd = f"curl -s -X POST 'http://localhost:32400/butler/DeepMediaAnalysis' -H 'X-Plex-Token: {plex_token}'"
        docker_exec(ip, container, butler_cmd, check=False)
        print("✅ Butler DeepMediaAnalysis déclenché.")
    else:
        print(f"❌ Erreur réactivation : {result.stderr}")


def debug_plex_container(ip, container='plex'):
    """Fonction de debug pour investiguer l'état du conteneur Plex"""
    debug_script = f"""#!/bin/bash
echo "=== État du conteneur ==="
docker ps -a --filter name={container}
echo ""
echo "=== Logs du conteneur ==="
docker logs {container} || echo "Aucun log disponible"
echo ""
echo "=== Inspection détaillée ==="
docker inspect {container} || echo "Conteneur non trouvé"
echo ""
echo "=== Processus Docker ==="
docker ps -a
echo ""
echo "=== Espace disque ==="
df -h
echo ""
echo "=== Permissions des volumes ==="
ls -la /opt/plex_data/ /mnt/s3-media/ 2>/dev/null || echo "Certains volumes non accessibles"
"""
    execute_script(ip, debug_script, '/tmp/debug_plex.sh')


def get_plex_token(ip, container='plex', timeout=120, retry_interval=10):
    """
    Récupère le token d'authentification Plex depuis le conteneur avec retry.
    Le token est stocké dans /config/Library/Application Support/Plex Media Server/Preferences.xml

    Le token peut mettre du temps à apparaître après le claim initial,
    d'où le mécanisme de retry.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        timeout: Temps max d'attente en secondes (default: 120s)
        retry_interval: Intervalle entre les tentatives (default: 10s)

    Returns:
        str: Token Plex (X-Plex-Token) ou None si échec
    """
    print("🔑 Récupération du token Plex...")
    start_time = time.time()

    # Extraire le token depuis le fichier Preferences.xml
    cmd = (
        f"docker exec {container} cat '/config/Library/Application Support/Plex Media Server/Preferences.xml' "
        f"| grep -oP 'PlexOnlineToken=\"\\K[^\"]+'"
    )

    while time.time() - start_time < timeout:
        elapsed = int(time.time() - start_time)
        result = execute_command(ip, cmd, capture_output=True, check=False)

        if result.returncode == 0 and result.stdout.strip():
            token = result.stdout.strip()
            print(f"   ✅ Token récupéré après {elapsed}s : {token[:20]}...")
            return token

        # Log de progression
        if elapsed > 0 and elapsed % 30 == 0:
            print(f"   ⏳ Token non disponible... ({elapsed}s/{timeout}s)")

        time.sleep(retry_interval)

    print(f"⚠️  Impossible de récupérer le token Plex après {timeout}s")
    return None


def wait_plex_ready(ip, container='plex', timeout=180):
    """
    Attend que Plex soit prêt (conteneur healthy + API répond).
    """
    print("⏳ Attente du démarrage complet de Plex...")

    start_time = time.time()

    while time.time() - start_time < timeout:
        elapsed = int(time.time() - start_time)

        # 1. Vérifier si le conteneur est en cours d'exécution
        check_cmd = f"docker inspect --format '{{{{.State.Running}}}}' {container} 2>/dev/null"
        docker_result = execute_command(ip, check_cmd, check=False, capture_output=True)

        if docker_result.returncode != 0 or docker_result.stdout.strip() != 'true':
            if elapsed % 10 == 0:  # Log toutes les 10s
                print(f"   ⏳ [{elapsed}s] Conteneur pas encore démarré...")
            time.sleep(5)
            continue

        # 2. Vérifier l'API Plex via docker exec curl (plus fiable que requests)
        api_check = f"docker exec {container} curl -s http://localhost:32400/identity 2>/dev/null"
        api_result = execute_command(ip, api_check, check=False, capture_output=True)

        if api_result.returncode == 0 and 'MediaContainer' in api_result.stdout:
            print(f"✅ Plex est prêt (API répond après {elapsed}s)")
            return

        # Log de progression toutes les 30s
        if elapsed > 0 and elapsed % 30 == 0:
            print(f"   ⏳ API Plex pas encore prête... ({elapsed}s/{timeout}s)")

        time.sleep(5)

    raise TimeoutError(f"❌ Plex n'a pas démarré dans les {timeout}s")


def wait_plex_fully_ready(ip, container='plex', timeout=300):
    """
    Attend que Plex soit complètement prêt (serveur claimé + plugins chargés).

    Critères de validation :
    1. L'API /identity répond HTTP 200 avec claimed="1"
    2. Au moins 3 processus Plex actifs dans le conteneur

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        timeout: Temps max d'attente en secondes (default: 300s)

    Returns:
        bool: True si Plex est prêt, False sinon
    """
    print("⏳ Attente que Plex soit complètement initialisé...")
    start_time = time.time()
    last_api_status = "inconnu"
    last_api_response = ""
    is_claimed = False

    while time.time() - start_time < timeout:
        elapsed = int(time.time() - start_time)

        # Vérifier que Plex répond ET que le serveur est claimé
        cmd = "curl -s -w '\\n%{http_code}' http://localhost:32400/identity 2>&1"
        result = docker_exec(ip, container, cmd, capture_output=True, check=False)

        # Parser la réponse : body + code HTTP
        lines = result.stdout.strip().split('\n')
        http_code = lines[-1] if lines else "0"
        api_body = '\n'.join(lines[:-1]) if len(lines) > 1 else ""
        last_api_response = api_body[:200]

        # Analyser le statut de l'API - vérifier le claim
        if http_code == "200" and 'claimed="1"' in api_body:
            last_api_status = "OK (claimé)"
            is_claimed = True
        elif http_code == "200" and 'claimed="0"' in api_body:
            last_api_status = "Non claimé (PLEX_CLAIM invalide/expiré?)"
            is_claimed = False
        elif http_code == "200":
            last_api_status = "HTTP 200 (statut claim inconnu)"
            is_claimed = False
        elif http_code.isdigit() and int(http_code) > 0:
            last_api_status = f"HTTP {http_code}"
            is_claimed = False
        else:
            last_api_status = "pas de réponse"
            is_claimed = False

        # Vérifier aussi les processus système Plex
        processes_cmd = "ps aux | grep -i plex | grep -v grep | wc -l"
        processes_result = docker_exec(ip, container, processes_cmd, capture_output=True, check=False)
        plex_processes = int(processes_result.stdout.strip()) if processes_result.stdout.strip().isdigit() else 0

        # Critère de succès : serveur claimé + au moins 3 processus
        if is_claimed and plex_processes >= 3:
            print(f"✅ Plex complètement initialisé après {elapsed}s")
            return True

        # Log de progression avec détails
        print(f"   [{elapsed}s] Processus: {plex_processes}, API: {last_api_status}")
        time.sleep(30)

    # Timeout atteint - diagnostic détaillé
    print(f"\n⚠️  Plex n'est pas complètement initialisé après {timeout}s")
    print(f"   Dernier statut API : {last_api_status}")
    if last_api_response:
        print(f"   Dernière réponse   : {last_api_response[:100]}...")

    # Message d'aide si non claimé
    if not is_claimed and 'claimed="0"' in last_api_response:
        print("\n💡 Le serveur n'est pas claimé. Causes possibles :")
        print("   • PLEX_CLAIM expiré (durée de vie ~4 minutes)")
        print("   • PLEX_CLAIM déjà utilisé")
        print("   • Problème réseau vers plex.tv")
        print("   → Générez un nouveau claim : https://www.plex.tv/claim")

    # Capturer les logs Docker pour diagnostic
    print("\n📋 Logs Docker (dernières 20 lignes) :")
    logs_cmd = f"docker logs --tail 20 {container} 2>&1"
    logs_result = execute_command(ip, logs_cmd, capture_output=True, check=False)
    if logs_result.stdout:
        for line in logs_result.stdout.strip().split('\n')[:20]:
            print(f"   {line}")

    return False


def wait_plex_ready_for_libraries(ip, container, plex_token, timeout=120):
    """Attendre que Plex soit VRAIMENT prêt pour créer des bibliothèques"""
    print("⏳ Attente que Plex soit prêt pour les bibliothèques...")

    start = time.time()
    while time.time() - start < timeout:
        # Test si on peut créer une bibliothèque test
        cmd = f"curl -s 'http://localhost:32400/library/sections' -H 'X-Plex-Token: {plex_token}'"
        result = docker_exec(ip, container, cmd, capture_output=True, check=False)

        # Si la réponse contient "MediaContainer" sans erreur "starting"
        if "MediaContainer" in result.stdout and "starting" not in result.stdout:
            print("✅ Plex prêt")
            time.sleep(5)  # Sécurité supplémentaire
            return True

        time.sleep(5)

    print("⚠️ Timeout")
    return False


def add_library(ip, container, library_config, plex_token):
    """Version ultra simple pour debug"""
    title = library_config['title']
    print(f"📚 Ajout de '{title}'...")

    # Utiliser une URL simple avec paramètres GET (plus fiable que POST avec -F)
    from urllib.parse import quote

    params = {
        'name': title,
        'type': library_config['type'],
        'agent': library_config.get('agent', 'tv.plex.agents.movie'),
        'scanner': library_config.get('scanner', 'Plex Movie'),
        'language': library_config.get('language', 'fr-FR'),
        'location': library_config['paths'][0] if isinstance(library_config['paths'], list) else library_config['paths']
    }

    # # Utiliser l'agent "Plex Series" (Legacy) pour les séries TV
    # if params['type'] == 'show':
    #     # On utilise l'ancien agent plus permissif pour le test
    #     params['agent'] = "com.plexapp.agents.thetvdb"
    #     params['scanner'] = "Plex Series Scanner"
    #     params['language'] = "fr-FR"

    # Construire l'URL avec paramètres
    url_params = '&'.join([f"{k}={quote(str(v))}" for k, v in params.items()])
    curl_cmd = f"curl -X POST 'http://localhost:32400/library/sections?{url_params}&X-Plex-Token={plex_token}'"

    result = docker_exec(ip, container, curl_cmd, capture_output=True, check=False)

    # Debug : toujours voir ce qui se passe
    if result.stdout:
        print(f"   Location: {params['location']}")
        print(f"   Réponse: {result.stdout[:200]}")
    if result.stderr:
        print(f"   Erreur: {result.stderr[:200]}")

    # Vérifier immédiatement
    time.sleep(2)
    verify_cmd = f"curl -s 'http://localhost:32400/library/sections' -H 'X-Plex-Token: {plex_token}'"
    verify_result = docker_exec(ip, container, verify_cmd, capture_output=True, check=False)

    if title in verify_result.stdout:
        print(f"   ✅ '{title}' créée")
        return True
    else:
        print(f"   ❌ '{title}' non trouvée")
        return False


def create_library_section(ip, container, library_config, plex_token):
    """Juste la création via curl"""
    form_data = [
        f"name={library_config['title']}",
        f"type={library_config['type']}",
        f"agent={library_config.get('agent', 'tv.plex.agents.movie')}",
        f"scanner={library_config.get('scanner', 'Plex Movie')}",
        f"language={library_config.get('language', 'fr-FR')}",
    ]

    for path in library_config.get('paths', []):
        form_data.append(f"location={path}")

    form_string = " ".join([f"-F '{data}'" for data in form_data])
    headers = f"-H 'X-Plex-Token: {plex_token}'" if plex_token else ""

    curl_cmd = f"curl -sX POST http://localhost:32400/library/sections {form_string} {headers}"
    result = docker_exec(ip, container, curl_cmd, capture_output=True, check=False)

    if result.returncode != 0 or "error" in result.stdout.lower():
        print(f"   ❌ Erreur: {result.stderr or result.stdout}")
        return False
    return True


def wait_library_visible(ip, container, title, plex_token, max_wait=30):
    """Attendre que la bibliothèque soit visible dans l'API"""
    print(f"   ⏳ Attente visibilité...")

    for i in range(max_wait):
        time.sleep(1)

        cmd = f"curl -s 'http://localhost:32400/library/sections' -H 'X-Plex-Token: {plex_token}'"
        result = docker_exec(ip, container, cmd, capture_output=True, check=False)

        # Chercher la section
        match = re.search(rf'key="(\d+)"[^>]*title="{re.escape(title)}"', result.stdout)
        if match:
            return match.group(1)

    print(f"   ⚠️ Timeout après {max_wait}s")
    return None


def prewarm_rclone_cache(ip, mount_point, max_depth=3):
    """
    Préchauffe le cache rclone via Python (plus verbeux et contrôlable).
    """
    print(f"🔥 [CACHE] Démarrage du préchauffage sur {mount_point}...")

    if ip != 'localhost':
        # En remote, on garde 'find' car c'est plus rapide à envoyer via SSH
        # mais on retire > /dev/null pour voir si ça bloque
        cmd = f"find {mount_point} -maxdepth {max_depth} -type d | head -n 5 && echo '... (suite masquée)'"
        execute_command(ip, cmd, check=False)
        return

    # === VERSION LOCALE (PYTHON) ===
    start_time = time.time()
    dir_count = 0

    # Normaliser le chemin pour calculer la profondeur
    base_path = str(mount_point).rstrip(os.sep)
    base_depth = base_path.count(os.sep)

    try:
        for root, dirs, files in os.walk(base_path):
            # Calcul de la profondeur actuelle
            current_depth = root.count(os.sep) - base_depth

            # Feedback visuel toutes les 10 entrées pour ne pas spammer
            dir_count += 1
            if dir_count % 10 == 0:
                elapsed = int(time.time() - start_time)
                print(f"   ⏳ [{elapsed}s] Scannés: {dir_count} dossiers | Actuel: {os.path.basename(root)}", end='\r')

            # Si on atteint la profondeur max, on vide 'dirs' pour empêcher os.walk de descendre plus bas
            if current_depth >= max_depth:
                del dirs[:]

    except KeyboardInterrupt:
        print("\n⚠️ Préchauffage interrompu par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur durant le préchauffage : {e}")

    total_time = time.time() - start_time
    print(f"\n✅ [CACHE] Terminé : {dir_count} dossiers visités en {total_time:.1f}s")


def disable_all_background_tasks(ip, container, plex_token):
    """
    Désactive toutes les tâches de fond Plex pour éviter la compétition durant le scan.
    Met Plex en "mode silencieux" pour libérer les ressources.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        plex_token: Token d'authentification Plex

    Returns:
        bool: True si succès, False sinon
    """
    print("🔇 Désactivation de toutes les tâches de fond...")

    params = {
        "GenerateBIFBehavior": "never",
        "GenerateIntroMarkerBehavior": "never",
        "GenerateChapterBehavior": "never",
        "LoudnessAnalysisBehavior": "never",
        "MusicAnalysisBehavior": "never",
        "ButlerTaskDeepAnalysis": "0",
        "ButlerTaskGenerateAutoTags": "0",
        "ButlerTaskRefreshLibraries": "0",
        "ButlerTaskRefreshLocalMedia": "0",
        "ButlerTaskRefreshPeriodicMetadata": "0",
        "BackgroundQueueIdlePaused": "1",
        "AutoScan": "0"
    }

    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    url = f"http://localhost:32400/:/prefs?{query_string}"
    cmd = f"curl -s -X PUT '{url}' -H 'X-Plex-Token: {plex_token}'"

    result = docker_exec(ip, container, cmd, capture_output=True, check=False)

    if result.returncode == 0:
        print("   ✅ Tâches de fond désactivées")
        return True
    else:
        print(f"   ❌ Erreur: {result.stderr}")
        return False


def enable_music_analysis_only(ip, container, plex_token):
    """
    Active UNIQUEMENT les analyses musicales (Sonic/Loudness), tout le reste reste désactivé.
    Utilisé pour la phase de traitement Sonic en isolation.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        plex_token: Token d'authentification Plex

    Returns:
        bool: True si succès, False sinon
    """
    print("🎹 Activation exclusive des analyses musicales...")

    params = {
        "LoudnessAnalysisBehavior": "asap",
        "MusicAnalysisBehavior": "asap",
        "BackgroundQueueIdlePaused": "0",
        # Activer Deep Analysis (nécessaire pour Sonic)
        "ButlerTaskDeepAnalysis": "1",
        # Tout le reste reste à "never"
        "GenerateBIFBehavior": "never",
        "GenerateIntroMarkerBehavior": "never",
        "GenerateChapterBehavior": "never"
    }

    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    url = f"http://localhost:32400/:/prefs?{query_string}"
    cmd = f"curl -s -X PUT '{url}' -H 'X-Plex-Token: {plex_token}'"

    result = docker_exec(ip, container, cmd, capture_output=True, check=False)

    if result.returncode == 0:
        print("   ✅ Analyses musicales activées")

        # Déclencher le Butler pour lancer l'analyse Sonic immédiatement
        # Note (2026-01-23): Sans cet appel explicite, les préférences "asap"
        # n'ont aucun effet sur les items déjà présents. Le Butler scanne la
        # bibliothèque et crée les tâches d'analyse pour chaque piste.
        # Réf: forums.plex.tv/t/sonic-analysis-doesn-t-trigger
        butler_cmd = f"curl -s -X POST 'http://localhost:32400/butler/DeepMediaAnalysis' -H 'X-Plex-Token: {plex_token}'"
        butler_result = docker_exec(ip, container, butler_cmd, capture_output=True, check=False)
        if butler_result.returncode == 0:
            print("   ✅ Butler DeepMediaAnalysis déclenché")
        else:
            print(f"   ⚠️  Butler non déclenché: {butler_result.stderr}")

        return True
    else:
        print(f"   ❌ Erreur: {result.stderr}")
        return False


def enable_all_analysis(ip, container, plex_token):
    """
    Réactive toutes les analyses pour le clean-up final (Photos, Vidéos).

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        plex_token: Token d'authentification Plex

    Returns:
        bool: True si succès, False sinon
    """
    print("🔄 Réactivation de toutes les analyses...")

    params = {
        "GenerateBIFBehavior": "scheduled",
        "GenerateIntroMarkerBehavior": "scheduled",
        "GenerateChapterBehavior": "scheduled",
        "LoudnessAnalysisBehavior": "scheduled",
        "MusicAnalysisBehavior": "scheduled",
        "ButlerTaskDeepAnalysis": "1",
        "BackgroundQueueIdlePaused": "0"
    }

    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    url = f"http://localhost:32400/:/prefs?{query_string}"
    cmd = f"curl -s -X PUT '{url}' -H 'X-Plex-Token: {plex_token}'"

    result = docker_exec(ip, container, cmd, capture_output=True, check=False)

    if result.returncode == 0:
        print("   ✅ Toutes les analyses réactivées")
        return True
    else:
        print(f"   ❌ Erreur: {result.stderr}")
        return False


def collect_plex_logs(ip, container, output_dir="logs", prefix="plex", terminal_log=None, rclone_log=None, timestamp=None, keep_terminal_log=False):
    """
    Récupère les logs Plex pour debug, avec option d'inclure le log terminal et rclone.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        output_dir: Répertoire de destination des logs (défaut: logs/)
        prefix: Préfixe pour le nom d'archive
        terminal_log: Chemin vers le fichier log terminal (optionnel)
        rclone_log: Chemin vers le fichier log rclone (optionnel, sur l'hôte distant ou local)
        timestamp: Horodatage à utiliser (optionnel, généré si non fourni)
        keep_terminal_log: Si True, ne pas supprimer le fichier terminal après archivage
                          (utile si TeeLogger est encore actif)

    Returns:
        str: Chemin de l'archive ou None si échec
    """
    import tarfile
    import tempfile
    import shutil

    # Créer le dossier de destination s'il n'existe pas
    os.makedirs(output_dir, exist_ok=True)

    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"{timestamp}_logs_{prefix}.tar.gz"

    print(f"📋 Collecte des logs dans {output_dir}/...")

    if ip == 'localhost':
        archive_path = os.path.join(output_dir, archive_name)
    else:
        archive_path = f"/tmp/{archive_name}"

    # Copier tous les fichiers .log depuis le conteneur (collecte dynamique)
    log_path = "/config/Library/Application Support/Plex Media Server/Logs"
    tar_cmd = f"""docker exec {container} sh -c "cd '{log_path}' && tar -czf /tmp/plex_logs.tar.gz *.log 2>/dev/null || echo 'Aucun log trouvé'" """

    result = execute_command(ip, tar_cmd, check=False, capture_output=True)

    if ip == 'localhost':
        # Copier depuis le conteneur vers l'hôte
        execute_command(ip, f"docker cp {container}:/tmp/plex_logs.tar.gz {archive_path}", check=False)
    else:
        # Déplacer l'archive sur le remote
        execute_command(ip, f"mv /tmp/plex_logs.tar.gz {archive_path}", check=False)

    # Vérifier que l'archive Plex existe
    plex_archive_exists = False
    if ip == 'localhost':
        plex_archive_exists = os.path.exists(archive_path)
    else:
        check_result = execute_command(ip, f"test -f {archive_path} && echo 'OK'", capture_output=True, check=False)
        plex_archive_exists = 'OK' in check_result.stdout

    if not plex_archive_exists:
        print(f"   ⚠️  Échec de la collecte des logs Plex")
        # Si on a un terminal_log, on peut quand même créer une archive avec juste ce log
        if terminal_log and os.path.exists(terminal_log):
            terminal_only_archive = os.path.join(output_dir, f"{timestamp}_terminal_{prefix}.tar.gz")
            with tarfile.open(terminal_only_archive, 'w:gz') as tar:
                tar.add(terminal_log, arcname='terminal.log')
            # Supprimer le fichier terminal brut (maintenant dans l'archive)
            if not keep_terminal_log:
                os.remove(terminal_log)
            size_mb = os.path.getsize(terminal_only_archive) / (1024*1024)
            print(f"   ✅ Log terminal seul: {terminal_only_archive} ({size_mb:.1f} MB)")
            return terminal_only_archive
        return None

    # Vérifier si on a des logs supplémentaires à inclure
    has_terminal = terminal_log and os.path.exists(terminal_log)
    has_rclone = False
    local_rclone_log = None

    # Si rclone_log est spécifié, vérifier s'il existe (local ou remote)
    if rclone_log:
        if ip == 'localhost':
            has_rclone = os.path.exists(rclone_log)
            local_rclone_log = rclone_log if has_rclone else None
        else:
            # Vérifier si le fichier existe sur le remote
            check_result = execute_command(ip, f"test -f {rclone_log} && echo 'OK'", capture_output=True, check=False)
            has_rclone = 'OK' in check_result.stdout

    # Si pas de logs supplémentaires, retourner l'archive Plex seule
    if not has_terminal and not has_rclone:
        if ip == 'localhost':
            size_mb = os.path.getsize(archive_path) / (1024*1024)
            print(f"   ✅ Logs Plex collectés: {archive_path} ({size_mb:.1f} MB)")
        else:
            print(f"   ✅ Logs Plex collectés: {archive_path}")
        return archive_path

    # Créer une archive combinée (Plex + terminal + rclone)
    extras = []
    if has_terminal:
        extras.append("terminal")
    if has_rclone:
        extras.append("rclone")
    print(f"   📦 Création archive combinée (Plex + {' + '.join(extras)})...")

    combined_archive = os.path.join(output_dir, f"{timestamp}_logs_{prefix}_all.tar.gz")
    temp_dir = tempfile.mkdtemp(prefix="plex_logs_")

    try:
        # Extraire l'archive Plex dans un sous-dossier
        plex_logs_dir = os.path.join(temp_dir, "plex_logs")
        os.makedirs(plex_logs_dir)

        if ip == 'localhost':
            with tarfile.open(archive_path, 'r:gz') as tar:
                tar.extractall(plex_logs_dir)
        else:
            # Télécharger l'archive remote d'abord
            local_plex_archive = os.path.join(temp_dir, "plex_logs.tar.gz")
            execute_command(
                'localhost',
                f"scp -o StrictHostKeyChecking=no root@{ip}:{archive_path} {local_plex_archive}",
                check=False
            )
            if os.path.exists(local_plex_archive):
                with tarfile.open(local_plex_archive, 'r:gz') as tar:
                    tar.extractall(plex_logs_dir)

        # Si rclone_log est sur un remote, le télécharger
        if has_rclone and ip != 'localhost':
            local_rclone_log = os.path.join(temp_dir, "rclone.log")
            execute_command(
                'localhost',
                f"scp -o StrictHostKeyChecking=no root@{ip}:{rclone_log} {local_rclone_log}",
                check=False
            )
            if not os.path.exists(local_rclone_log):
                local_rclone_log = None
                has_rclone = False

        # Créer l'archive combinée
        with tarfile.open(combined_archive, 'w:gz') as tar:
            # Ajouter les logs Plex
            for item in os.listdir(plex_logs_dir):
                tar.add(os.path.join(plex_logs_dir, item), arcname=f"plex_logs/{item}")
            # Ajouter le log terminal
            if has_terminal:
                tar.add(terminal_log, arcname='terminal.log')
            # Ajouter le log rclone
            if has_rclone and local_rclone_log and os.path.exists(local_rclone_log):
                tar.add(local_rclone_log, arcname='rclone.log')

        # Supprimer l'archive Plex seule (remplacée par la combinée)
        if ip == 'localhost' and os.path.exists(archive_path):
            os.remove(archive_path)

        # Supprimer le fichier terminal brut (maintenant dans l'archive)
        if not keep_terminal_log and terminal_log and os.path.exists(terminal_log):
            os.remove(terminal_log)

        size_mb = os.path.getsize(combined_archive) / (1024*1024)
        print(f"   ✅ Archive combinée: {combined_archive} ({size_mb:.1f} MB)")
        return combined_archive

    except Exception as e:
        print(f"   ⚠️  Erreur archive combinée: {e}")
        # Fallback: retourner l'archive Plex seule
        if ip == 'localhost' and os.path.exists(archive_path):
            size_mb = os.path.getsize(archive_path) / (1024*1024)
            print(f"   ✅ Logs Plex (fallback): {archive_path} ({size_mb:.1f} MB)")
            return archive_path
        return None
    finally:
        # Nettoyage du répertoire temporaire
        shutil.rmtree(temp_dir, ignore_errors=True)


def stop_plex(ip, container='plex', timeout=30):
    """
    Arrête proprement le conteneur Plex avec timeout et fallback

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        timeout: Timeout en secondes avant force kill (défaut: 30)

    Cette fonction tente d'abord un arrêt gracieux avec timeout.
    Si ça échoue, elle force l'arrêt avec docker kill.
    """
    print(f"⏸️  Arrêt du conteneur {container}...")

    # Essayer stop avec timeout
    result = execute_command(
        ip,
        f"docker stop -t {timeout} {container}",
        check=False,
        capture_output=True
    )

    if result.returncode != 0:
        print(f"   ⚠️  Stop timeout, tentative de kill...")
        execute_command(ip, f"docker kill {container}", check=False)

    print(f"✅ Conteneur {container} arrêté")


def verify_rclone_mount_healthy_simple(ip, mount_point='/mnt/s3-media', timeout=30):
    """
    Vérifie que le montage rclone est fonctionnel (version simple sans lecture fichier).

    Cette version ne fait que les tests 1 et 2 (mountpoint + ls).
    Utilisée après un remontage pour éviter les faux négatifs avec MEGA.

    Args:
        ip: 'localhost' ou IP remote
        mount_point: Point de montage à vérifier
        timeout: Timeout en secondes pour le test d'accès

    Returns:
        dict: {
            'healthy': bool,
            'error': str|None,
            'response_time': float
        }
    """
    import time as time_module
    start = time_module.time()

    # Test 1: Vérifier que le point de montage existe
    result = execute_command(ip, f"mountpoint -q {mount_point}", check=False, capture_output=True)
    if result.returncode != 0:
        return {
            'healthy': False,
            'error': f"Point de montage {mount_point} non actif",
            'response_time': time_module.time() - start
        }

    # Test 2: Vérifier l'accès avec timeout (détecte les sockets morts)
    test_cmd = f"timeout {timeout} ls {mount_point} > /dev/null 2>&1"
    result = execute_command(ip, test_cmd, check=False, capture_output=True)

    response_time = time_module.time() - start

    if result.returncode == 124:  # Timeout
        return {
            'healthy': False,
            'error': f"Timeout ({timeout}s) - socket probablement déconnecté",
            'response_time': response_time
        }
    elif result.returncode != 0:
        return {
            'healthy': False,
            'error': f"Erreur d'accès au montage (code {result.returncode})",
            'response_time': response_time
        }

    return {
        'healthy': True,
        'error': None,
        'response_time': response_time
    }


def verify_rclone_mount_healthy(ip, mount_point='/mnt/s3-media', timeout=30):
    """
    Vérifie que le montage rclone est fonctionnel (pas de socket déconnecté).

    Effectue 3 tests progressifs:
    1. Vérification que le point de montage est actif
    2. Lecture du contenu du répertoire (metadata)
    3. Lecture réelle d'un fichier (détecte les I/O bloqués silencieusement)

    Args:
        ip: 'localhost' ou IP remote
        mount_point: Point de montage à vérifier
        timeout: Timeout en secondes pour le test d'accès (défaut: 30s pour MEGA)

    Returns:
        dict: {
            'healthy': bool,      # True si le montage est fonctionnel
            'error': str|None,    # Message d'erreur si échec
            'response_time': float # Temps de réponse en secondes
        }
    """
    import time as time_module
    start = time_module.time()

    # Test 1: Vérifier que le point de montage existe
    result = execute_command(ip, f"mountpoint -q {mount_point}", check=False, capture_output=True)
    if result.returncode != 0:
        return {
            'healthy': False,
            'error': f"Point de montage {mount_point} non actif",
            'response_time': time_module.time() - start
        }

    # Test 2: Vérifier l'accès avec timeout (détecte les sockets morts)
    test_cmd = f"timeout {timeout} ls {mount_point} > /dev/null 2>&1"
    result = execute_command(ip, test_cmd, check=False, capture_output=True)

    if result.returncode == 124:  # Timeout
        return {
            'healthy': False,
            'error': f"Timeout ({timeout}s) - socket probablement déconnecté",
            'response_time': time_module.time() - start
        }
    elif result.returncode != 0:
        return {
            'healthy': False,
            'error': f"Erreur d'accès au montage (code {result.returncode})",
            'response_time': time_module.time() - start
        }

    # Test 3: Lecture réelle d'un fichier (pas juste metadata)
    # Détecte les cas où ls passe mais l'I/O est bloqué silencieusement
    # Note: On limite la recherche à maxdepth 3 pour éviter un scan complet du bucket
    # et on cherche dans Music/ en priorité (répertoire le plus utilisé)
    test_read_cmd = f"""timeout {timeout} sh -c '
        # Essayer d abord dans Music (sous-répertoire courant)
        file=$(find {mount_point}/Music -maxdepth 3 -type f \\( -name "*.mp3" -o -name "*.flac" -o -name "*.m4a" \\) 2>/dev/null | head -1)
        # Fallback sur tout le mount si Music n existe pas
        if [ -z "$file" ]; then
            file=$(find {mount_point} -maxdepth 3 -type f \\( -name "*.mp3" -o -name "*.flac" -o -name "*.m4a" -o -name "*.mp4" -o -name "*.mkv" \\) 2>/dev/null | head -1)
        fi
        if [ -n "$file" ]; then
            head -c 100 "$file" > /dev/null 2>&1
            echo "OK: $file"
        else
            # Pas de fichier trouvé, mais le mount semble OK
            echo "OK: no_file_found"
        fi
    '"""
    result = execute_command(ip, test_read_cmd, check=False, capture_output=True)

    response_time = time_module.time() - start

    if result.returncode == 124:  # Timeout sur la lecture
        return {
            'healthy': False,
            'error': f"Timeout lecture fichier ({timeout}s) - I/O bloqué",
            'response_time': response_time
        }

    return {
        'healthy': True,
        'error': None,
        'response_time': response_time
    }


def remount_s3_if_needed(ip, rclone_remote, profile='lite', mount_point='/mnt/s3-media',
                         cache_dir=None, log_file=None, config_path=None, max_retries=3,
                         skip_lock=False):
    """
    Vérifie le montage rclone et remonte si nécessaire.

    Args:
        ip: 'localhost' ou IP remote
        rclone_remote: Nom du bucket/chemin S3
        profile: Profil de configuration
        mount_point: Point de montage
        cache_dir: Répertoire de cache rclone
        log_file: Fichier de logs rclone
        config_path: Chemin du fichier rclone.conf
        max_retries: Nombre max de tentatives de remontage
        skip_lock: Si True, ne pas acquérir le lock global (appelé depuis MountMonitor)

    Returns:
        bool: True si le montage est fonctionnel, False si échec après retries
    """
    # Importer le lock global du MountMonitor (import tardif pour éviter les dépendances circulaires)
    from common.mount_monitor import MountHealthMonitor
    global_lock = MountHealthMonitor.get_global_lock()

    # Vérification initiale (version simple pour éviter blocage sur FUSE mort)
    health = verify_rclone_mount_healthy_simple(ip, mount_point)
    if health['healthy']:
        return True

    print(f"⚠️  Montage rclone défaillant: {health['error']}")

    # Acquérir le lock global si nécessaire (évite les remontages concurrents)
    lock_acquired = False
    if not skip_lock:
        if not global_lock.acquire(blocking=False):
            print(f"⏳ Remontage déjà en cours par MountMonitor, attente...")
            # Attendre que le MountMonitor finisse son remontage
            global_lock.acquire(blocking=True)
            global_lock.release()
            # Re-vérifier après l'attente (version simple)
            health = verify_rclone_mount_healthy_simple(ip, mount_point)
            if health['healthy']:
                print(f"✅ Montage restauré par MountMonitor")
                return True
            # Si toujours pas OK, on continue avec notre propre remontage
            global_lock.acquire(blocking=True)
        lock_acquired = True

    try:
        # Auto-détection du cache_dir si non fourni
        if cache_dir is None:
            if ip == 'localhost':
                cache_dir = os.path.expanduser('~/tmp/rclone-cache')
            else:
                cache_dir = '/mnt/rclone-cache'

        for attempt in range(1, max_retries + 1):
            print(f"🔄 Tentative de remontage {attempt}/{max_retries}...")

            # Démontage forcé
            execute_command(ip, f"pkill -9 -f 'rclone mount.*{mount_point}' || true", check=False)
            time.sleep(3)
            execute_command(ip, f"fusermount3 -uz {mount_point} || true", check=False)
            execute_command(ip, f"fusermount -uz {mount_point} || true", check=False)
            time.sleep(2)

            # Nettoyage du cache VFS rclone (peut contenir des handles corrompus)
            print(f"   🧹 Nettoyage du cache rclone...")
            execute_command(ip, f"rm -rf {cache_dir}/vfs/* 2>/dev/null || true", check=False)

            # Cooldown pour laisser MEGA récupérer (augmente avec chaque tentative)
            cooldown = 10 * attempt  # 10s, 20s, 30s
            print(f"   ⏳ Cooldown {cooldown}s avant remontage...")
            time.sleep(cooldown)

            # Remontage
            try:
                mount_s3(ip, rclone_remote, profile=profile, mount_point=mount_point,
                         cache_dir=cache_dir, log_file=log_file, config_path=config_path)
            except Exception as e:
                print(f"   ❌ Erreur de remontage: {e}")
                continue

            # Vérification post-remontage (test simple: ls seulement, pas de lecture fichier)
            # Le test de lecture fichier est trop strict pour MEGA qui peut être lent
            print(f"   🔍 Vérification post-remontage...")
            time.sleep(10)  # Attendre que rclone soit vraiment prêt
            health = verify_rclone_mount_healthy_simple(ip, mount_point, timeout=30)
            if health['healthy']:
                print(f"   ✅ Remontage réussi (temps de réponse: {health['response_time']:.2f}s)")
                return True

            print(f"   ❌ Remontage échoué: {health['error']}")

        print(f"❌ Impossible de restaurer le montage après {max_retries} tentatives")
        return False

    finally:
        if lock_acquired:
            global_lock.release()


def ensure_mount_healthy(ip, rclone_remote, profile, mount_point, cache_dir, log_file, phase_name):
    """
    Vérifie que le montage rclone est fonctionnel avant une phase critique.
    Remonte automatiquement si nécessaire.

    Args:
        ip: 'localhost' ou IP remote
        rclone_remote: Nom du bucket S3
        profile: Profil rclone à utiliser
        mount_point: Point de montage S3
        cache_dir: Répertoire de cache rclone
        log_file: Fichier de logs rclone
        phase_name: Nom de la phase pour les logs

    Returns:
        bool: True si le montage est fonctionnel, False si échec
    """
    print(f"   🔍 Vérification du montage S3...", end=" ", flush=True)
    # Version simple (ls avec timeout) pour éviter les blocages sur FUSE mort
    health = verify_rclone_mount_healthy_simple(ip, mount_point)

    if health['healthy']:
        print(f"✅ ({health['response_time']:.1f}s)")
        return True

    print("❌")
    print(f"\n⚠️  Montage S3 défaillant avant {phase_name}: {health['error']}")
    print("🔄 Tentative de remontage automatique...")

    success = remount_s3_if_needed(
        ip,
        rclone_remote,
        profile=profile,
        mount_point=mount_point,
        cache_dir=cache_dir,
        log_file=log_file,
        max_retries=3
    )

    if success:
        print(f"✅ Montage restauré, poursuite de {phase_name}")
    else:
        print(f"❌ Impossible de restaurer le montage pour {phase_name}")

    return success


def verify_plex_pass_active(ip, container='plex', plex_token=None, timeout=120, check_interval=10):
    """
    Vérifie que le serveur Plex a un abonnement Plex Pass actif.

    L'analyse Sonic, intro detection, et autres features premium nécessitent
    un Plex Pass. Après un claim, il peut y avoir un délai de propagation.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur Plex
        plex_token: Token d'authentification (requis)
        timeout: Temps max d'attente en secondes (default: 120s)
        check_interval: Intervalle entre les vérifications (default: 10s)

    Returns:
        dict: {
            'active': bool,           # True si Plex Pass actif
            'username': str|None,     # Nom du compte
            'subscription': str|None, # Type d'abonnement
            'features': list,         # Features premium disponibles
            'error': str|None         # Message d'erreur si échec
        }
    """

    if not plex_token:
        return {
            'active': False,
            'username': None,
            'subscription': None,
            'features': [],
            'error': 'Token Plex requis pour vérifier le Pass'
        }

    print(f"🔐 Vérification du statut Plex Pass...")

    start_time = time.time()
    last_error = None

    while time.time() - start_time < timeout:
        try:
            # Appel API pour récupérer les infos du compte MyPlex
            api_cmd = f"curl -s 'http://localhost:32400/myplex/account' -H 'X-Plex-Token: {plex_token}'"
            result = docker_exec(ip, container, api_cmd, capture_output=True, check=False)

            if result.returncode != 0 or not result.stdout:
                last_error = "Impossible de contacter l'API Plex"
                elapsed = int(time.time() - start_time)
                print(f"   ⏳ Attente de la connexion à plex.tv... ({elapsed}s)")
                time.sleep(check_interval)
                continue

            response = result.stdout

            # Vérifier si le serveur est claimé
            if 'authToken' not in response and 'username' not in response:
                last_error = "Serveur non claimé ou non connecté à plex.tv"
                elapsed = int(time.time() - start_time)
                print(f"   ⏳ Attente du claim... ({elapsed}s)")
                time.sleep(check_interval)
                continue

            # Parser la réponse XML
            username_match = re.search(r'username="([^"]+)"', response)
            sub_active_match = re.search(r'subscriptionActive="([^"]+)"', response)
            sub_state_match = re.search(r'subscriptionState="([^"]+)"', response)
            sub_plan_match = re.search(r'subscriptionPlan="([^"]+)"', response)

            username = username_match.group(1) if username_match else None
            sub_active = sub_active_match.group(1) if sub_active_match else "0"
            sub_state = sub_state_match.group(1) if sub_state_match else "Unknown"
            sub_plan = sub_plan_match.group(1) if sub_plan_match else None

            # Extraire les features premium
            features = re.findall(r'<Feature id="([^"]+)"', response)

            # Vérifier le statut
            is_active = sub_active == "1"

            if is_active:
                print(f"   ✅ Plex Pass ACTIF")
                print(f"      Compte: {username}")
                print(f"      Plan: {sub_plan or sub_state}")

                # Afficher les features intéressantes pour l'analyse
                if features:
                    sonic_features = [f for f in features if any(
                        k in f.lower() for k in ['sonic', 'loudness', 'analysis', 'music']
                    )]
                    if sonic_features:
                        print(f"      Features audio: {', '.join(sonic_features[:3])}")

                return {
                    'active': True,
                    'username': username,
                    'subscription': sub_plan or sub_state,
                    'features': features,
                    'error': None
                }
            else:
                # Pass non actif - réessayer (propagation en cours ?)
                elapsed = int(time.time() - start_time)
                remaining = timeout - elapsed

                if remaining > check_interval:
                    print(f"   ⏳ Plex Pass non détecté, nouvel essai... ({elapsed}s/{timeout}s)")
                    time.sleep(check_interval)
                else:
                    # Timeout atteint
                    print(f"   ❌ Plex Pass INACTIF")
                    print(f"      Compte: {username}")
                    print(f"      État: {sub_state}")

                    return {
                        'active': False,
                        'username': username,
                        'subscription': sub_state,
                        'features': features,
                        'error': f"Abonnement Plex Pass non actif (état: {sub_state})"
                    }

        except Exception as e:
            last_error = str(e)
            print(f"   ⚠️  Erreur: {e}")
            time.sleep(check_interval)

    # Timeout global atteint
    print(f"   ❌ Timeout ({timeout}s) - impossible de vérifier")
    return {
        'active': False,
        'username': None,
        'subscription': None,
        'features': [],
        'error': last_error or f"Timeout ({timeout}s)"
    }
