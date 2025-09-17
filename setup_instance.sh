#!/bin/bash
# setup_instance.sh - Script cloud-init pour configurer l'instance
# Ce script est exécuté automatiquement au démarrage de l'instance

set -euo pipefail

echo "🚀 Configuration de l'instance pour Plex scan"

# 1. Mise à jour système
apt-get update
apt-get upgrade -y

# 2. Installation Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# 3. Installation Rclone
curl https://rclone.org/install.sh | sudo bash

# 4. Création des répertoires
mkdir -p /mnt/s3-media
mkdir -p /opt/plex_data/{config,transcode}
mkdir -p /root/.config/rclone

# 5. Installation d'outils utiles
apt-get install -y htop iotop wget curl jq

# 6. Pull de l'image Plex
docker pull plexinc/pms-docker:latest

echo "✅ Instance configurée et prête"
