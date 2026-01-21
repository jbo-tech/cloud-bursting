#!/usr/bin/env python3
"""
Abstraction d'exécution : local vs remote
"""
import subprocess
import shutil
import os
import tempfile

# ============================================================================
# FONCTIONS PRIVÉES (implémentations spécifiques)
# ============================================================================

def _execute_local(command, check=True, capture_output=False, text=True):
    """Exécution locale via bash"""
    return subprocess.run(
        ["bash", "-c", command],
        check=check,
        capture_output=capture_output,
        text=text
    )

def _execute_remote(ip, command, check=True, capture_output=False, text=True):
    """Exécution distante via SSH"""
    ssh_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        f"root@{ip}",
        command
    ]
    return subprocess.run(
        ssh_cmd,
        check=check,
        capture_output=capture_output,
        text=text
    )

# ============================================================================
# API PUBLIQUE
# ============================================================================

def execute_command(ip, command, check=True, capture_output=False, text=True, verbose=False):
    """
    Exécute une commande selon le contexte.

    Args:
        ip: 'localhost' pour local, sinon IP de l'instance remote
        command: Commande shell à exécuter
        check: Lever une exception si erreur
        capture_output: Capturer stdout/stderr
        text: Mode texte (vs binaire)

    Returns:
        subprocess.CompletedProcess
    """
    if verbose:
        prefix = "[LOCAL]" if ip == 'localhost' else f"[REMOTE @ {ip}]"
        print(f"🔧 {prefix} {command[:80]}...")

    if ip == 'localhost':
        return _execute_local(command, check, capture_output, text)
    else:
        return _execute_remote(ip, command, check, capture_output, text)


def execute_script(ip, script_content, remote_path='/tmp/exec_script.sh'):
    """
    Exécute un script bash complexe de manière robuste.

    Stratégie :
    - Local : écrit dans /tmp, exécute, nettoie
    - Remote : écrit localement, copie via SCP, exécute via SSH, nettoie

    Bénéfice : Évite les problèmes d'échappement de quotes/pipes en SSH

    Args:
        ip: 'localhost' ou IP remote
        script_content: Contenu du script bash
        remote_path: Chemin du script sur la machine distante

    Returns:
        subprocess.CompletedProcess
    """
    if ip == 'localhost':
        # Exécution locale
        print(f"📜 [LOCAL] Exécution d'un script ({len(script_content)} bytes)")

        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(script_content)
            local_script_path = f.name

        try:
            result = subprocess.run(
                ["bash", local_script_path],
                check=True,
                capture_output=False,
                text=True
            )
            return result
        finally:
            os.unlink(local_script_path)

    else:
        # Exécution remote
        print(f"📜 [REMOTE @ {ip}] Exécution d'un script ({len(script_content)} bytes)")

        # 1. Écrire le script localement
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(script_content)
            local_script_path = f.name

        try:
            # 2. Copier sur la machine distante
            transfer_file_to_remote(local_script_path, ip, remote_path)

            # 3. Exécuter
            result = execute_command(ip, f"bash {remote_path}")

            # 4. Nettoyer le script distant
            execute_command(ip, f"rm -f {remote_path}", check=False)

            return result
        finally:
            os.unlink(local_script_path)


def docker_exec(ip, container, command, check=True, capture_output=False):
    """
    Exécute une commande dans un conteneur Docker.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        command: Commande à exécuter

    Returns:
        subprocess.CompletedProcess
    """
    docker_cmd = f"docker exec {container} {command}"
    return execute_command(ip, docker_cmd, check, capture_output, text=True)


def transfer_file_to_remote(local_path, ip, remote_path):
    """
    Copie un fichier local vers une machine distante.
    Ne fait rien si ip='localhost' (fichier déjà accessible).

    Args:
        local_path: Chemin local du fichier
        ip: IP de destination (ignoré si 'localhost')
        remote_path: Chemin sur la machine distante
    """
    if ip == 'localhost':
        print(f"ℹ️  [LOCAL] Fichier déjà accessible : {local_path}")
        return

    print(f"📤 [SCP] {local_path} → root@{ip}:{remote_path}")

    # Créer le répertoire parent si nécessaire
    remote_dir = os.path.dirname(remote_path)
    if remote_dir:
        execute_command(ip, f"mkdir -p {remote_dir}", check=False)

    scp_cmd = [
        "scp",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        local_path,
        f"root@{ip}:{remote_path}"
    ]
    subprocess.run(scp_cmd, check=True)


def download_file_from_remote(ip, remote_path, local_path):
    """
    Télécharge un fichier depuis une machine distante.
    Si ip='localhost', utilise docker cp si remote_path est au format 'container:path',
    sinon copie simple.

    Args:
        ip: IP source
        remote_path: Chemin sur la machine distante (ou 'container:path' en local)
        local_path: Chemin de destination local
    """
    if ip == 'localhost':
        # Si remote_path est au format 'container:/path', utiliser docker cp
        if ':' in remote_path and not remote_path.startswith('/'):
            print(f"📦 [DOCKER CP] {remote_path} → {local_path}")
            docker_cp_cmd = ["docker", "cp", remote_path, local_path]
            subprocess.run(docker_cp_cmd, check=True)
        else:
            # Vérifier si c'est le même fichier (chemins absolus)
            remote_abs = os.path.abspath(remote_path)
            local_abs = os.path.abspath(local_path)

            if remote_abs == local_abs:
                print(f"✅ [LOCAL] Fichier déjà présent : {local_path}")
            else:
                print(f"📋 [COPY] {remote_path} → {local_path}")
                shutil.copy(remote_path, local_path)
        return

    print(f"📥 [SCP] root@{ip}:{remote_path} → {local_path}")
    scp_cmd = [
        "scp",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        f"root@{ip}:{remote_path}",
        local_path
    ]
    subprocess.run(scp_cmd, check=True)


# ============================================================================
# GESTION DE FICHIERS D'ÉTAT
# ============================================================================

def read_state_file(path):
    """
    Lit un fichier d'état simple (texte)

    Args:
        path: Chemin du fichier à lire

    Returns:
        str: Contenu du fichier (sans whitespace), ou None si inexistant
    """
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read().strip()
    return None


def write_state_file(path, content):
    """
    Écrit un fichier d'état simple (texte)

    Args:
        path: Chemin du fichier à écrire
        content: Contenu à écrire (sera converti en string)
    """
    with open(path, "w") as f:
        f.write(str(content))
