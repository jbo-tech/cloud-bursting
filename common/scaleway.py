#!/usr/bin/env python3
"""
scaleway.py - Gestion de l'infrastructure Scaleway

Ce module gère le cycle de vie des instances Scaleway:
- Création d'instances avec cloud-init
- Attente de l'initialisation (SSH, cloud-init)
- Destruction complète (instance + IP + volumes)

Fonctions principales:
- create_instance()      : Crée une instance Scaleway
- destroy_instance()     : Détruit l'instance et toutes ses ressources
- wait_ssh_ready()       : Attend que SSH soit accessible
- wait_cloud_init()      : Attend que cloud-init termine
"""

import os
import subprocess
import time
import json

from .config import load_env
from .executor import execute_command, read_state_file, write_state_file

# === FICHIERS D'ÉTAT ===
INSTANCE_ID_FILE = ".current_instance_id"
INSTANCE_IP_FILE = ".current_instance_ip"

# === MAPPING PROFILS D'INSTANCES ===
INSTANCE_PROFILES = {
    'lite': {
        'type': 'DEV1-S',
        'volume': '20G',
        'description': '2 vCPU, 2GB RAM - Tests uniquement'
    },
    'standard': {
        'type': 'DEV1-M',
        'volume': '40G',
        'description': '3 vCPU, 4GB RAM - Petites bibliothèques'
    },
    'power': {
        'type': 'GP1-S',
        'volume': '100G',
        'description': '8 vCPU, 16GB RAM - Bibliothèques moyennes'
    },
    'superpower': {
        'type': 'GP1-M',
        'volume': '100G',
        'description': '8 vCPU, 32GB RAM - Grosses bibliothèques + Sonic'
    },
}


# === GESTION DES INSTANCES ===

def create_instance(env, profile):
    """
    Crée une instance Scaleway avec cloud-init

    Args:
        env: Dictionnaire des variables d'environnement
        profile: Profil d'instance ('lite', 'standard', 'power', 'superpower')

    Returns:
        str: IP publique de l'instance créée

    Raises:
        RuntimeError: Si la création échoue
        FileNotFoundError: Si le script cloud-init est introuvable
    """
    print("\n" + "=" * 60)
    print("🚀 CRÉATION INSTANCE SCALEWAY")
    print("=" * 60)

    instance_config = INSTANCE_PROFILES[profile]
    instance_type = instance_config['type']
    volume_size = instance_config['volume']

    print(f"Profil      : {profile}")
    print(f"Type        : {instance_type}")
    print(f"Volume      : {volume_size}")
    print(f"Zone        : {env.get('SCW_DEFAULT_ZONE', 'fr-par-1')}")
    print("=" * 60)

    instance_name = f"plex-scanner-{int(time.time())}"

    # Vérifier le script cloud-init
    cloud_init_path = "./setup_instance.sh"
    if not os.path.exists(cloud_init_path):
        raise FileNotFoundError(f"Script cloud-init introuvable: {cloud_init_path}")

    # Créer l'instance avec cloud-init via fichier (compatible toutes versions scw)
    create_cmd = [
        "scw", "instance", "server", "create",
        f"type={instance_type}",
        f"zone={env.get('SCW_DEFAULT_ZONE', 'fr-par-1')}",
        f"name={instance_name}",
        "image=debian_bookworm",
        f"root-volume=l:{volume_size}",
        f"cloud-init=@{cloud_init_path}",  # Syntaxe compatible scw 2.42+
        "-w",  # Wait for server to be ready
        "-o", "json"
    ]

    print("⏳ Création en cours (peut prendre 1-2 minutes)...")

    result = subprocess.run(create_cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"❌ Erreur: {result.stderr}")
        raise RuntimeError("Échec de la création de l'instance")

    instance_data = json.loads(result.stdout)
    instance_id = instance_data['id']
    instance_ip = instance_data['public_ip']['address']

    # Sauvegarder l'état
    write_state_file(INSTANCE_ID_FILE, instance_id)
    write_state_file(INSTANCE_IP_FILE, instance_ip)

    print(f"✅ Instance créée: {instance_id}")
    print(f"📍 IP: {instance_ip}")

    return instance_ip


