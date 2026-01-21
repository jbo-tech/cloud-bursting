#!/bin/bash
# destroy_instance.sh - Détruire l'instance de test et nettoyer les ressources

echo "🛑 Suppression de l'instance Scaleway..."

if [ -f .current_instance_id ]; then
    INSTANCE_ID=$(cat .current_instance_id)
    echo "   🧹 Destruction de l'instance $INSTANCE_ID..."

    # 1. Récupérer les informations de l'instance (y compris l'IP et l'heure de création)
    INSTANCE_INFO=$(scw instance server get $INSTANCE_ID --output=json 2>/dev/null)
    STATUS=$(echo "$INSTANCE_INFO" | jq -r '.state')
    IP_ID=$(echo "$INSTANCE_INFO" | jq -r '.public_ip.id // empty')
    IP_ADDRESS=$(echo "$INSTANCE_INFO" | jq -r '.public_ip.address // empty')
    CREATION_DATE=$(echo "$INSTANCE_INFO" | jq -r '.creation_date // empty')

    echo "   📊 État actuel: $STATUS"
    [ -n "$IP_ADDRESS" ] && echo "   🌐 IP publique: $IP_ADDRESS (ID: $IP_ID)"
    [ -n "$CREATION_DATE" ] && echo "   🕐 Créée le: $CREATION_DATE"

    # 2. Arrêter si nécessaire
    if [ "$STATUS" != "stopped" ] && [ "$STATUS" != "stopping" ]; then
        echo "   ⏸️  Arrêt de l'instance..."
        scw instance server stop $INSTANCE_ID --wait 2>/dev/null || true
    fi

    # 3. Attendre l'arrêt complet (VRAIMENT attendre)
    echo "⏳ Attente de l'arrêt complet..."
    MAX_ATTEMPTS=60  # 2 minutes max
    ATTEMPT=0

    while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
        STATUS=$(scw instance server get $INSTANCE_ID --output=json 2>/dev/null | jq -r '.state')

        if [ "$STATUS" = "stopped" ]; then
            echo "✅ Instance arrêtée"
            break
        fi

        ATTEMPT=$((ATTEMPT + 1))
        echo "   État: $STATUS - attente... ($ATTEMPT/$MAX_ATTEMPTS)"
        sleep 2
    done

    # 4. Petit délai de sécurité (important!)
    echo "⏳ Délai de sécurité..."
    sleep 5

    # 5. Supprimer l'instance
    echo "🗑️ Suppression de l'instance..."
    if scw instance server delete $INSTANCE_ID 2>/dev/null; then
        echo "✅ Instance supprimée"
    else
        # Si ça échoue encore, forcer
        echo "⚠️ Première tentative échouée, force de suppression..."
        sleep 10
        scw instance server delete $INSTANCE_ID --with-volumes --force 2>/dev/null || {
            echo "❌ Impossible de supprimer. Commande manuelle requise:"
            echo "   scw instance server delete $INSTANCE_ID"
            exit 1
        }
    fi

    # 6. Supprimer explicitement l'IP publique si elle existe
    if [ -n "$IP_ID" ]; then
        echo "🗑️ Suppression de l'IP publique $IP_ADDRESS..."
        if scw instance ip delete $IP_ID 2>/dev/null; then
            echo "✅ IP publique supprimée"
        else
            echo "⚠️ L'IP a peut-être déjà été supprimée avec l'instance"
        fi
    fi

    # 7. Calculer le coût de l'opération
    echo ""
    echo "💰 Calcul du coût de l'opération..."
    if [ -n "$CREATION_DATE" ]; then
        # Convertir les dates en timestamps
        START_TS=$(date -d "$CREATION_DATE" +%s 2>/dev/null || echo "0")
        END_TS=$(date +%s)

        if [ "$START_TS" != "0" ]; then
            DURATION_SECONDS=$((END_TS - START_TS))
            DURATION_HOURS=$(echo "scale=2; $DURATION_SECONDS / 3600" | bc)

            # Coûts Scaleway (estimation)
            # GP1-S: ~0.10€/h, GP1-M: ~0.20€/h, GP1-L: ~0.40€/h
            # IP publique: ~0.01€/h
            # Stockage: ~0.10€/GB/mois (~0.00014€/GB/h)

            COST_COMPUTE=$(echo "scale=4; $DURATION_HOURS * 0.10" | bc)  # Assumant GP1-S
            COST_IP=$(echo "scale=4; $DURATION_HOURS * 0.01" | bc)
            COST_STORAGE=$(echo "scale=4; $DURATION_HOURS * 0.014" | bc) # 100GB
            TOTAL_COST=$(echo "scale=4; $COST_COMPUTE + $COST_IP + $COST_STORAGE" | bc)

            echo "   ⏱️  Durée d'exécution: ${DURATION_HOURS}h (${DURATION_SECONDS}s)"
            echo "   💵 Estimation du coût:"
            echo "      - Instance (GP1-S): ~${COST_COMPUTE}€"
            echo "      - IP publique: ~${COST_IP}€"
            echo "      - Stockage (100GB): ~${COST_STORAGE}€"
            echo "      - TOTAL: ~${TOTAL_COST}€"
            echo ""
            echo "   ℹ️  Note: Coûts estimés. Vérifiez votre console Scaleway pour le coût exact."
        fi
    fi

    # 8. Nettoyer les fichiers locaux
    rm -f .current_instance_id
    rm -f .current_instance_ip

    echo ""
    echo "✅ Nettoyage terminé"
else
    echo "❌ Aucune instance à détruire"
fi
