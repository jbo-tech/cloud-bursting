#!/bin/bash
# ============================================================================
# export_plex_db.sh - Exporte la DB Plex locale
#
# Crée une archive de la base de données Plex (et optionnellement les métadonnées)
# pour injection dans une instance cloud ou backup.
#
# À exécuter SUR le serveur Plex local.
#
# Usage:
#   ./export_plex_db.sh [plex_data_path] [--with-metadata]
#
# Arguments:
#   plex_data_path   Chemin vers "Plex Media Server" (défaut: /var/lib/plexmediaserver/...)
#   --with-metadata  Inclure le dossier Metadata (artwork, etc.)
#
# Exemples:
#   ./export_plex_db.sh                              # DB seule, chemin standard
#   ./export_plex_db.sh --with-metadata              # DB + Metadata
#   ./export_plex_db.sh /custom/path --with-metadata # Chemin custom
# ============================================================================

set -euo pipefail

# === PARSING ARGUMENTS ===
PLEX_BASE=""
WITH_METADATA=false

for arg in "$@"; do
    if [[ "$arg" == "--with-metadata" ]]; then
        WITH_METADATA=true
    elif [[ -z "$PLEX_BASE" ]]; then
        PLEX_BASE="$arg"
    fi
done

# Valeur par défaut si non spécifié
if [[ -z "$PLEX_BASE" ]]; then
    PLEX_BASE="/var/lib/plexmediaserver/Library/Application Support/Plex Media Server"
fi

# === CONFIGURATION ===
OUTPUT_DIR="/tmp"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# === VÉRIFICATIONS ===
echo "============================================================"
echo "EXPORT BASE DE DONNÉES PLEX"
echo "============================================================"
echo "🎯 Chemin Plex : $PLEX_BASE"
echo "📦 Metadata    : $WITH_METADATA"
echo "============================================================"
echo ""

echo "🔍 Vérification de l'environnement..."

# Vérifier que le dossier Plex existe
if [[ ! -d "$PLEX_BASE" ]]; then
    echo "❌ Dossier Plex introuvable: $PLEX_BASE"
    exit 1
fi

# Vérifier que Plex est arrêté (important pour éviter corruption DB)
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q plex; then
    echo "⚠️  Le conteneur Plex semble tourner."
    echo "   Arrêtez-le d'abord: docker stop plex"
    exit 1
fi

if systemctl is-active --quiet plexmediaserver 2>/dev/null; then
    echo "⚠️  Le service Plex semble tourner."
    echo "   Arrêtez-le d'abord: sudo systemctl stop plexmediaserver"
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
echo "============================================================"
echo "✅ EXPORT TERMINÉ"
echo "============================================================"
echo "📦 Fichier : $ARCHIVE_PATH"
echo "📏 Taille  : $ARCHIVE_SIZE"
echo ""
echo "🔗 Pour récupérer l'archive sur votre machine:"
echo "   scp $(whoami)@$(hostname):${ARCHIVE_PATH} ./"
echo ""
echo "🧹 Pour nettoyer après transfert:"
echo "   rm ${ARCHIVE_PATH}"
echo ""