def wait_ssh_ready(ip, timeout=120):
    """
    Attend que SSH soit accessible sur l'instance

    Args:
        ip: IP de l'instance
        timeout: Timeout en secondes (défaut: 120)

    Raises:
        TimeoutError: Si SSH n'est pas accessible après timeout
    """
    print(f"\n⏳ Attente de SSH sur {ip}...")

    start_time = time.time()

    while time.time() - start_time < timeout:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             "-o", "ConnectTimeout=5", f"root@{ip}", "echo ok"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            print("✅ SSH accessible")
            return True

        time.sleep(5)

    raise TimeoutError(f"SSH non accessible après {timeout}s")


def wait_cloud_init(ip, timeout=600):
    """
    Attend que cloud-init ait fini d'initialiser l'instance

    Args:
        ip: IP de l'instance
        timeout: Timeout en secondes (défaut: 600)

    Raises:
        TimeoutError: Si cloud-init ne termine pas avant timeout
    """
    print("\n⏳ Attente de cloud-init...")

    start_time = time.time()

    while time.time() - start_time < timeout:
        result = execute_command(
            ip,
            "[ -f /var/lib/cloud/instance/boot-finished ] && echo 'ready' || echo 'waiting'",
            check=False,
            capture_output=True
        )

        if result.returncode == 0 and 'ready' in result.stdout:
            print("✅ Cloud-init terminé")
            return True

        elapsed = int(time.time() - start_time)
        if elapsed % 30 == 0:
            print(f"   ⏳ Attente... ({elapsed}s)")

        time.sleep(10)

    raise TimeoutError(f"Cloud-init n'a pas terminé dans les {timeout}s")


