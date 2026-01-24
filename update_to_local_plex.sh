#!/bin/bash
set -e

# ============================================================================
# update_to_local_plex.sh - Import des métadonnées Plex depuis une archive cloud
#
# Ce script importe les données Plex (bases de données, métadonnées, médias)
# depuis une archive générée par automate_scan.py ou automate_delta_sync.py.
#
# Usage:
#   ./update_to_local_plex.sh [plex_data_path] [archive.tar.gz] [-y]
#
# Arguments:
#   plex_data_path  Chemin vers "Plex Media Server" (défaut: /var/lib/plexmediaserver/...)
#   archive.tar.gz  Archive à importer (défaut: détection auto)
#   -y              Mode non-interactif (skip confirmation)
#
# Exemples:
#   ./update_to_local_plex.sh                                    # Tout auto
#   ./update_to_local_plex.sh /custom/path                       # Chemin custom
#   ./update_to_local_plex.sh /custom/path archive.tar.gz -y     # Tout explicite
# ============================================================================

# === ARGUMENTS ===
PLEX_DATA_PATH="${1:-/var/lib/plexmediaserver/Library/Application Support/Plex Media Server}"
ARCHIVE_NAME="$2"
AUTO_CONFIRM="$3"

# === DÉTECTION ARCHIVE ===
if [ -z "$ARCHIVE_NAME" ]; then
    # Cherche plex_metadata_* ou plex_delta_sync_* (le plus récent)
    ARCHIVE_NAME=$(ls -t plex_metadata_*.tar.gz plex_delta_sync_*.tar.gz 2>/dev/null | head -n 1)
fi

# === VÉRIFICATIONS ===
if [ -z "$ARCHIVE_NAME" ]; then
    echo "❌ Aucune archive trouvée."
    echo "   Patterns recherchés : plex_metadata_*.tar.gz, plex_delta_sync_*.tar.gz"
    echo ""
    echo "Usage: $0 [plex_data_path] [archive.tar.gz] [-y]"
    exit 1
fi

if [ ! -f "$ARCHIVE_NAME" ]; then
    echo "❌ Archive introuvable : $ARCHIVE_NAME"
    exit 1
fi

if [ ! -d "$PLEX_DATA_PATH" ]; then
    echo "❌ Chemin Plex invalide : $PLEX_DATA_PATH"
    echo ""
    echo "Usage: $0 [plex_data_path] [archive.tar.gz] [-y]"
    exit 1
fi

# === RÉSUMÉ ===
echo "============================================================"
echo "IMPORT MÉTADONNÉES PLEX"
echo "============================================================"
echo "📦 Archive : $ARCHIVE_NAME"
echo "🎯 Chemin Plex : $PLEX_DATA_PATH"
echo "============================================================"

# === CONFIRMATION ===
if [[ "$AUTO_CONFIRM" != "-y" ]]; then
    read -p "❓ Remplacer les données Plex locales ? (o/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Oo]$ ]]; then
        echo "🛑 Opération annulée."
        exit 1
    fi
fi

# === DÉPLOIEMENT ===
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

echo ""
echo "1. ⏸️  Arrêt du service Plex..."
sudo systemctl stop plexmediaserver

echo ""
echo "2. 💾 Création backup local..."
BACKUP_ARCHIVE="plex_local_backup_${TIMESTAMP}.tar.gz"
# Backup des dossiers existants (ignore les erreurs si dossiers absents)
if sudo tar -czf "$BACKUP_ARCHIVE" -C "$PLEX_DATA_PATH/.." \
    "Plex Media Server/Plug-in Support/Databases" \
    "Plex Media Server/Metadata" \
    "Plex Media Server/Media" 2>/dev/null; then
    echo "   ✅ Backup créé : $BACKUP_ARCHIVE"
else
    echo "   ⚠️  Backup partiel ou vide (certains dossiers absents)"
fi

echo ""
echo "3. 🗑️  Suppression des anciennes données..."
sudo rm -rf "$PLEX_DATA_PATH/Plug-in Support/Databases"
sudo rm -rf "$PLEX_DATA_PATH/Metadata"
sudo rm -rf "$PLEX_DATA_PATH/Media"
echo "   ✅ Anciennes données supprimées"

echo ""
echo "4. 🚀 Extraction de l'archive..."
sudo tar -xzf "$ARCHIVE_NAME" -C "$PLEX_DATA_PATH/.."
echo "   ✅ Archive extraite"

echo ""
echo "5. 🔐 Application des permissions..."
sudo chown -R plex:plex "$PLEX_DATA_PATH/Plug-in Support/Databases"
sudo chown -R plex:plex "$PLEX_DATA_PATH/Metadata"
sudo chown -R plex:plex "$PLEX_DATA_PATH/Media"
echo "   ✅ Permissions appliquées (plex:plex)"

echo ""
echo "6. ▶️  Redémarrage du service Plex..."
sudo systemctl start plexmediaserver

echo ""
echo "⏳ Attente démarrage Plex (10s)..."
sleep 10
sudo systemctl status plexmediaserver --no-pager

echo ""
echo "============================================================"
echo "✅ Import terminé avec succès"
echo "============================================================"
echo "💾 Backup disponible : $BACKUP_ARCHIVE"
echo ""
