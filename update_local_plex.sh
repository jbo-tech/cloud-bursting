#!/bin/bash
set -e # Arrête le script si une commande échoue

# === Configuration ===
PLEX_DATA_PATH="/var/lib/plexmediaserver/Library/Application Support/Plex Media Server" # Adaptez ce chemin !
ARCHIVE_NAME=$(ls -t plex_scan_*.tar.gz 2>/dev/null | head -n 1)

# === Vérifications ===
if [ -z "$ARCHIVE_NAME" ]; then
    echo "❌ Aucune archive 'plex_scan_*.tar.gz' trouvée dans le dossier courant."
    exit 1
fi

if [ ! -d "$PLEX_DATA_PATH" ]; then
    echo "❌ Le dossier de données Plex n'existe pas : $PLEX_DATA_PATH"
    echo "   Veuillez vérifier le chemin dans la variable PLEX_DATA_PATH."
    exit 1
fi

echo "📦 Archive détectée : $ARCHIVE_NAME"
echo "🎯 Chemin Plex local : $PLEX_DATA_PATH"
read -p "❓ Êtes-vous sûr de vouloir remplacer les données Plex locales ? (o/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Oo]$ ]]; then
    echo "🛑 Opération annulée."
    exit 1
fi

# === Déploiement ===
echo "---"
echo "1. ⏸️  Arrêt du service Plex..."
sudo systemctl stop plexmediaserver

echo "2. 💾  Sauvegarde des anciennes données..."
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
sudo mv "$PLEX_DATA_PATH/Plug-in Support/Databases" "$PLEX_DATA_PATH/Plug-in Support/Databases.bak.$TIMESTAMP"
sudo mv "$PLEX_DATA_PATH/Metadata" "$PLEX_DATA_PATH/Metadata.bak.$TIMESTAMP"
sudo mv "$PLEX_DATA_PATH/Media" "$PLEX_DATA_PATH/Media.bak.$TIMESTAMP"
echo "   ✅ Anciennes données renommées avec le suffixe .bak.$TIMESTAMP"

echo "3. 🚀  Extraction de la nouvelle archive..."
# On extrait directement dans le dossier parent pour recréer la structure
sudo tar -xzf "$ARCHIVE_NAME" -C "$PLEX_DATA_PATH/.."

echo "4. 🔐  Application des permissions..."
# Plex tourne souvent avec l'utilisateur 'plex'
sudo chown -R plex:plex "$PLEX_DATA_PATH/Plug-in Support/Databases"
sudo chown -R plex:plex "$PLEX_DATA_PATH/Metadata"
sudo chown -R plex:plex "$PLEX_DATA_PATH/Media"
echo "   ✅ Permissions appliquées pour l'utilisateur 'plex'."

echo "5. ▶️  Redémarrage du service Plex..."
sudo systemctl start plexmediaserver

sleep 10 # Laisser un peu de temps à Plex pour démarrer
sudo systemctl status plexmediaserver --no-pager

echo "---"
echo "🎉 Mise à jour terminée avec succès !"
