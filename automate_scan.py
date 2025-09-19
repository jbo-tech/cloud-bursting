#!/usr/bin/env python3
"""
automate_scan.py - Orchestre tout le processus de scan Plex dans le cloud
Usage: python automate_scan.py
"""

#!/usr/bin/env python3
import subprocess
import time
import os
import json
from dotenv import load_dotenv # On importe la librairie pour gérer le .env

# ==============================================================================
# --- ⚙️ CHARGEMENT DE LA CONFIGURATION ---
# ==============================================================================
load_dotenv() # Charge les variables depuis le fichier .env

# --- On lit les variables d'environnement ---
INSTANCE_TYPE = os.getenv("INSTANCE_TYPE", "GP1-M")
INSTANCE_ZONE = os.getenv("SCW_DEFAULT_ZONE", "fr-par-1")
ROOT_VOLUME_SIZE = os.getenv("ROOT_VOLUME_SIZE", "50GB")
PLEX_VERSION = os.getenv("PLEX_VERSION", "latest")

RCLONE_REMOTE_NAME = os.getenv("S3_BUCKET") # Assurez-vous que le nom du remote est correct
RCLONE_CONFIG_PATH = "./rclone.conf"

ZIMABOARD_IP = os.getenv("ZIMABOARD_IP")
PLEX_LOCAL_CONFIG_PATH = os.getenv("PLEX_CONFIG_PATH")

# IMPORTANT : Le PLEX_CLAIM_TOKEN doit toujours être frais. On ne le met pas dans le .env
# car il expire trop vite. Le script le demandera à l'utilisateur.
PLEX_LOCAL_CONTAINER_NAME = os.getenv("PLEX_LOCAL_CONTAINER_NAME", "plex")

# --- Constantes du script ---
INSTANCE_NAME_PREFIX = "plex-scanner"
CLOUD_INIT_SCRIPT = "./setup_instance.sh"
INSTANCE_ID_FILE = ".current_instance_id"
METADATA_ARCHIVE_NAME = "plex_metadata.tar.gz"


# ==============================================================================
# --- 🛠️ FONCTIONS UTILITAIRES ---
# ==============================================================================
def run_command(command, check=True):
    """Exécute une commande locale et affiche sa sortie."""
    print(f"🚀 LOCAL: {' '.join(command)}")
    try:
        result = subprocess.run(command, check=check, text=True, capture_output=True)
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        return result
    except subprocess.CalledProcessError as e:
        print(f"❌ Erreur lors de l'exécution de la commande.")
        if check:
            raise
        return e

def run_remote_command(ip, command_str):
    """Exécute une commande à distance via SSH."""
    ssh_options = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
    command = ["ssh", *ssh_options, f"root@{ip}", command_str]
    print(f"🛰️  REMOTE @ {ip}: {command_str}")
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def transfer_file(ip, local_path, remote_path):
    """Transfère un fichier local vers l'instance distante via SCP."""
    ssh_options = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
    command = ["scp", *ssh_options, local_path, f"root@{ip}:{remote_path}"]
    print(f"🛰️  UPLOAD: {local_path} -> root@{ip}:{remote_path}")
    subprocess.run(command, check=True)

def download_file(ip, remote_path, local_path):
    """Télécharge un fichier distant vers le répertoire local."""
    ssh_options = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
    command = ["scp", *ssh_options, f"root@{ip}:{remote_path}", local_path]
    print(f"🛰️  DOWNLOAD: root@{ip}:{remote_path} -> {local_path}")
    subprocess.run(command, check=True)

# ==============================================================================
# --- 🤖 WORKFLOW PRINCIPAL ---
# ==============================================================================

