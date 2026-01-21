#!/bin/bash
# diagnose_sonic.sh - Diagnostic complet de l'analyse Sonic Plex
# Usage: ./diagnose_sonic.sh [container_name] [plex_token]

CONTAINER=${1:-plex}
TOKEN=${2:-}

echo "🔍 DIAGNOSTIC ANALYSE SONIC PLEX"
echo "=================================="
echo ""

# 1. Vérifier si Plex Pass est actif
echo "1️⃣  STATUS PLEX PASS"
if [ -n "$TOKEN" ]; then
    MYPLEXSUB=$(docker exec $CONTAINER curl -s "http://localhost:32400/myplex/account" \
        -H "X-Plex-Token: $TOKEN" 2>/dev/null | grep -oP 'subscriptionActive="\K[^"]+' || echo "unknown")
    
    if [ "$MYPLEXSUB" = "1" ]; then
        echo "   ✅ Plex Pass ACTIF"
    elif [ "$MYPLEXSUB" = "0" ]; then
        echo "   ❌ Plex Pass INACTIF - L'analyse Sonic ne fonctionnera PAS"
        echo "   → Vérifiez que votre compte a un abonnement actif"
    else
        echo "   ⚠️  Impossible de vérifier (serveur non claimé ?)"
        # Afficher la réponse brute pour debug
        docker exec $CONTAINER curl -s "http://localhost:32400/myplex/account" \
            -H "X-Plex-Token: $TOKEN" 2>/dev/null | head -5
    fi
else
    echo "   ⚠️  Token non fourni - vérification manuelle requise"
fi
echo ""

# 2. Vérifier les préférences d'analyse
echo "2️⃣  PRÉFÉRENCES D'ANALYSE"
if [ -n "$TOKEN" ]; then
    echo "   Récupération des settings..."
    
    # Les préférences pertinentes pour l'analyse audio
    PREFS=$(docker exec $CONTAINER curl -s "http://localhost:32400/:/prefs" \
        -H "X-Plex-Token: $TOKEN" 2>/dev/null)
    
    # Extraire les valeurs (format XML)
    echo "$PREFS" | grep -oE '(LoudnessAnalysis|musicAnalysis|ButlerTaskSonicAnalysis)[^/]*' | while read line; do
        KEY=$(echo "$line" | grep -oP 'id="\K[^"]+')
        VALUE=$(echo "$line" | grep -oP 'value="\K[^"]+')
        DEFAULT=$(echo "$line" | grep -oP 'default="\K[^"]+')
        echo "   $KEY: $VALUE (default: $DEFAULT)"
    done
    
    # Si rien n'est affiché, chercher autrement
    if ! echo "$PREFS" | grep -q "LoudnessAnalysis"; then
        echo "   ⚠️  Préférences d'analyse non trouvées dans la réponse"
    fi
else
    echo "   ⚠️  Token requis"
fi
echo ""

# 3. Vérifier les tâches Butler en cours
echo "3️⃣  ACTIVITÉS BUTLER (Background Tasks)"
if [ -n "$TOKEN" ]; then
    ACTIVITIES=$(docker exec $CONTAINER curl -s "http://localhost:32400/activities" \
        -H "X-Plex-Token: $TOKEN" 2>/dev/null)
    
    ACTIVITY_COUNT=$(echo "$ACTIVITIES" | grep -c "<Activity" || echo "0")
    echo "   Tâches actives: $ACTIVITY_COUNT"
    
    if [ "$ACTIVITY_COUNT" -gt 0 ]; then
        echo "$ACTIVITIES" | grep -oP 'title="\K[^"]+' | while read title; do
            echo "   → $title"
        done
    fi
fi
echo ""

# 4. Vérifier les logs du scanner pour les erreurs Sonic
echo "4️⃣  LOGS SCANNER (dernières erreurs liées à l'analyse)"
LOG_PATH="/config/Library/Application Support/Plex Media Server/Logs"

docker exec $CONTAINER sh -c "grep -i 'sonic\|analysis\|loudness\|fingerprint' '$LOG_PATH/Plex Media Scanner.log' 2>/dev/null | tail -20" || \
    echo "   Aucune entrée trouvée dans les logs scanner"

echo ""
docker exec $CONTAINER sh -c "grep -i 'sonic\|analysis\|loudness' '$LOG_PATH/Plex Media Server.log' 2>/dev/null | tail -10" || \
    echo "   Aucune entrée trouvée dans les logs serveur"
echo ""

# 5. Tester le lancement manuel de l'analyse
echo "5️⃣  TEST LANCEMENT ANALYSE MANUELLE"

# Via API (méthode officielle)
if [ -n "$TOKEN" ]; then
    echo "   Tentative via API /library/sections/1/analyze..."
    RESPONSE=$(docker exec $CONTAINER curl -s -X PUT \
        "http://localhost:32400/library/sections/1/analyze?force=1" \
        -H "X-Plex-Token: $TOKEN" 2>&1)
    
    if [ -z "$RESPONSE" ]; then
        echo "   ✅ Commande acceptée (réponse vide = OK)"
    else
        echo "   Réponse: $RESPONSE"
    fi
fi

# Via CLI Scanner
echo ""
echo "   Tentative via CLI Scanner --analyze..."
docker exec $CONTAINER '/usr/lib/plexmediaserver/Plex Media Scanner' \
    --analyze --section 1 2>&1 | head -5

echo ""

# 6. Vérifier si des pistes ont l'extra_data Sonic
echo "6️⃣  ÉCHANTILLON DE DONNÉES SONIC EN BASE"
DB="/config/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

# Une piste avec analyse
echo "   Piste AVEC analyse Sonic:"
docker exec $CONTAINER '/usr/lib/plexmediaserver/Plex SQLite' "$DB" \
    "SELECT id, title, substr(extra_data, 1, 200) FROM metadata_items 
     WHERE metadata_type=10 AND extra_data LIKE '%ms:musicAnalysisVersion%' 
     LIMIT 1;" 2>/dev/null || echo "   (aucune)"

echo ""
echo "   Piste SANS analyse Sonic (extra_data):"
docker exec $CONTAINER '/usr/lib/plexmediaserver/Plex SQLite' "$DB" \
    "SELECT id, title, substr(extra_data, 1, 200) FROM metadata_items 
     WHERE metadata_type=10 AND (extra_data IS NULL OR extra_data NOT LIKE '%ms:musicAnalysisVersion%')
     LIMIT 1;" 2>/dev/null

echo ""
echo "=================================="
echo "🏁 FIN DU DIAGNOSTIC"
