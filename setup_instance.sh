#!/bin/bash
# setup_instance.sh - Script cloud-init pour configurer l'instance
# Ce script est exécuté automatiquement au démarrage de l'instance

# Stoppe le script en cas d'erreur
set -euo pipefail

# --- Logging robuste ---
# Redirige toutes les sorties (stdout et stderr) vers un fichier de log
# pour un débogage facile.
exec > >(tee /var/log/cloud-init-output.log|logger -t user-data -s 2>/dev/console) 2>&1

echo "🚀 Démarrage de la configuration de l'instance..."

# --- Forcer le mode non-interactif ---
export DEBIAN_FRONTEND=noninteractive

# 1. Mise à jour système
echo "Mise à jour du système..."
apt-get update
apt-get upgrade -y

# 2. Installation des dépendances et outils utiles
echo "Installation des dépendances (unzip, docker, outils...)"
apt-get install -y htop iotop wget curl jq unzip fuse3 sqlite3

# 2b. Configuration de FUSE pour permettre à rclone + Docker de fonctionner
echo "Configuration de FUSE (user_allow_other)..."
if ! grep -q "^user_allow_other" /etc/fuse.conf; then
    echo "user_allow_other" >> /etc/fuse.conf
    echo "✅ user_allow_other activé dans /etc/fuse.conf"
else
    echo "✅ user_allow_other déjà activé"
fi

# 3. Installation Docker
echo "Installation de Docker..."
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# 4. Installation Rclone
echo "Installation de Rclone..."
curl https://rclone.org/install.sh | sudo bash

# 5. Création des répertoires
echo "Création des répertoires..."
mkdir -p /mnt/s3-media
mkdir -p /mnt/rclone-cache
mkdir -p /opt/plex_data/{config,transcode}

# 6. Correction des permissions
echo "Application des permissions pour Plex..."
# Le conteneur Plex a besoin d'écrire dans ce dossier.
# 777 est acceptable pour une instance temporaire et jetable.
chmod -R 777 /opt/plex_data

# 7. Pull de l'image Plex (optimisation)
echo "Pré-chargement de l'image Docker Plex..."
docker pull plexinc/pms-docker:latest

echo "✅ Instance configurée et prête."
