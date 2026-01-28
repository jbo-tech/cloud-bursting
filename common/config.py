#!/usr/bin/env python3
"""
Chargement de la configuration centralisée
"""
import os
import json
from datetime import datetime
from dotenv import load_dotenv


def print_phase_header(phase_num, title, width=60):
    """
    Affiche un en-tête de phase avec horodatage.

    Args:
        phase_num: Numéro de la phase (int ou str)
        title: Titre de la phase
        width: Largeur du séparateur (default: 60)

    Exemple de sortie:
        ============================================================
        PHASE 8: TRAITEMENT MUSIQUE (Sonic) [00:31:55]
        ============================================================
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    print("\n" + "=" * width)
    print(f"PHASE {phase_num}: {title} [{timestamp}]")
    print("=" * width)

def load_env():
    """
    Charge les variables d'environnement depuis .env

    Returns:
        dict: Configuration complète
    """
    load_dotenv()

    return {
        'INSTANCE_TYPE': os.getenv('INSTANCE_TYPE', 'GP1-M'),
        'INSTANCE_ZONE': os.getenv('SCW_DEFAULT_ZONE', 'fr-par-1'),
        'ROOT_VOLUME_SIZE': os.getenv('ROOT_VOLUME_SIZE', '50GB'),
        'RCLONE_REMOTE': os.getenv('RCLONE_REMOTE', 'mega-s4'),
        'S3_BUCKET': os.getenv('S3_BUCKET'),
        'PLEX_VERSION': os.getenv('PLEX_VERSION', 'latest'),
        'ZIMABOARD_IP': os.getenv('ZIMABOARD_IP'),
        'PLEX_CONFIG_PATH': os.getenv('PLEX_CONFIG_PATH'),
        'PLEX_LOCAL_CONTAINER_NAME': os.getenv('PLEX_LOCAL_CONTAINER_NAME', 'plex'),
    }


def load_libraries(limit=None):
    """
    Charge la configuration des bibliothèques Plex.

    Note : Ce fichier reste LOCAL, il n'est jamais transféré sur l'instance remote.
    C'est le script d'orchestration qui lit ce fichier pour savoir quelles
    commandes SSH envoyer.

    Args:
        limit: Nombre max de librairies à charger (pour tests)

    Returns:
        list: Liste des configs de librairies
    """
    with open('plex_libraries.json', 'r') as f:
        libraries = json.load(f)

    if limit is not None:
        libraries = libraries[:limit]
        print(f"ℹ️  Mode test : {len(libraries)} librairie(s) chargée(s)")

    return libraries


def get_rclone_config_path():
    """
    Retourne le chemin absolu vers rclone.conf

    Returns:
        str: Chemin absolu
    """
    return os.path.abspath('./rclone.conf')


def get_rclone_remote_name():
    """
    Retourne le nom du remote rclone (défini dans rclone.conf)

    Returns:
        str: Nom du remote (ex: 'mega-s4')
    """
    load_dotenv()
    return os.getenv('RCLONE_REMOTE', 'mega-s4')


def get_rclone_profile(profile='lite'):
    """
    Retourne la configuration rclone selon le profil de performance.

    Profils disponibles :
    - lite : DEV1-S (2 vCPU, 2GB RAM) - économique
    - standard : DEV1-M (3 vCPU, 4GB RAM) - équilibré
    - power : GP1-S (4 vCPU, 8GB RAM) - performant
    - superpower : GP1-M (4 vCPU, 16GB RAM) - performance maximale

    Args:
        profile: 'lite', 'standard', 'power', ou 'superpower'

    Returns:
        dict: Paramètres rclone optimisés
    """
    profiles = {
        'lite': {
            'cache_size': '5G',
            'buffer_size': '64M',
            'read_chunk': '32M',
            'transfers': '2',     # Réduit pour MEGA (rate-limiting)
            'checkers': '4',      # Réduit pour MEGA
            'timeout': '60m',
            'contimeout': '300s',
            'low_level_retries': '10',
            'retries': '10',
            'retries_sleep': '30s',
            'dir_cache': '24h',
            'attr_timeout': '8760h',
        },
        'standard': {
            'cache_size': '10G',  # Cache plus large
            'buffer_size': '128M',
            'read_chunk': '64M',
            'transfers': '4',     # Réduit de 8 à 4 pour MEGA
            'checkers': '8',      # Réduit de 16 à 8 pour MEGA
            'timeout': '120m',    # Timeout plus long
            'contimeout': '600s', # Connection timeout plus long
            'low_level_retries': '20',
            'retries': '20',
            'retries_sleep': '60s',
            'dir_cache': '72h',
            'attr_timeout': '8760h',
        },
        'power': {
            'cache_size': '20G',
            'buffer_size': '256M',
            'read_chunk': '128M',
            'transfers': '16',
            'checkers': '32',
            'timeout': '60m',
            'contimeout': '300s',
            'low_level_retries': '10',
            'retries': '10',
            'retries_sleep': '30s',
            'dir_cache': '24h',
            'attr_timeout': '8760h',
        },
        'superpower': {
            'cache_size': '20G',
            'buffer_size': '512M',
            'read_chunk': '128M',
            'transfers': '32',
            'checkers': '64',
            'timeout': '60m',
            'contimeout': '300s',
            'low_level_retries': '10',
            'retries': '10',
            'retries_sleep': '30s',
            'dir_cache': '24h',
            'attr_timeout': '8760h',
        }
    }

    return profiles.get(profile, profiles['lite'])


def get_docker_limits(profile='lite'):
    """
    Retourne les limites Docker (memory, swap, cpus) selon le profil.

    Args:
        profile: 'lite', 'standard', 'power', ou 'superpower'

    Returns:
        dict: Limites Docker
    """
    limits = {
        'lite': {
            'memory': '4g',
            'memory_swap': '6g',
            'cpus': '2.0',
        },
        'standard': {
            'memory': '8g',
            'memory_swap': '10g',
            'cpus': '4.0',
        },
        'power': {
            'memory': '16g',
            'memory_swap': '18g',
            'cpus': '4.0',
        },
        'superpower': {
            'memory': '32g',
            'memory_swap': '34g',
            'cpus': '4.0',
        }
    }

    return limits.get(profile, limits['lite'])
