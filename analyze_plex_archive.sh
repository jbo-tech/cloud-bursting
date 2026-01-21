#!/bin/bash
# analyze_plex_archive.sh - Analyse complète d'une archive Plex
# Supporte: Films, Séries, Musique, Photos

set -e

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BOLD}📊 Analyse de l'archive Plex${NC}"
echo "=============================="

# Vérifier l'argument
if [ $# -eq 0 ]; then
    echo "Usage: $0 <archive.tar.gz ou dossier>"
    exit 1
fi

INPUT="$1"
TEMP_DIR=""
ARCHIVE_SIZE=""

# Capturer la taille de l'archive avant décompression
if [ -f "$INPUT" ]; then
    ARCHIVE_SIZE=$(ls -lh "$INPUT" | awk '{print $5}')
fi

# Déterminer si c'est une archive ou un dossier
if [ -f "$INPUT" ]; then
    echo "📦 Décompression de l'archive..."
    TEMP_DIR=$(mktemp -d /tmp/plex_analyze_XXXXXX)
    tar -xzf "$INPUT" -C "$TEMP_DIR" 2>/dev/null || {
        echo -e "${RED}❌ Erreur lors de la décompression${NC}"
        rm -rf "$TEMP_DIR"
        exit 1
    }
    WORK_DIR="$TEMP_DIR"
    echo "   Extrait dans: $TEMP_DIR"
elif [ -d "$INPUT" ]; then
    WORK_DIR="$INPUT"
else
    echo -e "${RED}❌ $INPUT n'est ni un fichier ni un dossier${NC}"
    exit 1
fi

cd "$WORK_DIR"

# Trouver la structure Plex
if [ ! -d "Plug-in Support" ]; then
    PLUGIN_DIR=$(find . -type d -name "Plug-in Support" -print -quit)
    if [ -n "$PLUGIN_DIR" ]; then
        cd "$(dirname "$PLUGIN_DIR")"
    else
        echo -e "${RED}❌ Structure Plex non trouvée${NC}"
        [ -n "$TEMP_DIR" ] && rm -rf "$TEMP_DIR"
        exit 1
    fi
fi

DB_PATH="Plug-in Support/Databases/com.plexapp.plugins.library.db"

# === TAILLE ARCHIVE (si applicable) ===
if [ -n "$ARCHIVE_SIZE" ]; then
    echo -e "\n${BOLD}📦 Archive:${NC} $ARCHIVE_SIZE (compressé)"
fi

# === BASES DE DONNÉES ===
echo -e "\n${BOLD}💾 Bases de données:${NC}"
if [ -d "Plug-in Support/Databases" ]; then
    # DB principales (sans suffixe de date)
    echo -e "   ${CYAN}Fichiers actuels:${NC}"
    ls -lh "Plug-in Support/Databases/"*.db 2>/dev/null | grep -v '\-20[0-9][0-9]-' | sed 's/^/      /'
    CURRENT_DB=$(du -ch "Plug-in Support/Databases/"*.db 2>/dev/null | grep -v '\-20[0-9][0-9]-' | tail -1 | cut -f1)
    echo -e "      ${GREEN}Sous-total: $CURRENT_DB${NC}"

    # Backups datés
    BACKUP_COUNT=$(ls "Plug-in Support/Databases/"*-20[0-9][0-9]-* 2>/dev/null | wc -l)
    if [ "$BACKUP_COUNT" -gt 0 ]; then
        echo -e "\n   ${CYAN}Backups datés:${NC} $BACKUP_COUNT fichiers"
        BACKUP_SIZE=$(du -ch "Plug-in Support/Databases/"*-20[0-9][0-9]-* 2>/dev/null | tail -1 | cut -f1)
        echo -e "      Taille: $BACKUP_SIZE"
    fi

    # Dossier restore
    if [ -d "Plug-in Support/Databases/restore" ]; then
        RESTORE_SIZE=$(du -sh "Plug-in Support/Databases/restore" 2>/dev/null | cut -f1)
        echo -e "\n   ${CYAN}Dossier restore/:${NC} $RESTORE_SIZE"
    fi

    # Music Analysis
    if [ -d "Plug-in Support/Databases/Music Analysis 1" ]; then
        SONIC_SIZE=$(du -sh "Plug-in Support/Databases/Music Analysis 1" 2>/dev/null | cut -f1)
        echo -e "\n   ${CYAN}Music Analysis (Sonic):${NC} $SONIC_SIZE"
    fi

    # Taille totale
    TOTAL_DB=$(du -sh "Plug-in Support/Databases" 2>/dev/null | cut -f1)
    echo -e "\n   ${BOLD}Total dossier Databases: ${GREEN}$TOTAL_DB${NC}"
else
    echo -e "   ${RED}❌ Pas de base de données trouvée${NC}"
fi

# === ANALYSE SQL ===
SQLITE_AVAILABLE=false
TOTAL_ITEMS=0
SONIC_RATIO=0

if ! command -v sqlite3 &> /dev/null; then
    echo -e "\n${YELLOW}⚠️  sqlite3 non installé${NC}"
    echo "   Pour des stats détaillées: sudo apt install sqlite3 (Linux) ou brew install sqlite (macOS)"
    echo "   → Diagnostic basé uniquement sur les bundles"
elif [ ! -f "$DB_PATH" ]; then
    echo -e "\n${YELLOW}⚠️  Base de données introuvable: $DB_PATH${NC}"
else
    SQLITE_AVAILABLE=true

    # --- Bibliothèques ---
    echo -e "\n${BOLD}📚 Bibliothèques configurées:${NC}"
    sqlite3 "$DB_PATH" "
        SELECT
            id,
            name,
            CASE section_type
                WHEN 1 THEN 'Movie'
                WHEN 2 THEN 'TV Show'
                WHEN 8 THEN 'Music'
                WHEN 13 THEN 'Photo'
                ELSE 'Type ' || section_type
            END as type
        FROM library_sections
        ORDER BY id;
    " 2>/dev/null | while IFS='|' read id name type; do
        echo -e "   ${CYAN}[$id]${NC} $name (${BLUE}$type${NC})"
    done

    # --- Statistiques globales par type ---
    echo -e "\n${BOLD}📈 Contenu par type de média:${NC}"

    # Films
    MOVIES=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE metadata_type = 1;" 2>/dev/null || echo "0")
    [ "$MOVIES" -gt 0 ] && echo -e "   🎬 Films: ${GREEN}$MOVIES${NC}"

    # Séries
    SHOWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE metadata_type = 2;" 2>/dev/null || echo "0")
    SEASONS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE metadata_type = 3;" 2>/dev/null || echo "0")
    EPISODES=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE metadata_type = 4;" 2>/dev/null || echo "0")
    if [ "$SHOWS" -gt 0 ]; then
        echo -e "   📺 Séries: ${GREEN}$SHOWS${NC} séries, $SEASONS saisons, $EPISODES épisodes"
    fi

    # Musique
    ARTISTS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE metadata_type = 8;" 2>/dev/null || echo "0")
    ALBUMS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE metadata_type = 9;" 2>/dev/null || echo "0")
    TRACKS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE metadata_type = 10;" 2>/dev/null || echo "0")
    if [ "$ARTISTS" -gt 0 ]; then
        echo -e "   🎵 Musique: ${GREEN}$ARTISTS${NC} artistes, $ALBUMS albums, $TRACKS pistes"
    fi

    # Photos
    PHOTOS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE metadata_type = 12;" 2>/dev/null || echo "0")
    PHOTO_ALBUMS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE metadata_type = 14;" 2>/dev/null || echo "0")
    if [ "$PHOTOS" -gt 0 ]; then
        echo -e "   📷 Photos: ${GREEN}$PHOTOS${NC} photos, $PHOTO_ALBUMS albums"
    fi

    # --- Détail par bibliothèque ---
    echo -e "\n${BOLD}📋 Détail par bibliothèque:${NC}"
    sqlite3 "$DB_PATH" "
        SELECT
            ls.name,
            ls.section_type,
            COUNT(mi.id) as count
        FROM library_sections ls
        LEFT JOIN metadata_items mi ON mi.library_section_id = ls.id
        GROUP BY ls.id
        ORDER BY ls.id;
    " 2>/dev/null | while IFS='|' read name type count; do
        case $type in
            1) icon="🎬"; label="films" ;;
            2) icon="📺"; label="items" ;;
            8) icon="🎵"; label="items" ;;
            13) icon="📷"; label="photos" ;;
            *) icon="📁"; label="items" ;;
        esac
        echo -e "   $icon ${CYAN}$name${NC}: ${GREEN}$count${NC} $label"
    done

    # --- Échantillons ---
    echo -e "\n${BOLD}🔍 Échantillons (3 aléatoires de chaque type):${NC}"

    # Désactiver set -e pour les échantillons (non critique)
    set +e

    if [ "$MOVIES" -gt 0 ]; then
        echo -e "   ${BLUE}Films:${NC}"
        sqlite3 "$DB_PATH" "SELECT '      - ' || title || COALESCE(' (' || year || ')', '') FROM metadata_items WHERE metadata_type = 1 ORDER BY RANDOM() LIMIT 3;" 2>/dev/null
    fi

    if [ "$SHOWS" -gt 0 ]; then
        echo -e "   ${BLUE}Séries:${NC}"
        sqlite3 "$DB_PATH" "SELECT '      - ' || title || COALESCE(' (' || year || ')', '') FROM metadata_items WHERE metadata_type = 2 ORDER BY RANDOM() LIMIT 3;" 2>/dev/null
    fi

    if [ "$ARTISTS" -gt 0 ]; then
        echo -e "   ${BLUE}Artistes:${NC}"
        sqlite3 "$DB_PATH" "SELECT '      - ' || title FROM metadata_items WHERE metadata_type = 8 ORDER BY RANDOM() LIMIT 3;" 2>/dev/null
    fi

    if [ "$ALBUMS" -gt 0 ]; then
        echo -e "   ${BLUE}Albums:${NC}"
        sqlite3 "$DB_PATH" "SELECT '      - ' || title FROM metadata_items WHERE metadata_type = 9 ORDER BY RANDOM() LIMIT 3;" 2>/dev/null || echo "      (erreur lecture albums)"
    fi

    set -e

    # Total items
    TOTAL_ITEMS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items;" 2>/dev/null || echo "0")
    echo -e "\n   ${BOLD}Total items en base: ${GREEN}$TOTAL_ITEMS${NC}"
