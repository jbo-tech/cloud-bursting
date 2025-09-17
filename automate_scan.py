#!/usr/bin/env python3
"""
automate_scan.py - Orchestre tout le processus de scan Plex dans le cloud
Usage: python3 automate_scan.py
"""

import os
import sys
import json
import time
import subprocess
from datetime import datetime
from pathlib import Path

# Configuration depuis l'environnement
CONFIG = {
    "instance_type": os.getenv("INSTANCE_TYPE", "DEV1-S"),
    "zone": os.getenv("SCW_DEFAULT_ZONE", "fr-par-1"),
    "s3_bucket": os.getenv("S3_BUCKET", "media-bucket"),
    "plex_version": os.getenv("PLEX_VERSION", "latest"),
    "zimaboard_ip": os.getenv("ZIMABOARD_IP", "192.168.1.100"),
}

class PlexCloudScanner:
    def __init__(self):
        self.instance_id = None
        self.instance_ip = None
        self.start_time = time.time()
        
    def log(self, message):
        """Afficher un message avec timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")
    
    def run_command(self, cmd, capture=False):
        """Exécuter une commande shell"""
        if capture:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return result.stdout.strip()
        else:
            return subprocess.run(cmd, shell=True, check=True)
    
    def create_instance(self):
        """Créer l'instance Scaleway avec cloud-init"""
        self.log("🚀 Création de l'instance Scaleway...")
        
        # Lire le script cloud-init
        with open("setup_instance.sh", "r") as f:
            user_data = f.read()
        
        # Créer l'instance avec cloud-init
        cmd = f"""
        scw instance server create \
            type={CONFIG['instance_type']} \
            image=ubuntu_jammy \
            name=plex-scanner-{datetime.now():%Y%m%d-%H%M%S} \
            cloud-init='{user_data}' \
            --output=json
        """
        
        result = self.run_command(cmd, capture=True)
        data = json.loads(result)
        self.instance_id = data['id']
        
        self.log(f"✅ Instance créée: {self.instance_id}")
        return self.instance_id
    
    def wait_for_instance(self):
        """Attendre que l'instance soit prête"""
        self.log("⏳ Attente du démarrage de l'instance...")
        
        for i in range(30):  # Max 5 minutes
            # Récupérer le statut
            cmd = f"scw instance server get {self.instance_id} --output=json"
            result = self.run_command(cmd, capture=True)
            data = json.loads(result)
            
            if data['state'] == 'running' and data.get('public_ip'):
                self.instance_ip = data['public_ip']['address']
                
                # Tester SSH
                ssh_test = f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@{self.instance_ip} echo ready"
                if subprocess.run(ssh_test, shell=True, capture_output=True).returncode == 0:
                    self.log(f"✅ Instance prête: {self.instance_ip}")
                    return True
            
            time.sleep(10)
        
        raise Exception("Timeout: l'instance n'est pas prête")
    
    def setup_rclone(self):
        """Transférer et configurer rclone"""
        self.log("📤 Configuration de rclone...")
        
        # Copier rclone.conf
        scp_cmd = f"scp -o StrictHostKeyChecking=no rclone.conf root@{self.instance_ip}:/root/.config/rclone/"
        self.run_command(scp_cmd)
        
        # Monter S3
        mount_cmd = f"""
        ssh -o StrictHostKeyChecking=no root@{self.instance_ip} '
            # Créer le point de montage
            mkdir -p /mnt/s3-media
            
            # Monter avec rclone
            rclone mount mega-s4:{CONFIG['s3_bucket']} /mnt/s3-media \
                --daemon \
                --vfs-cache-mode full \
                --vfs-cache-max-size 20G \
                --buffer-size 256M \
                --allow-other
            
            # Vérifier le montage
            sleep 5
            if mountpoint -q /mnt/s3-media; then
                echo "✅ S3 monté avec succès"
                ls -la /mnt/s3-media | head -5
            else
                echo "❌ Erreur de montage S3"
                exit 1
            fi
        '
        """
        self.run_command(mount_cmd)
        
        self.log("✅ S3 monté")
    
    def start_plex(self):
        """Lancer Plex et déclencher le scan"""
        self.log("🎬 Démarrage de Plex...")
        
        plex_cmd = f"""
        ssh -o StrictHostKeyChecking=no root@{self.instance_ip} '
            # Lancer Plex
            docker run -d \
                --name plex \
                --network=host \
                -e PLEX_UID=1000 \
                -e PLEX_GID=1000 \
                -e TZ=Europe/Paris \
                -v /opt/plex_data/config:/config \
                -v /opt/plex_data/transcode:/transcode \
                -v /mnt/s3-media:/Media:ro \
                plexinc/pms-docker:{CONFIG['plex_version']}
            
            # Attendre le démarrage
            sleep 30
            
            # Vérifier que Plex est accessible
            if curl -s http://localhost:32400/web > /dev/null; then
                echo "✅ Plex accessible"
            else
                echo "⚠️ Plex non accessible"
            fi
            
            # Déclencher le scan (si token disponible)
            # Note: Configuration manuelle via l'interface web si pas de token
        '
        """
        self.run_command(plex_cmd)
        
        self.log("✅ Plex démarré")
        self.log(f"🌐 Interface web: http://{self.instance_ip}:32400/web")
    
    def monitor_scan(self):
        """Surveiller la progression du scan"""
        self.log("📊 Surveillance du scan...")
        self.log("(Cela peut prendre plusieurs heures pour 9 To)")
        
        last_activity = time.time()
        no_activity_count = 0
        
        while True:
            # Vérifier l'activité de scan
            check_cmd = f"""
            ssh -o StrictHostKeyChecking=no root@{self.instance_ip} '
                # Compter les processus de scan
                SCANNERS=$(docker exec plex pgrep -c "Plex Media Scan" 2>/dev/null || echo 0)
                
                # Taille de la base de données
                DB_SIZE=$(du -h /opt/plex_data/config/Library/Application\\ Support/Plex\\ Media\\ Server/Plug-in\\ Support/Databases/*.db 2>/dev/null | cut -f1)
                
                echo "SCANNERS:$SCANNERS"
                echo "DB_SIZE:$DB_SIZE"
            '
            """
            
            output = self.run_command(check_cmd, capture=True)
            
            # Parser la sortie
            scanners = 0
            for line in output.split('\n'):
                if 'SCANNERS:' in line:
                    scanners = int(line.split(':')[1])
                if 'DB_SIZE:' in line:
                    db_size = line.split(':')[1]
                    self.log(f"  Scanners actifs: {scanners} | DB: {db_size}")
            
            # Vérifier si le scan est terminé
            if scanners == 0:
                no_activity_count += 1
                if no_activity_count >= 10:  # 5 minutes sans activité
                    self.log("✅ Scan terminé (aucune activité depuis 5 minutes)")
                    break
            else:
                no_activity_count = 0
                last_activity = time.time()
            
            # Timeout de sécurité (6 heures)
            if time.time() - self.start_time > 21600:
                self.log("⚠️ Timeout atteint (6 heures)")
                break
            
            time.sleep(30)
    
    def export_data(self):
        """Exporter et télécharger la base de données Plex"""
        self.log("💾 Export des données...")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"plex_metadata_{timestamp}.tar.gz"
        
        export_cmd = f"""
        ssh -o StrictHostKeyChecking=no root@{self.instance_ip} '
            # Arrêter Plex pour cohérence
            docker stop plex
            
            # Créer l'archive
            cd /opt/plex_data/config/Library/Application\\ Support/Plex\\ Media\\ Server/
            tar czf /tmp/{archive_name} \
                --exclude="Cache/*" \
                --exclude="Logs/*" \
                Plug-in\\ Support/Databases/ \
                Metadata/ \
                Media/ \
                Preferences.xml
            
            # Afficher la taille
            ls -lh /tmp/{archive_name}
        '
        """
        self.run_command(export_cmd)
        
        # Télécharger l'archive
        self.log("📥 Téléchargement de l'archive...")
        scp_cmd = f"scp -o StrictHostKeyChecking=no root@{self.instance_ip}:/tmp/{archive_name} ./"
        self.run_command(scp_cmd)
        
        self.log(f"✅ Archive téléchargée: {archive_name}")
        return archive_name
    
    def destroy_instance(self):
        """Détruire l'instance pour stopper la facturation"""
        if self.instance_id:
            self.log("🧹 Destruction de l'instance...")
            cmd = f"scw instance server delete {self.instance_id} force=true"
            self.run_command(cmd)
            self.log("✅ Instance détruite - facturation arrêtée")
    
    def calculate_cost(self):
        """Calculer le coût estimé"""
        duration_hours = (time.time() - self.start_time) / 3600
        
        # Prix approximatifs Scaleway
        prices = {
            "DEV1-S": 0.01,
            "DEV1-L": 0.04,
            "GP1-M": 0.16,
        }
        
        price_per_hour = prices.get(CONFIG['instance_type'], 0.20)
        return round(duration_hours * price_per_hour, 2)
    
    def run(self):
        """Exécuter le workflow complet"""
        self.log("=" * 50)
        self.log("🚀 DÉMARRAGE DU SCAN PLEX CLOUD")
        self.log(f"Instance: {CONFIG['instance_type']}")
        self.log(f"Bucket: {CONFIG['s3_bucket']}")
        self.log("=" * 50)
        
        try:
            # Workflow complet
            self.create_instance()
            self.wait_for_instance()
            self.setup_rclone()
            self.start_plex()
            self.monitor_scan()
            archive = self.export_data()
            
            # Résumé
            self.log("=" * 50)
            self.log("✅ SCAN TERMINÉ AVEC SUCCÈS")
            self.log(f"📦 Archive: {archive}")
            self.log(f"⏱️ Durée: {(time.time() - self.start_time) / 60:.1f} minutes")
            self.log(f"💰 Coût estimé: {self.calculate_cost()}€")
            self.log("=" * 50)
            
            self.log("\n👉 Prochaine étape:")
            self.log(f"   Copier {archive} sur votre ZimaBoard et l'importer dans Plex")
            
        except Exception as e:
            self.log(f"❌ Erreur: {e}")
            
        finally:
            self.destroy_instance()

def main():
    # Vérifier les prérequis
    if not os.path.exists("rclone.conf"):
        print("❌ Fichier rclone.conf manquant")
        sys.exit(1)
    
    if not os.path.exists("setup_instance.sh"):
        print("❌ Fichier setup_instance.sh manquant")
        sys.exit(1)
    
    # Lancer le scan
    scanner = PlexCloudScanner()
    scanner.run()

if __name__ == "__main__":
    main()
