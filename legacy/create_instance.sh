#!/bin/bash
# create_instance.sh - Créer une instance de test

set -euo pipefail

# Charger les variables d'environnement
source .env

echo "🧪 Création d'une instance de test..."

# Créer l'instance avec cloud-init
INSTANCE_ID=$(scw instance server create \
    type=${INSTANCE_TYPE} \
    zone=${SCW_DEFAULT_ZONE} \
    name=${INSTANCE_NAME} \
    image=${IMAGE} \
    root-volume=l:${ROOT_VOLUME_SIZE} \
    cloud-init="$(cat setup_instance.sh)" \
    --output=json | jq -r '.id')

if [ -z "$INSTANCE_ID" ]; then
    echo "❌ Erreur : La création de l'instance a échoué."
    exit 1
fi

echo "✅ Instance créée: $INSTANCE_ID"

# Attendre et récupérer l'IP
sleep 30
INSTANCE_IP=$(scw instance server get $INSTANCE_ID --output=json | jq -r '.public_ip.address')

echo "📍 IP: $INSTANCE_IP"
echo "🔗 SSH: ssh root@$INSTANCE_IP"

# Sauvegarder l'ID pour destruction
echo $INSTANCE_ID > .current_instance_id

# Sauvegarder l'IP pour référence
echo $INSTANCE_IP > .current_instance_ip

ping -c 3 ${INSTANCE_IP}

# --- Boucle de validation SSH ---
echo "⏳ Attente de la disponibilité de SSH (peut prendre 1 à 2 minutes)..."
for i in {1..20}; do
    ssh -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 "root@${INSTANCE_IP}" "exit"
    if [ $? -eq 0 ]; then
        echo "✅ SSH est prêt."
        echo "⏳ L'instance démarre et exécute le script de configuration en arrière-plan..."
        echo "   Vous pouvez surveiller la configuration en vous connectant et en regardant les logs :"
        echo "   ssh root@${INSTANCE_IP} 'tail -f /var/log/cloud-init-output.log'"
        SSH_READY=true
        break
    fi
    echo "   Tentative ${i}/20 : SSH non disponible, attente 5 secondes..."
    sleep 5
done
echo "   DEBUG: Fin de la boucle SSH avec statut=$SSH_READY"

if [ -z "$SSH_READY" ]; then
    echo "❌ Échec : Le port SSH n'est pas devenu accessible."
    exit 1
fi

# Laisser un peu de temps à cloud-init pour terminer ses tâches lourdes
echo "⏳ Attente de 60 secondes pour la fin de la configuration cloud-init..."
sleep 60

# --- Section de validation automatique ---
echo "---"
echo "🕵️  Lancement des validations sur l'instance distante..."
echo "---"

# 1. Boucle d'attente pour le message de succès dans le log
CONFIG_SUCCESS=false
# On essaie pendant 5 minutes maximum (20 tentatives * 15 secondes)
for i in {1..20}; do
    echo "   Tentative ${i}/20 : Vérification du log cloud-init..."
    if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "root@${INSTANCE_IP}" "grep -q 'Instance configurée et prête' /var/log/cloud-init-output.log"; then
        echo "   ✅ Succès : Le script cloud-init s'est terminé correctement."
        CONFIG_SUCCESS=true
        break
    fi
    sleep 15
done

if [ "$CONFIG_SUCCESS" = "false" ]; then
    echo "   ❌ Échec : Le script cloud-init ne s'est pas terminé dans le temps imparti."
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "root@${INSTANCE_IP}" "echo '--- Dernières lignes du log ---'; tail -n 30 /var/log/cloud-init-output.log"
    exit 1
fi

# Les validations suivantes sont maintenant quasi-certaines de réussir

# 2. Vérifier la version de Rclone
echo -e "\n2️⃣  Validation de Rclone..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "root@${INSTANCE_IP}" "rclone version"

# 3. Vérifier que l'image Docker Plex est bien présente
echo -e "\n3️⃣  Validation de l'image Docker..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "root@${INSTANCE_IP}" "docker images"

# 4. Vérifier les permissions du dossier de configuration
echo -e "\n4️⃣  Validation des permissions..."
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "root@${INSTANCE_IP}" "ls -ld /opt/plex_data/config"

echo -e "\n🎉 Tout est parfaitement configuré ! L'instance est prête."
echo "   Pour détruire l'instance, lancez : ./destroy_instance.sh"
