#!/bin/bash
# destroy_instance.sh - Détruire l'instance de test

if [ -f .current_instance_id ]; then
    INSTANCE_ID=$(cat .current_instance_id)
    echo "🧹 Destruction de l'instance $INSTANCE_ID..."

    scw instance server stop $INSTANCE_ID 
    echo "🛑 Instance stoppée..."
    sleep 10   
    scw instance server delete $INSTANCE_ID
    
    rm .current_instance_id
    
    echo "✅ Instance détruite"
else
    echo "❌ Aucune instance à détruire"
fi
