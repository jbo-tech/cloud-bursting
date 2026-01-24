#!/bin/bash
set -e

# ============================================================================
# update_to_distant_plex.sh - Déploiement des métadonnées Plex vers serveur distant
#
# Script orchestrateur qui s'exécute depuis le poste local et pilote l'import
# des métadonnées Plex sur un serveur distant via SSH.
#
# Workflow:
#   1. Détecte l'archive locale (ou utilise celle fournie)
#   2. Transfère l'archive vers le serveur distant via SCP
#   3. Transfère update_to_local_plex.sh
#   4. Exécute l'import sur le serveur distant via SSH
#   5. Nettoie les fichiers temporaires
#
# Variables d'environnement (OBLIGATOIRES):
#   PLEX_REMOTE_HOST  Connexion SSH (ex: user@hostname)
#   PLEX_REMOTE_PATH  Chemin Plex sur le serveur distant
#
# Usage:
#   export PLEX_REMOTE_HOST="user@server"
#   export PLEX_REMOTE_PATH="/var/lib/plexmediaserver/Library/Application Support/Plex Media Server"
#   ./update_to_distant_plex.sh [archive.tar.gz]
#
# Exemples:
#   ./update_to_distant_plex.sh                              # Auto-détection archive
#   ./update_to_distant_plex.sh plex_metadata_20240115.tar.gz
# ============================================================================

# === CONFIGURATION ===
REMOTE_TMP="/tmp"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# === VALIDATION VARIABLES D'ENVIRONNEMENT ===
if [ -z "$PLEX_REMOTE_HOST" ]; then
    echo "❌ Variable PLEX_REMOTE_HOST non définie."
    echo ""
    echo "Usage:"
    echo "   export PLEX_REMOTE_HOST=\"user@server\""
    echo "   export PLEX_REMOTE_PATH=\"/path/to/Plex Media Server\""
    echo "   $0 [archive.tar.gz]"
    exit 1
fi

if [ -z "$PLEX_REMOTE_PATH" ]; then
    echo "❌ Variable PLEX_REMOTE_PATH non définie."
    echo ""
    echo "Usage:"
    echo "   export PLEX_REMOTE_HOST=\"user@server\""
    echo "   export PLEX_REMOTE_PATH=\"/path/to/Plex Media Server\""
    echo "   $0 [archive.tar.gz]"
    exit 1
fi

# === ARGUMENTS ===
ARCHIVE_PATH="$1"

# === DÉTECTION ARCHIVE ===
if [ -z "$ARCHIVE_PATH" ]; then
    # Auto-détection de la dernière archive
    ARCHIVE_PATH=$(ls -t plex_metadata_*.tar.gz plex_delta_sync_*.tar.gz 2>/dev/null | head -n 1)
fi

# === VÉRIFICATIONS ===
if [ -z "$ARCHIVE_PATH" ]; then
    echo "❌ Aucune archive trouvée."
    echo "   Patterns recherchés : plex_metadata_*.tar.gz, plex_delta_sync_*.tar.gz"
    echo ""
    echo "Usage: $0 [archive.tar.gz]"
    exit 1
fi

if [ ! -f "$ARCHIVE_PATH" ]; then
    echo "❌ Archive introuvable : $ARCHIVE_PATH"
    exit 1
fi

ARCHIVE_NAME=$(basename "$ARCHIVE_PATH")

# === RÉSUMÉ ===
echo "============================================================"
echo "DÉPLOIEMENT VERS SERVEUR DISTANT"
echo "============================================================"
echo "📦 Archive locale : $ARCHIVE_PATH"
echo "🖥️  Hôte distant  : $PLEX_REMOTE_HOST"
echo "🎯 Chemin Plex    : $PLEX_REMOTE_PATH"
echo "============================================================"
echo ""

# === ÉTAPE 1: TEST CONNEXION ===
echo "1. 🔌 Test connexion SSH..."
if ! ssh -o ConnectTimeout=10 "$PLEX_REMOTE_HOST" "echo 'OK'" > /dev/null 2>&1; then
    echo "   ❌ Impossible de se connecter à $PLEX_REMOTE_HOST"
    echo "   Vérifiez que SSH est configuré et que le serveur est accessible."
    exit 1
fi
echo "   ✅ Connexion établie"

# === ÉTAPE 2: TRANSFERT ARCHIVE ===
echo ""
echo "2. 📤 Transfert de l'archive..."
ARCHIVE_SIZE=$(du -h "$ARCHIVE_PATH" | cut -f1)
echo "   Taille : $ARCHIVE_SIZE"
scp "$ARCHIVE_PATH" "$PLEX_REMOTE_HOST:$REMOTE_TMP/"
echo "   ✅ Archive transférée"

# === ÉTAPE 3: TRANSFERT SCRIPT ===
echo ""
echo "3. 📤 Transfert du script d'import..."
scp "$SCRIPT_DIR/update_to_local_plex.sh" "$PLEX_REMOTE_HOST:$REMOTE_TMP/"
ssh "$PLEX_REMOTE_HOST" "chmod +x $REMOTE_TMP/update_to_local_plex.sh"
echo "   ✅ Script transféré"

# === ÉTAPE 4: EXÉCUTION IMPORT ===
echo ""
echo "4. 🚀 Lancement de l'import sur le serveur distant..."
echo "============================================================"
# Exécution avec mode non-interactif (-y)
ssh -t "$PLEX_REMOTE_HOST" "cd $REMOTE_TMP && ./update_to_local_plex.sh '$PLEX_REMOTE_PATH' '$ARCHIVE_NAME' -y"
echo "============================================================"

# === ÉTAPE 5: NETTOYAGE ===
echo ""
echo "5. 🧹 Nettoyage fichiers temporaires..."
ssh "$PLEX_REMOTE_HOST" "rm -f $REMOTE_TMP/$ARCHIVE_NAME $REMOTE_TMP/update_to_local_plex.sh"
echo "   ✅ Fichiers temporaires supprimés"

# === RÉSULTAT ===
echo ""
echo "============================================================"
echo "✅ DÉPLOIEMENT TERMINÉ"
echo "============================================================"
echo ""
echo "Le serveur distant utilise maintenant les métadonnées de : $ARCHIVE_NAME"
echo ""
