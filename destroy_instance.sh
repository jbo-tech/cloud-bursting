#!/bin/bash
# destroy_instance.sh - Détruire l'instance de test

if [ -f .current_instance_id ]; then
    INSTANCE_ID=$(cat .current_instance_id)
    echo "🧹 Destruction de l'instance $INSTANCE_ID..."

    # 1. Récupérer l'état actuel
    STATUS=$(scw instance server get $INSTANCE_ID --output=json 2>/dev/null | jq -r '.state')
    echo "📊 État actuel: $STATUS"

    # 2. Arrêter si nécessaire
    if [ "$STATUS" != "stopped" ] && [ "$STATUS" != "stopping" ]; then
        echo "⏸️ Arrêt de l'instance..."
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
        echo "  État: $STATUS - attente... ($ATTEMPT/$MAX_ATTEMPTS)"
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
        scw instance server delete $INSTANCE_ID --with-volumes --with-ip --force 2>/dev/null || {
            echo "❌ Impossible de supprimer. Commande manuelle requise:"
            echo "   scw instance server delete $INSTANCE_ID"
            exit 1
        }
    fi

    # 6. Nettoyer
    rm .current_instance_id

    echo "✅ Instance détruite"
else
    echo "❌ Aucune instance à détruire"
fi
