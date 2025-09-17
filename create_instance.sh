#!/bin/bash
# create_instance.sh - Créer une instance de test

echo "🧪 Création d'une instance de test..."

# Créer l'instance avec cloud-init
INSTANCE_ID=$(scw instance server create \
    type=DEV1-S \
    image=ubuntu_jammy \
    name=test-plex-$(date +%s) \
    cloud-init="$(cat setup_instance.sh)" \
    --output=json | jq -r '.id')

echo "✅ Instance créée: $INSTANCE_ID"

# Attendre et récupérer l'IP
sleep 30
INSTANCE_IP=$(scw instance server get $INSTANCE_ID --output=json | jq -r '.public_ip.address')

echo "📍 IP: $INSTANCE_IP"
echo "🔗 SSH: ssh root@$INSTANCE_IP"

# Sauvegarder l'ID pour destruction
echo $INSTANCE_ID > .current_instance_id
