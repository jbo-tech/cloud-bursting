#!/usr/bin/env python3
"""
local.py - Gestion de l'environnement de test local

Ce module gère le cycle de vie de l'environnement de test local:
- Préparation de l'environnement (dossiers, nettoyage)
- Nettoyage après tests
- Utilitaires pour les tests locaux

Fonctions principales:
- setup_local_test_env()     : Prépare l'environnement de test local
- cleanup_local_test_env()   : Nettoie l'environnement de test
- find_latest_db_archive()   : Trouve l'archive DB la plus récente
"""

import os
import glob
import time
from pathlib import Path

from .executor import execute_command


def setup_local_test_env(test_dir, mount_dir, plex_config, clean_docker=True):
    """
    Prépare l'environnement de test local

    Args:
        test_dir: Répertoire racine des tests (Path)
        mount_dir: Point de montage S3 (Path)
        plex_config: Répertoire de config Plex (Path)
        clean_docker: Si True, arrête et supprime le conteneur plex existant

    Cette fonction:
    - Nettoie les montages rclone existants
    - Arrête les conteneurs Docker plex (optionnel)
    - Crée les dossiers nécessaires
    """
    print(f"📁 Création des dossiers dans {test_dir}")

    # Nettoyage préalable du montage S3
    if mount_dir.exists():
        print(f"   ⚠️  {mount_dir} existe - nettoyage...")
        execute_command('localhost', f"fusermount3 -uz {mount_dir} || true", check=False)
        execute_command('localhost', "pkill -f 'rclone mount' || true", check=False)
        time.sleep(2)

    # Arrêter tout conteneur plex existant (pour tests propres)
    if clean_docker:
        execute_command('localhost', "docker stop plex 2>/dev/null || true", check=False)
        execute_command('localhost', "docker rm plex 2>/dev/null || true", check=False)

    # Créer les dossiers
    test_dir.mkdir(parents=True, exist_ok=True)
    plex_config.mkdir(parents=True, exist_ok=True)
    (plex_config / 'transcode').mkdir(parents=True, exist_ok=True)
    mount_dir.mkdir(parents=True, exist_ok=True)

    print(f"   ✅ Dossiers créés")


def cleanup_local_test_env(test_dir, mount_dir):
    """
    Nettoie l'environnement de test local

    Args:
        test_dir: Répertoire racine des tests (Path)
        mount_dir: Point de montage S3 (Path)

    Cette fonction:
    - Arrête et supprime le conteneur Plex (avec timeout robuste)
    - Démonte le système de fichiers S3
    - Supprime les dossiers de test
    """
    print("\n🧹 Nettoyage local...")

    # Arrêter Plex avec timeout robuste
    execute_command(
        'localhost',
        "docker stop -t 30 plex 2>/dev/null || docker kill plex 2>/dev/null || true",
        check=False
    )
    execute_command('localhost', "docker rm plex 2>/dev/null || true", check=False)

    # Démonter S3
    execute_command('localhost', f"fusermount3 -uz {mount_dir} || true", check=False)
    execute_command('localhost', "pkill -f 'rclone mount' || true", check=False)

    # Supprimer les dossiers (avec sudo pour les fichiers créés par plex UID 1000)
    print(f"📁 Suppression des dossiers de test...")
    execute_command('localhost', f"sudo rm -rf {test_dir}", check=False)

    print("✅ Nettoyage terminé")


def find_latest_db_archive(patterns=None, directory='.'):
    """
    Trouve l'archive DB Plex la plus récente

    Args:
        patterns: Liste de patterns glob (défaut: archives DB Plex)
        directory: Répertoire de recherche (défaut: répertoire courant)

    Returns:
        str: Chemin de l'archive la plus récente, ou None si aucune trouvée

    Exemple:
        >>> archive = find_latest_db_archive()
        >>> if archive:
        ...     print(f"Archive trouvée: {archive}")
    """
    if patterns is None:
        patterns = [
            "plex_db_only_*.tar.gz",
            "plex_db_metadata_*.tar.gz",
            "plex_metadata_*.tar.gz",
            "plex_delta_sync_*.tar.gz",
        ]

    # Rechercher toutes les archives correspondantes
    archives = []
    for pattern in patterns:
        search_pattern = os.path.join(directory, pattern)
        archives.extend(glob.glob(search_pattern))

    if not archives:
        return None

    # Trier par date de modification (plus récent en premier)
    archives.sort(key=os.path.getmtime, reverse=True)
    return archives[0]