fi

# === MÉTADONNÉES (Bundles) ===
echo -e "\n${BOLD}🎬 Métadonnées (bundles):${NC}"
if [ -d "Metadata" ]; then
    # Compter par type de bundle
    MOVIE_BUNDLES=$(find Metadata/Movies -name "*.bundle" -type d 2>/dev/null | wc -l)
    TV_BUNDLES=$(find Metadata/TV\ Shows -name "*.bundle" -type d 2>/dev/null | wc -l)
    ARTIST_BUNDLES=$(find Metadata/Artists -name "*.bundle" -type d 2>/dev/null | wc -l)
    ALBUM_BUNDLES=$(find Metadata/Albums -name "*.bundle" -type d 2>/dev/null | wc -l)
    PHOTO_BUNDLES=$(find Metadata/Photos -name "*.bundle" -type d 2>/dev/null | wc -l)

    [ "$MOVIE_BUNDLES" -gt 0 ] && echo -e "   🎬 Movies: ${GREEN}$MOVIE_BUNDLES${NC} bundles"
    [ "$TV_BUNDLES" -gt 0 ] && echo -e "   📺 TV Shows: ${GREEN}$TV_BUNDLES${NC} bundles"
    [ "$ARTIST_BUNDLES" -gt 0 ] && echo -e "   🎤 Artists: ${GREEN}$ARTIST_BUNDLES${NC} bundles"
    [ "$ALBUM_BUNDLES" -gt 0 ] && echo -e "   💿 Albums: ${GREEN}$ALBUM_BUNDLES${NC} bundles"
    [ "$PHOTO_BUNDLES" -gt 0 ] && echo -e "   📷 Photos: ${GREEN}$PHOTO_BUNDLES${NC} bundles"

    TOTAL_BUNDLES=$((MOVIE_BUNDLES + TV_BUNDLES + ARTIST_BUNDLES + ALBUM_BUNDLES + PHOTO_BUNDLES))
    SIZE=$(du -sh Metadata 2>/dev/null | cut -f1)
    echo -e "   ${BOLD}Total: ${GREEN}$TOTAL_BUNDLES${NC} bundles (${SIZE})${NC}"

    # Vérifier les pochettes dans les bundles (Music)
    if [ "$ARTIST_BUNDLES" -gt 0 ] || [ "$ALBUM_BUNDLES" -gt 0 ]; then
        echo -e "\n   ${BLUE}Pochettes musique:${NC}"
        # Plex stocke les images hashées (SHA-1) dans Contents/_stored/ ou _combined/
        # Chercher tous les fichiers non-xml/json dans les bundles
        POSTER_FILES=0
        for bundle_type in "Metadata/Artists" "Metadata/Albums"; do
            if [ -d "$bundle_type" ]; then
                # Compter les fichiers dans _stored ou _combined (images hashées)
                COUNT=$(find "$bundle_type" -type d \( -name "_stored" -o -name "_combined" \) -exec find {} -type f \; 2>/dev/null | wc -l)
                POSTER_FILES=$((POSTER_FILES + COUNT))
            fi
        done

        if [ "$POSTER_FILES" -gt 0 ]; then
            echo -e "      Fichiers média (hashés): ${GREEN}$POSTER_FILES${NC}"
        else
            # Fallback: compter tous les fichiers non-texte
            BINARY_COUNT=$(find Metadata/Artists Metadata/Albums -type f ! -name "*.xml" ! -name "*.json" ! -name "Info.xml" 2>/dev/null | wc -l)
            if [ "$BINARY_COUNT" -gt 0 ]; then
                echo -e "      Fichiers binaires: ${GREEN}$BINARY_COUNT${NC}"
            else
                echo -e "      ${YELLOW}Aucune pochette téléchargée${NC}"
            fi
        fi
    fi