def destroy_instance():
    """
    Détruit l'instance Scaleway et toutes ses ressources associées

    Cette fonction:
    - Inspecte l'instance avant suppression
    - Arrête l'instance proprement
    - Supprime l'instance + IP publique + volumes locaux
    - Nettoie les fichiers d'état
    - Affiche un rapport détaillé de la suppression

    Note: L'opération est irréversible. En cas d'échec, des instructions
          pour vérification manuelle sont affichées.
    """
    print("\n" + "=" * 60)
    print("🗑️  DESTRUCTION INSTANCE ET RESSOURCES")
    print("=" * 60)

    instance_id = read_state_file(INSTANCE_ID_FILE)

    if not instance_id:
        print("ℹ️  Aucune instance à détruire")
        return

    # Récupérer les infos de l'instance AVANT suppression
    print(f"🔍 Inspection de l'instance: {instance_id}")
    inspect_result = subprocess.run(
        ["scw", "instance", "server", "get", instance_id, "-o", "json"],
        capture_output=True,
        text=True
    )

    if inspect_result.returncode == 0:
        try:
            instance_data = json.loads(inspect_result.stdout)
            print(f"   📛 Nom       : {instance_data.get('name', 'N/A')}")
            print(f"   🖥️  Type      : {instance_data.get('commercial_type', 'N/A')}")
            print(f"   🌍 Zone      : {instance_data.get('zone', 'N/A')}")

            # IP publique
            public_ip = instance_data.get('public_ip')
            if public_ip:
                ip_id = public_ip.get('id', 'N/A')
                ip_address = public_ip.get('address', 'N/A')
                print(f"   🌐 IP        : {ip_address} (ID: {ip_id})")

            # Volumes
            volumes = instance_data.get('volumes', {})
            if volumes:
                print(f"   💾 Volumes   : {len(volumes)} volume(s)")
                for vol_key, vol_data in volumes.items():
                    vol_id = vol_data.get('id', 'N/A')
                    vol_size = vol_data.get('size', 0) // (1024**3)  # Convertir en GB
                    print(f"      • {vol_key}: {vol_size}GB (ID: {vol_id})")
        except (json.JSONDecodeError, KeyError) as e:
            print(f"   ⚠️  Impossible de parser les détails: {e}")

    # Arrêter l'instance
    print("\n⏸️  Arrêt de l'instance...")
    stop_result = subprocess.run(
        ["scw", "instance", "server", "stop", instance_id, "--wait"],
        capture_output=True,
        text=True
    )

    if stop_result.returncode == 0:
        print("   ✅ Instance arrêtée")
    else:
        print(f"   ⚠️  Arrêt échoué (peut être déjà arrêtée): {stop_result.stderr.strip()}")

    # Supprimer l'instance + IP + volumes locaux
    print("\n🗑️  Suppression complète (instance + IP + volumes)...")
    print("   ⚠️  Cette opération est IRRÉVERSIBLE")
    delete_result = subprocess.run(
        ["scw", "instance", "server", "delete", instance_id, "with-ip=true", "with-volumes=local"],
        capture_output=True,
        text=True
    )

    if delete_result.returncode == 0:
        print("\n" + "=" * 60)
        print("✅ DESTRUCTION COMPLÈTE RÉUSSIE")
        print("=" * 60)
        print("   ✅ Instance supprimée")
        print("   ✅ IP publique libérée")
        print("   ✅ Volumes locaux supprimés")
        print("   💰 Facturation ARRÊTÉE")

        # Nettoyer les fichiers d'état
        if os.path.exists(INSTANCE_ID_FILE):
            os.remove(INSTANCE_ID_FILE)
        if os.path.exists(INSTANCE_IP_FILE):
            os.remove(INSTANCE_IP_FILE)
        print("   ✅ Fichiers d'état nettoyés")

    else:
        print("\n" + "=" * 60)
        print("⚠️  ERREUR LORS DE LA SUPPRESSION")
        print("=" * 60)
        print(f"Erreur: {delete_result.stderr.strip()}")
        print("")
        print("🔍 VÉRIFICATION MANUELLE REQUISE:")
        print(f"   1. Console web: https://console.scaleway.com/instance/servers")
        print(f"   2. CLI: scw instance server list | grep {instance_id[:8]}")
        print(f"   3. Suppression manuelle: scw instance server delete {instance_id} with-ip=true with-volumes=local")
        print("")
        print("⚠️  Si l'instance existe encore, elle CONTINUE À FACTURER !")

