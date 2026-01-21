#!/bin/bash
# export_zimaboard_db.sh - Exporte la DB Plex du ZimaBoard
# Usage: ./export_zimaboard_db.sh [--with-metadata]
#
# À exécuter SUR le ZimaBoard (via SSH)

set -euo pipefail

# === CONFIGURATION ===
# Adapter ce chemin selon ton installation
PLEX_BASE="/mnt/smallfeet/DATA/AppData/plex/config/Library/Application Support/Plex Media Server"
OUTPUT_DIR="/tmp"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# === PARSING ARGUMENTS ===
WITH_METADATA=false
if [[ "${1:-}" == "--with-metadata" ]]; then
    WITH_METADATA=true
fi

# === VÉRIFICATIONS ===
echo "🔍 Vérification de l'environnement..."

# Vérifier que Plex est arrêté (important pour éviter corruption DB)
if docker ps --format '{{.Names}}' | grep -q plex; then
    echo "⚠️  Le conteneur Plex semble tourner."
    echo "   Arrêtez-le d'abord: docker stop plex"
    exit 1
fi

# Vérifier que les dossiers existent
DB_PATH="${PLEX_BASE}/Plug-in Support/Databases"
METADATA_PATH="${PLEX_BASE}/Metadata"

if [[ ! -d "$DB_PATH" ]]; then
    echo "❌ Dossier DB introuvable: $DB_PATH"
    exit 1
fi

echo "✅ Plex arrêté, dossiers accessibles"

# === AFFICHER LES TAILLES ===
echo ""
echo "📊 Tailles actuelles:"
du -sh "$DB_PATH"
if [[ "$WITH_METADATA" == "true" ]]; then
    du -sh "$METADATA_PATH"
fi

# === CRÉATION DE L'ARCHIVE ===
if [[ "$WITH_METADATA" == "true" ]]; then
    ARCHIVE_NAME="plex_db_metadata_${TIMESTAMP}.tar.gz"
    echo ""
    echo "📦 Création de l'archive COMPLÈTE (DB + Metadata)..."
    echo "   ⏳ Cela peut prendre plusieurs minutes..."
    
    tar -czf "${OUTPUT_DIR}/${ARCHIVE_NAME}" \
        -C "$PLEX_BASE" \
        "Plug-in Support/Databases" \
        "Metadata"
else
    ARCHIVE_NAME="plex_db_only_${TIMESTAMP}.tar.gz"
    echo ""
    echo "📦 Création de l'archive DB seule..."
    echo "   (Utilisez --with-metadata pour inclure les artwork)"
    
    tar -czf "${OUTPUT_DIR}/${ARCHIVE_NAME}" \
        -C "$PLEX_BASE" \
        "Plug-in Support/Databases"
fi

# === RÉSULTAT ===
ARCHIVE_PATH="${OUTPUT_DIR}/${ARCHIVE_NAME}"
ARCHIVE_SIZE=$(du -h "$ARCHIVE_PATH" | cut -f1)

echo ""
echo "✅ Archive créée avec succès!"
echo ""
echo "📦 Fichier : $ARCHIVE_PATH"
echo "📏 Taille  : $ARCHIVE_SIZE"
echo ""
echo "🔗 Pour récupérer l'archive sur votre machine de dev:"
echo "   scp jbo@zimaboard:${ARCHIVE_PATH} ./"
echo ""
echo "🧹 Pour nettoyer après transfert:"
echo "   rm ${ARCHIVE_PATH}"