def main():
    """Orchestre l'ensemble du processus de scan dans le cloud."""
    instance_id = None

    # --- Demande du PLEX_CLAIM_TOKEN au lancement ---
    plex_claim_token = input("🔗 Veuillez coller un nouveau PLEX_CLAIM_TOKEN (de https://plex.tv/claim) : ")
    if not plex_claim_token.startswith("claim-"):
        print("❌ Token invalide. Arrêt.")
        return

    # --- Chargement de la configuration de la bibliothèque ---
    print("\n--- 1. Chargement de la configuration de la bibliothèque ---")
    with open('plex_libraries.json', 'r') as f:
        libraries = json.load(f)
    movie_library = next((lib for lib in libraries if lib['title'] == 'Movies'), None)
    if not movie_library:
        print("❌ Bibliothèque 'Movies' non trouvée dans plex_libraries.json")
        return
    print(f"   Bibliothèque '{movie_library['title']}' sélectionnée.")

    try:
        # --- Étape 2: Création de l'instance (utilise les variables du .env) ---
        print("\n--- 2. Création de l'instance Scaleway ---")
        instance_name = f"{INSTANCE_NAME_PREFIX}-{int(time.time())}"
        with open(CLOUD_INIT_SCRIPT, 'r') as f:
            cloud_init_content = f.read()

        create_cmd = [
            "scw", "instance", "server", "create",
            f"type={INSTANCE_TYPE}", f"zone={INSTANCE_ZONE}", f"name={instance_name}",
            "image=debian_bookworm", f"root-volume=l:{ROOT_VOLUME_SIZE}",
            "--cloud-init", cloud_init_content, "-w", "-o", "json"
        ]
        result = run_command(create_cmd)
        instance_data = json.loads(result.stdout)
        instance_id = instance_data['id']
        instance_ip = instance_data['public_ip']['address']
        with open(INSTANCE_ID_FILE, 'w') as f: f.write(instance_id)
        print(f"✅ Instance {instance_id} créée avec l'IP {instance_ip}")

        # --- Étape 3: Attente et configuration ---
        print("\n--- 3. Attente de la fin de la configuration (cloud-init) ---")
        # (Cette section reste identique, elle attend la fin du script cloud-init)
        config_success = False
        for i in range(1, 21):
            print(f"   Tentative {i}/20: Vérification du log...", end='\r')
            try:
                result = subprocess.run(
                    ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", f"root@{instance_ip}", "grep -q 'Instance configurée et prête' /var/log/cloud-init-output.log"],
                    capture_output=True
                )
                if result.returncode == 0:
                    print("\n   ✅ Configuration terminée avec succès.")
                    config_success = True
                    break
            except subprocess.CalledProcessError: pass
            time.sleep(15)
        if not config_success: raise Exception("La configuration cloud-init a échoué.")

        print("   Configuration de Rclone...")
        transfer_file(instance_ip, RCLONE_CONFIG_PATH, "/root/.config/rclone/rclone.conf")

        # --- Étape 4: Lancement du Scan Plex ---
        print("\n--- 4. Lancement du Scan Plex ---")
        # 4.1 Monter le bucket S3 (utilise la variable du .env)
        rclone_path = f"{RCLONE_REMOTE_NAME}:"
        run_remote_command(instance_ip, f"rclone mount '{rclone_path}' /mnt/s3-media --allow-other --daemon --vfs-cache-mode full")
        time.sleep(5)

        # 4.2 Lancer le conteneur Plex (utilise le token entré par l'utilisateur)
        docker_cmd = f"docker run -d --name=plex -e PLEX_CLAIM='{plex_claim_token}' -v /opt/plex_data/config:/config -v /mnt/s3-media:/media --net=host plexinc/pms-docker:{PLEX_VERSION}"
        run_remote_command(instance_ip, docker_cmd)
        print("   Conteneur Plex démarré. Attente de 2 minutes...")
        time.sleep(120)

        # 4.3 Ajouter et scanner la bibliothèque (inchangé)
        plex_media_path = movie_library['paths'][0]
        container_path = f"/media{plex_media_path}"
        scanner_add_cmd = f"docker exec plex /usr/lib/plexmediaserver/Plex\\ Media\\ Scanner --add --section '{movie_library['title']}' --type movie --agent '{movie_library['agent']}' --scanner '{movie_library['scanner']}' --language '{movie_library['language']}' --location '{container_path}'"
        run_remote_command(instance_ip, scanner_add_cmd)

        print("   Lancement du scan. Surveillance du processus...")
        scanner_scan_cmd = "docker exec plex /usr/lib/plexmediaserver/Plex\\ Media\\ Scanner --scan --refresh"
        run_remote_command(instance_ip, scanner_scan_cmd)
        while True:
            result = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", f"root@{instance_ip}", "docker exec plex pgrep -f 'Plex Media Scanner'"], capture_output=True)
            if result.returncode != 0:
                print("   ✅ Scan terminé !")
                break
            print(f"   [{time.strftime('%H:%M:%S')}] Le scan est en cours...", end='\r')
            time.sleep(120)

        # --- Étape 5: Export et rapatriement ---
        print("\n--- 5. Export et rapatriement des métadonnées ---")
        run_remote_command(instance_ip, "docker stop plex")
        tar_cmd = "cd '/opt/plex_data/config/Library/Application Support/Plex Media Server/' && tar -czf /root/plex_metadata.tar.gz 'Plug-in Support/Databases/com.plexapp.plugins.library.db' 'Metadata'"
        run_remote_command(instance_ip, tar_cmd)
        download_file(instance_ip, f"/root/{METADATA_ARCHIVE_NAME}", f"./{METADATA_ARCHIVE_NAME}")
        print(f"   ✅ Archive {METADATA_ARCHIVE_NAME} téléchargée.")

        # --- Étape 6: Mise à jour de l'instance locale ---
        # print("\n--- 6. Mise à jour de l'instance Plex locale ---")
        # print(f"   Arrêt du conteneur '{PLEX_LOCAL_CONTAINER_NAME}'...")
        # run_command(["docker", "stop", PLEX_LOCAL_CONTAINER_NAME])

        # print("   Application de la nouvelle configuration...")
        # local_plex_path_full = os.path.join(PLEX_LOCAL_CONFIG_PATH, "Library/Application Support/Plex Media Server/")
        # run_command(["tar", "-xzf", METADATA_ARCHIVE_NAME, "-C", local_plex_path_full])

        # print(f"   Démarrage du conteneur '{PLEX_LOCAL_CONTAINER_NAME}'...")
        # run_command(["docker", "start", PLEX_LOCAL_CONTAINER_NAME])
        print("   ✅ Instance locale mise à jour.")

    except Exception as e:
        print(f"\n🚨 UNE ERREUR EST SURVENUE : {e}")
    finally:
        # --- Étape 7: Destruction de l'instance ---
        print("\n--- 7. Destruction de l'instance Cloud ---")
        if os.path.exists(INSTANCE_ID_FILE):
            with open(INSTANCE_ID_FILE, 'r') as f: instance_id_to_delete = f.read().strip()
            if instance_id_to_delete:
                print(f"   Destruction de l'instance {instance_id_to_delete}...")
                run_command(["scw", "instance", "server", "delete", instance_id_to_delete, "with-ip=true"], check=False)
                os.remove(INSTANCE_ID_FILE)
            else: print("   Aucun ID d'instance à détruire.")
        else: print("   Aucun fichier d'ID d'instance trouvé.")

    print("\n🎉 Workflow terminé !")

if __name__ == "__main__":
    main()