def test_mega_bandwidth(ip, test_path="Music", timeout=120):
    """
    Test la connectivité et bande passante S3 en 2 étapes.

    Étape 1: Test de connectivité (rclone lsf)
    Étape 2: Test de bande passante (télécharger 1 fichier)

    Args:
        ip: IP de l'instance
        test_path: Chemin relatif dans le bucket
        timeout: Timeout en secondes pour le téléchargement

    Returns:
        dict: {'success': bool, 'speed_mbps': float, 'error': str}
    """
    print("\n" + "=" * 60)
    print("🧪 TEST CONNECTIVITÉ S3")
    print("=" * 60)

    env = load_env()
    bucket = env.get('S3_BUCKET', 'media-center')
    remote = f"mega-s4:{bucket}/{test_path}"

    print(f"   Remote : {remote}")

    # Étape 1: Test connectivité (lister les fichiers)
    print("\n   📋 Étape 1: Test de connectivité...")
    list_cmd = f"timeout 30 rclone --config /tmp/rclone.conf lsf '{remote}' --max-depth 1 2>&1 | head -5"
    list_result = execute_command(ip, list_cmd, capture_output=True, check=False)

    if list_result.returncode != 0 or not list_result.stdout.strip():
        print(f"   ❌ Impossible de lister {remote}")
        print(f"   Output: {list_result.stdout[:200]}")
        return {'success': False, 'speed_mbps': 0, 'error': 'listing_failed'}

    files = list_result.stdout.strip().split('\n')
    print(f"   ✅ Connectivité OK ({len(files)} éléments listés)")

    # Étape 2: Test bande passante (télécharger 1 fichier)
    print(f"\n   📥 Étape 2: Test de bande passante (timeout={timeout}s)...")

    # Trouver un fichier audio à télécharger
    find_file_cmd = f"""
    rclone --config /tmp/rclone.conf lsf '{remote}' --recursive --files-only \
        --include '*.flac' --include '*.mp3' 2>/dev/null | head -1
    """
    file_result = execute_command(ip, find_file_cmd, capture_output=True, check=False)
    test_file = file_result.stdout.strip()

    if not test_file:
        print("   ⚠️  Aucun fichier audio trouvé pour le test")
        return {'success': True, 'speed_mbps': 0, 'error': 'no_test_file'}

    print(f"   Fichier test: {test_file[:60]}...")

    # Télécharger le fichier (--ignore-checksum car on teste juste la bande passante)
    download_cmd = f"""
    rm -rf /tmp/mega-test 2>/dev/null
    mkdir -p /tmp/mega-test
    START=$(date +%s.%N)
    timeout {timeout} rclone --config /tmp/rclone.conf copy \
        --ignore-checksum --retries 1 \
        '{remote}/{test_file}' /tmp/mega-test/ 2>/tmp/rclone-test.log
    RC=$?
    END=$(date +%s.%N)
    SIZE=$(du -sb /tmp/mega-test 2>/dev/null | cut -f1 || echo 0)
    DURATION=$(echo "$END - $START" | bc)
    echo "rc=$RC"
    echo "size=$SIZE"
    echo "duration=$DURATION"
    """

    dl_result = execute_command(ip, download_cmd, capture_output=True, check=False)

    # Parser les résultats
    rc, size_bytes, duration = 0, 0, 0
    for line in dl_result.stdout.split('\n'):
        if line.startswith('rc='):
            rc = int(line.split('=')[1])
        elif line.startswith('size='):
            try:
                size_bytes = int(line.split('=')[1])
            except:
                size_bytes = 0
        elif line.startswith('duration='):
            try:
                duration = float(line.split('=')[1])
            except:
                duration = 0

    if rc == 0 and size_bytes > 0 and duration > 0:
        speed_mbps = (size_bytes * 8) / (duration * 1_000_000)
        print(f"\n   📊 Résultats:")
        print(f"      Téléchargé : {size_bytes / (1024*1024):.1f} MB")
        print(f"      Durée      : {duration:.1f}s")
        print(f"      Débit      : {speed_mbps:.1f} Mbps")

        if speed_mbps > 5:
            print(f"\n   ✅ S3 fonctionne correctement!")
            return {'success': True, 'speed_mbps': speed_mbps, 'error': None}
        else:
            print(f"\n   ⚠️  Débit faible ({speed_mbps:.1f} Mbps)")
            return {'success': True, 'speed_mbps': speed_mbps, 'error': 'low_bandwidth'}

    # Cas rc=0 mais size ou duration manquant
    if rc == 0:
        check_cmd = "ls -la /tmp/mega-test/ 2>/dev/null | head -5"
        check_result = execute_command(ip, check_cmd, capture_output=True, check=False)
        print(f"\n   ⚠️  Téléchargement terminé (rc=0) mais métriques incomplètes")
        print(f"      size={size_bytes}, duration={duration}")
        print(f"   Contenu: {check_result.stdout[:200]}")
        # Considérer comme succès si rc=0
        return {'success': True, 'speed_mbps': 0, 'error': 'incomplete_metrics'}

    # Échec du téléchargement (rc != 0)
    print(f"\n   ⚠️  Téléchargement échoué (rc={rc})")

    # Afficher les logs d'erreur
    log_cmd = "tail -10 /tmp/rclone-test.log 2>/dev/null"
    log_result = execute_command(ip, log_cmd, capture_output=True, check=False)
    if log_result.stdout.strip():
        print(f"   Log: {log_result.stdout[:300]}")

    # Connectivité OK mais téléchargement KO = avertissement seulement
    return {'success': False, 'speed_mbps': 0, 'error': 'download_failed'}