else
    echo -e "   ${YELLOW}⚠️  Dossier Metadata absent${NC}"
fi

# === MEDIA (Miniatures vidéo) ===
echo -e "\n${BOLD}🖼️  Media (miniatures vidéo/photos):${NC}"
if [ -d "Media" ]; then
    FILES=$(find Media -type f 2>/dev/null | wc -l)
    SIZE=$(du -sh Media 2>/dev/null | cut -f1)

    if [ "$FILES" -gt 0 ]; then
        echo -e "   Fichiers: ${GREEN}$FILES${NC}"
        echo "   Taille: $SIZE"

        # Détail par sous-dossier
        for subdir in Media/*/; do
            if [ -d "$subdir" ]; then
                name=$(basename "$subdir")
                count=$(find "$subdir" -type f 2>/dev/null | wc -l)
                [ "$count" -gt 0 ] && echo "      $name: $count fichiers"
            fi
        done
    else
        echo -e "   ${YELLOW}Aucun fichier (normal si pas de vidéos/photos)${NC}"
    fi
else
    echo -e "   ${YELLOW}Dossier Media absent${NC}"
fi

# === DIAGNOSTIC PAR BIBLIOTHÈQUE ===
echo -e "\n${BOLD}🔍 Diagnostic par bibliothèque:${NC}"

if [ "$SQLITE_AVAILABLE" = true ]; then

    # Compter les thumbnails vidéo disponibles
    THUMB_COUNT=0
    if [ -d "Media/localhost" ]; then
        THUMB_COUNT=$(find "Media/localhost" -type f 2>/dev/null | wc -l)
    fi

    # Itérer sur chaque bibliothèque
    sqlite3 "$DB_PATH" "SELECT id, name, section_type FROM library_sections ORDER BY id;" 2>/dev/null | while IFS='|' read -r LIB_ID LIB_NAME LIB_TYPE; do

        echo ""

        # Icône et type selon section_type
        case $LIB_TYPE in
            1)  ICON="🎬"; TYPE_NAME="Movies" ;;
            2)  ICON="📺"; TYPE_NAME="TV Shows" ;;
            8)  ICON="🎵"; TYPE_NAME="Music" ;;
            13) ICON="📷"; TYPE_NAME="Photos" ;;
            *)  ICON="📁"; TYPE_NAME="Type $LIB_TYPE" ;;
        esac

        echo -e "   ${ICON} ${BOLD}${LIB_NAME}${NC} ${CYAN}(${TYPE_NAME})${NC}"

        # === DÉCOUVERTE ===
        case $LIB_TYPE in
            1) # Movies
                COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE library_section_id = $LIB_ID AND metadata_type = 1;" 2>/dev/null)
                echo -e "      Découverte: ${GREEN}${COUNT}${NC} films"
                DISCOVERABLE=$COUNT
                ;;
            2) # TV Shows
                SHOWS_C=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE library_section_id = $LIB_ID AND metadata_type = 2;" 2>/dev/null)
                SEASONS_C=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE library_section_id = $LIB_ID AND metadata_type = 3;" 2>/dev/null)
                EPISODES_C=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE library_section_id = $LIB_ID AND metadata_type = 4;" 2>/dev/null)
                echo -e "      Découverte: ${GREEN}${SHOWS_C}${NC} séries, ${SEASONS_C} saisons, ${EPISODES_C} épisodes"
                DISCOVERABLE=$SHOWS_C
                ;;
            8) # Music
                ARTISTS_C=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE library_section_id = $LIB_ID AND metadata_type = 8;" 2>/dev/null)
                ALBUMS_C=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE library_section_id = $LIB_ID AND metadata_type = 9;" 2>/dev/null)
                TRACKS_C=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE library_section_id = $LIB_ID AND metadata_type = 10;" 2>/dev/null)
                echo -e "      Découverte: ${GREEN}${ARTISTS_C}${NC} artistes, ${ALBUMS_C} albums, ${TRACKS_C} pistes"
                DISCOVERABLE=$((ARTISTS_C + ALBUMS_C))
                ;;
            13) # Photos
                PHOTOS_C=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM metadata_items WHERE library_section_id = $LIB_ID AND metadata_type = 12;" 2>/dev/null)
                echo -e "      Découverte: ${GREEN}${PHOTOS_C}${NC} photos"
                DISCOVERABLE=$PHOTOS_C
                ;;
            *)
                DISCOVERABLE=0
                ;;
        esac

        # === ENRICHISSEMENT ===
        ENRICH_STATUS=""
        ENRICH_ISSUES=""

        case $LIB_TYPE in
            1|2) # Movies / TV Shows - Bundles + Thumbnails
                if [ $LIB_TYPE -eq 1 ]; then
                    BUNDLE_PATH="Metadata/Movies"
                else
                    BUNDLE_PATH="Metadata/TV Shows"
                fi

                if [ -d "$BUNDLE_PATH" ]; then
                    BUNDLES_C=$(find "$BUNDLE_PATH" -name "*.bundle" -type d 2>/dev/null | wc -l)
                else
                    BUNDLES_C=0
                fi

                # Ratio bundles
                if [ "$DISCOVERABLE" -gt 0 ]; then
                    RATIO=$((BUNDLES_C * 100 / DISCOVERABLE))
                else
                    RATIO=0
                fi

                echo -n "      Enrichissement: ${BUNDLES_C} bundles"
                [ "$DISCOVERABLE" -gt 0 ] && echo -n " (${RATIO}%)"

                # Thumbnails
                if [ "$THUMB_COUNT" -gt 0 ]; then
                    echo ", ${THUMB_COUNT} thumbs"
                else
                    echo ""
                    ENRICH_ISSUES="thumbs manquants"
                fi

                [ "$RATIO" -ge 70 ] && ENRICH_STATUS="OK" || ENRICH_STATUS="PARTIAL"
                ;;

            8) # Music - Bundles + Sonic
                ARTIST_BUNDLES_C=0
                ALBUM_BUNDLES_C=0
                [ -d "Metadata/Artists" ] && ARTIST_BUNDLES_C=$(find "Metadata/Artists" -name "*.bundle" -type d 2>/dev/null | wc -l)
                [ -d "Metadata/Albums" ] && ALBUM_BUNDLES_C=$(find "Metadata/Albums" -name "*.bundle" -type d 2>/dev/null | wc -l)
                BUNDLES_C=$((ARTIST_BUNDLES_C + ALBUM_BUNDLES_C))

                # Ratio bundles vs artistes+albums
                if [ "$DISCOVERABLE" -gt 0 ]; then
                    RATIO=$((BUNDLES_C * 100 / DISCOVERABLE))
                else
                    RATIO=0
                fi

                # Sonic analysis - stocké dans metadata_items.extra_data pour les tracks
                # Pattern: ms:musicAnalysisVersion
                SONIC_ANALYZED=$(sqlite3 "$DB_PATH" "
                    SELECT COUNT(*) FROM metadata_items
                    WHERE library_section_id = $LIB_ID
                    AND metadata_type = 10
                    AND extra_data LIKE '%ms:musicAnalysisVersion%';
                " 2>/dev/null || echo "0")

                if [ "${TRACKS_C:-0}" -gt 0 ]; then
                    SONIC_PCT=$((SONIC_ANALYZED * 100 / TRACKS_C))
                else
                    SONIC_PCT=0
                fi

                echo "      Enrichissement: ${BUNDLES_C} bundles (${RATIO}%), Sonic ${SONIC_PCT}%"

                if [ "$RATIO" -ge 70 ] && [ "$SONIC_PCT" -ge 50 ]; then
                    ENRICH_STATUS="OK"
                elif [ "$RATIO" -ge 30 ] || [ "$SONIC_PCT" -ge 10 ]; then
                    ENRICH_STATUS="PARTIAL"
                    [ "$SONIC_PCT" -lt 50 ] && ENRICH_ISSUES="Sonic incomplet"
                else
                    ENRICH_STATUS="FAIL"
                fi
                ;;

            13) # Photos
                echo "      Enrichissement: (photos - pas de métadonnées externes)"
                ENRICH_STATUS="OK"
                ;;

            *)
                ENRICH_STATUS="UNKNOWN"
                ;;
        esac

        # === VERDICT ===
        if [ "${DISCOVERABLE:-0}" -eq 0 ]; then
            echo -e "      ${RED}❌ Aucun média découvert${NC}"
        elif [ "$ENRICH_STATUS" = "OK" ]; then
            echo -e "      ${GREEN}✅ OK${NC}"
        elif [ "$ENRICH_STATUS" = "PARTIAL" ]; then
            echo -en "      ${YELLOW}⚠️  Partiel${NC}"
            [ -n "$ENRICH_ISSUES" ] && echo -e " (${ENRICH_ISSUES})" || echo ""
        else
            echo -e "      ${RED}❌ Enrichissement insuffisant${NC}"
        fi

    done

else
    # Mode dégradé sans sqlite3
    echo -e "   ${YELLOW}(Installer sqlite3 pour le diagnostic par bibliothèque)${NC}"
    echo ""

    # Diagnostic global basé sur les fichiers
    DB_SIZE=$(stat -f%z "$DB_PATH" 2>/dev/null || stat -c%s "$DB_PATH" 2>/dev/null || echo "0")
    DB_SIZE_H=$(numfmt --to=iec $DB_SIZE 2>/dev/null || echo "${DB_SIZE} bytes")

    echo "   Base de données: $DB_SIZE_H"
    echo "   Bundles: ${TOTAL_BUNDLES:-0}"

    if [ "${DB_SIZE:-0}" -gt 1000000 ] && [ "${TOTAL_BUNDLES:-0}" -gt 0 ]; then
        echo -e "\n   ${GREEN}✅ Données présentes (détails indisponibles sans sqlite3)${NC}"
    else
        echo -e "\n   ${RED}❌ Données insuffisantes${NC}"
    fi
fi

# Nettoyage
if [ -n "$TEMP_DIR" ]; then
    echo -e "\n🧹 Nettoyage..."
    rm -rf "$TEMP_DIR"
fi

echo ""
