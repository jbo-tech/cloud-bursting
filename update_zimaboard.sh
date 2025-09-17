#!/bin/bash
# update_zimaboard.sh - Appliquer la DB sur le ZimaBoard

source .env

if [ $# -ne 1 ]; then
    echo "Usage: $0 plex_metadata_XXXXXX.tar.gz"
    exit 1
fi

ARCHIVE=$1

echo "📤 Copie vers ZimaBoard..."
scp $ARCHIVE user@${ZIMABOARD_IP}:/tmp/

echo "🔄 Application de la nouvelle DB..."
ssh user@${ZIMABOARD_IP} << EOF
    # Arrêter Plex
    docker stop plex  # ou systemctl stop plexmediaserver
    
    # Backup de l'ancienne DB
    cd "${PLEX_CONFIG_PATH}/Library/Application Support/Plex Media Server/"
    tar czf ~/plex_backup_$(date +%Y%m%d).tar.gz Plug-in\\ Support/Databases/
    
    # Extraire la nouvelle
    tar xzf /tmp/$ARCHIVE
    
    # Redémarrer Plex
    docker start plex  # ou systemctl start plexmediaserver
    
    echo "✅ Mise à jour terminée"
EOF
