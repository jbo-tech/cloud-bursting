#!/usr/bin/env python3
"""
delta_sync.py - Fonctions pour le workflow Delta Sync
Permet d'injecter une DB Plex existante dans un nouveau conteneur
"""

import os
import time
from pathlib import Path
from .executor import execute_command, docker_exec, transfer_file_to_remote


def inject_existing_db(ip, archive_path, plex_config_path, container='plex'):
    """
    Injecte une archive DB Plex existante dans le volume de configuration.

    IMPORTANT: Doit être appelé AVANT de démarrer le conteneur Plex.

    Args:
        ip: 'localhost' ou IP remote
        archive_path: Chemin vers l'archive .tar.gz (local si ip='localhost', sinon sera transférée)
        plex_config_path: Chemin du volume config Plex (ex: /opt/plex_data/config ou ./tmp/plex-config)
        container: Nom du conteneur (pour vérifier qu'il n'est pas démarré)

    Returns:
        bool: True si succès, False sinon
    """
    print(f"\n💉 Injection de la DB existante...")
    print(f"   Archive: {archive_path}")
    print(f"   Destination: {plex_config_path}")

    # 1. Vérifier que le conteneur n'est pas démarré
    check_cmd = f"docker ps --format '{{{{.Names}}}}' | grep -q '^{container}$' && echo 'running' || echo 'stopped'"
    result = execute_command(ip, check_cmd, capture_output=True, check=False)

    if 'running' in result.stdout:
        print(f"   ❌ Le conteneur '{container}' est démarré. Arrêtez-le d'abord.")
        return False

    # 2. Vérifier que l'archive existe
    if ip == 'localhost':
        if not os.path.exists(archive_path):
            print(f"   ❌ Archive introuvable: {archive_path}")
            return False
        archive_remote = archive_path
    else:
        # Transférer l'archive vers l'instance remote
        print(f"   📤 Transfert de l'archive vers {ip}...")
        archive_remote = f"/tmp/{os.path.basename(archive_path)}"
        transfer_file_to_remote(archive_path, ip, archive_remote)

    # 3. Créer la structure de base si nécessaire
    pms_path = f"{plex_config_path}/Library/Application Support/Plex Media Server"
    execute_command(ip, f"mkdir -p '{pms_path}'", check=False)

    # 4. Extraire l'archive
    print(f"   📦 Extraction de l'archive...")

    # L'archive contient "Plug-in Support/Databases" et optionnellement "Metadata"
    # On extrait directement dans le dossier Plex Media Server
    extract_cmd = f"tar -xzf '{archive_remote}' -C '{pms_path}'"
    result = execute_command(ip, extract_cmd, check=False, capture_output=True)

    if result.returncode != 0:
        print(f"   ❌ Erreur d'extraction: {result.stderr}")
        return False

    # 5. Vérifier que la DB est bien là
    db_file = f"{pms_path}/Plug-in Support/Databases/com.plexapp.plugins.library.db"
    check_db = f"test -f '{db_file}' && echo 'found' || echo 'missing'"
    result = execute_command(ip, check_db, capture_output=True)

    if 'missing' in result.stdout:
        print(f"   ❌ DB non trouvée après extraction")
        return False

    # 5b. Vérifier que la DB est lisible (évite PRAGMA integrity_check qui
    # échoue sur les tables FTS avec tokenizers personnalisés de Plex)
    print(f"   🔍 Vérification de l'intégrité de la DB...")
    # Requête simple sur une table basique pour valider que la DB est lisible
    integrity_cmd = f"sqlite3 '{db_file}' 'SELECT COUNT(*) FROM library_sections;'"
    integrity_result = execute_command(ip, integrity_cmd, capture_output=True, check=False)

    if integrity_result.returncode != 0:
        print(f"   ❌ Erreur sqlite3: {integrity_result.stderr}")
        print(f"   💡 L'archive source est probablement endommagée.")
        print(f"   💡 Regénérez l'archive avec: ./export_zimaboard_db.sh")
        return False

    print(f"   ✅ DB lisible ({integrity_result.stdout.strip()} bibliothèques)")

    # 6. Corriger les permissions (UID 1000 = plex dans le conteneur)
    print(f"   🔐 Correction des permissions...")
    execute_command(ip, f"chown -R 1000:1000 '{plex_config_path}'", check=False)
    execute_command(ip, f"chmod -R 755 '{plex_config_path}'", check=False)

    # 7. Afficher les stats
    db_size_cmd = f"du -sh '{pms_path}/Plug-in Support/Databases/'"
    db_size = execute_command(ip, db_size_cmd, capture_output=True, check=False)

    metadata_size_cmd = f"du -sh '{pms_path}/Metadata/' 2>/dev/null || echo '0\tN/A'"
    metadata_size = execute_command(ip, metadata_size_cmd, capture_output=True, check=False)

    print(f"   📊 DB injectée: {db_size.stdout.strip()}")
    print(f"   📊 Metadata: {metadata_size.stdout.strip()}")
    print(f"   ✅ Injection réussie!")

    return True


def get_library_stats_from_db(ip, plex_config_path):
    """
    Lit les statistiques directement depuis la DB SQLite injectée.
    Utile pour vérifier l'état avant de démarrer Plex.

    Args:
        ip: 'localhost' ou IP remote
        plex_config_path: Chemin du volume config Plex

    Returns:
        dict: Stats par type de média
    """
    db_path = f"{plex_config_path}/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

    stats = {
        'artists': 0,
        'albums': 0,
        'tracks': 0,
        'tracks_with_sonic': 0,
        'movies': 0,
        'shows': 0,
        'episodes': 0,
        'photos': 0,
        'sections': []
    }

    # Utiliser sqlite3 natif (pas besoin de Plex)
    queries = {
        'artists': "SELECT COUNT(*) FROM metadata_items WHERE metadata_type=8;",
        'albums': "SELECT COUNT(*) FROM metadata_items WHERE metadata_type=9;",
        'tracks': "SELECT COUNT(*) FROM metadata_items WHERE metadata_type=10;",
        'tracks_with_sonic': "SELECT COUNT(*) FROM metadata_items WHERE metadata_type=10 AND extra_data LIKE '%ms:musicAnalysisVersion%';",
        'movies': "SELECT COUNT(*) FROM metadata_items WHERE metadata_type=1;",
        'shows': "SELECT COUNT(*) FROM metadata_items WHERE metadata_type=2;",
        'episodes': "SELECT COUNT(*) FROM metadata_items WHERE metadata_type=4;",
        'photos': "SELECT COUNT(*) FROM metadata_items WHERE metadata_type=13;",
    }

    print(f"\n📊 Lecture des stats depuis la DB injectée...")

    for key, query in queries.items():
        cmd = f"sqlite3 '{db_path}' \"{query}\""
        result = execute_command(ip, cmd, capture_output=True, check=False)

        if result.returncode == 0 and result.stdout.strip().isdigit():
            stats[key] = int(result.stdout.strip())

    # Sections (bibliothèques)
    sections_query = "SELECT id, name, section_type FROM library_sections;"
    result = execute_command(ip, f"sqlite3 '{db_path}' \"{sections_query}\"", capture_output=True, check=False)

    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split('\n'):
            parts = line.split('|')
            if len(parts) >= 3:
                stats['sections'].append({
                    'id': parts[0],
                    'name': parts[1],
                    'type': parts[2]
                })

    # Chemins par section
    paths_query = "SELECT library_section_id, root_path FROM section_locations;"
    result = execute_command(ip, f"sqlite3 '{db_path}' \"{paths_query}\"", capture_output=True, check=False)

    section_paths = {}
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.strip().split('\n'):
            parts = line.split('|')
            if len(parts) >= 2:
                section_id = parts[0]
                path = parts[1]
                if section_id not in section_paths:
                    section_paths[section_id] = []
                section_paths[section_id].append(path)

    stats['section_paths'] = section_paths

    return stats


def print_injection_stats(stats):
    """Affiche les stats de la DB injectée de manière lisible"""
    print("\n" + "=" * 50)
    print("📊 CONTENU DE LA DB INJECTÉE")
    print("=" * 50)

    if stats['sections']:
        print("\n📚 Bibliothèques:")
        for section in stats['sections']:
            paths = stats.get('section_paths', {}).get(section['id'], [])
            paths_str = ', '.join(paths) if paths else 'N/A'
            print(f"   [{section['id']}] {section['name']} (type {section['type']})")
            print(f"       → {paths_str}")

    print("\n🎵 Musique:")
    print(f"   Artistes: {stats['artists']}")
    print(f"   Albums: {stats['albums']}")
    print(f"   Pistes: {stats['tracks']}")
    if stats['tracks'] > 0:
        sonic_pct = (stats['tracks_with_sonic'] / stats['tracks']) * 100
        print(f"   Analyse Sonic: {stats['tracks_with_sonic']} ({sonic_pct:.1f}%)")

    print("\n🎬 Vidéo:")
    print(f"   Films: {stats['movies']}")
    print(f"   Séries: {stats['shows']}")
    print(f"   Épisodes: {stats['episodes']}")

    if stats.get('photos', 0) > 0:
        print("\n📷 Photos:")
        print(f"   Photos: {stats['photos']}")

    print("=" * 50)


def verify_paths_match(ip, plex_config_path, mount_point):
    """
    Vérifie que les chemins dans la DB correspondent au point de montage actuel.

    La DB contient des chemins absolus (ex: /media/Music).
    Si le montage est différent, Plex ne trouvera pas les fichiers.

    Args:
        ip: 'localhost' ou IP remote
        plex_config_path: Chemin du volume config Plex
        mount_point: Point de montage S3 actuel (ex: /mnt/s3-media)

    Returns:
        dict: {'match': bool, 'db_paths': list, 'suggestions': list}
    """
    db_path = f"{plex_config_path}/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

    # Récupérer les chemins configurés dans la DB
    query = "SELECT DISTINCT root_path FROM section_locations;"
    result = execute_command(ip, f"sqlite3 '{db_path}' \"{query}\"", capture_output=True, check=False)

    db_paths = []
    if result.returncode == 0 and result.stdout.strip():
        db_paths = result.stdout.strip().split('\n')

    print(f"\n🔍 Vérification des chemins...")
    print(f"   Chemins dans la DB: {db_paths}")
    print(f"   Point de montage actuel: {mount_point}")

    # Vérifier si les chemins correspondent
    # Typiquement, la DB aura /media/X et le montage sera aussi /media ou /mnt/s3-media

    suggestions = []
    all_match = True

    for db_path_item in db_paths:
        # Vérifier si le chemin existe avec le montage actuel
        # Par exemple, si DB a "/media/Music" et montage est "/mnt/s3-media"
        # on doit vérifier que /mnt/s3-media/Music existe

        # Extraire la partie relative (ex: "Music" de "/media/Music")
        # Ceci est une heuristique - on suppose que /media/ est le préfixe standard
        relative = db_path_item.replace('/media/', '').replace('/Media/', '')
        check_path = f"{mount_point}/{relative}"

        check_cmd = f"test -d '{check_path}' && echo 'exists' || echo 'missing'"
        check_result = execute_command(ip, check_cmd, capture_output=True, check=False)

        if 'missing' in check_result.stdout:
            all_match = False
            suggestions.append(f"Chemin manquant: {check_path}")

    return {
        'match': all_match,
        'db_paths': db_paths,
        'mount_point': mount_point,
        'suggestions': suggestions
    }


def load_path_mappings(mappings_file=None):
    """
    Charge les mappings de chemins depuis un fichier JSON.

    Args:
        mappings_file: Chemin vers path_mappings.json (optionnel, auto-détecté sinon)

    Returns:
        dict: {'file': str|None, 'mappings': dict} - fichier trouvé et mappings
    """
    import json

    result = {'file': None, 'mappings': {}}

    # Auto-détection si non spécifié
    if mappings_file is None:
        for candidate in ['path_mappings.json', '../path_mappings.json']:
            if os.path.exists(candidate):
                mappings_file = candidate
                break

    if mappings_file is None or not os.path.exists(mappings_file):
        return result

    try:
        with open(mappings_file, 'r') as f:
            config = json.load(f)
        result['file'] = mappings_file
        result['mappings'] = config.get('mappings', {})
    except (json.JSONDecodeError, IOError) as e:
        print(f"   ⚠️  Erreur lecture {mappings_file}: {e}")

    return result


def remap_library_paths(ip, plex_config_path, mount_point, mappings, backup_dir=None):
    """
    Remplace les chemins dans la DB Plex selon un dictionnaire de mappings.

    Modifie:
    - section_locations.root_path (chemins racines des bibliothèques)
    - media_parts.file (chemins absolus des fichiers médias)

    Crée un backup de la DB avant modification.

    Args:
        ip: 'localhost' ou IP remote
        plex_config_path: Chemin du volume config Plex
        mount_point: Point de montage S3 actuel (pour vérifier les nouveaux chemins)
        mappings: dict {ancien_chemin: nouveau_chemin}
        backup_dir: Répertoire pour le backup (défaut: ./tmp)

    Returns:
        dict: {'sections_remapped': int, 'files_remapped': int, 'skipped': int, 'errors': list}
    """
    import shutil
    from datetime import datetime

    db_path = f"{plex_config_path}/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

    result = {'sections_remapped': 0, 'files_remapped': 0, 'skipped': 0, 'errors': []}

    if not mappings:
        return result

    print(f"\n🔄 Remapping des chemins ({len(mappings)} mappings)...")

    # 1. Backup de la DB avant modification
    if backup_dir is None:
        backup_dir = './tmp'

    backup_name = f"plex_db_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    backup_path = f"{backup_dir}/{backup_name}"

    print(f"   💾 Backup de la DB → {backup_path}")

    if ip == 'localhost':
        try:
            shutil.copy2(db_path, backup_path)
        except IOError as e:
            error_msg = f"Échec backup: {e}"
            print(f"   ❌ {error_msg}")
            result['errors'].append(error_msg)
            return result
    else:
        # Remote: copier via SSH
        cp_result = execute_command(ip, f"cp '{db_path}' '/tmp/{backup_name}'", capture_output=True, check=False)
        if cp_result.returncode != 0:
            error_msg = f"Échec backup remote: {cp_result.stderr}"
            print(f"   ❌ {error_msg}")
            result['errors'].append(error_msg)
            return result

    # 2. Appliquer les remappings
    for old_path, new_path in mappings.items():
        print(f"\n   📂 {old_path} → {new_path}")

        # 2a. Vérifier si l'ancien chemin est dans section_locations
        check_query = f"SELECT COUNT(*) FROM section_locations WHERE root_path = '{old_path}';"
        check_result = execute_command(ip, f"sqlite3 '{db_path}' \"{check_query}\"", capture_output=True, check=False)

        sections_count = int(check_result.stdout.strip()) if check_result.returncode == 0 and check_result.stdout.strip().isdigit() else 0

        if sections_count == 0:
            print(f"      ⏭️  Aucune section avec ce chemin")
            result['skipped'] += 1
            continue

        # 2b. Vérifier que le nouveau chemin existe sur le montage
        relative = new_path.replace('/media/', '').replace('/Media/', '')
        check_path = f"{mount_point}/{relative}"

        check_cmd = f"test -d '{check_path}' && echo 'exists' || echo 'missing'"
        path_result = execute_command(ip, check_cmd, capture_output=True, check=False)

        if 'missing' in path_result.stdout:
            error_msg = f"Nouveau chemin inexistant: {check_path}"
            print(f"      ❌ {error_msg}")
            result['errors'].append(f"{old_path}: {error_msg}")
            continue

        # 2c. Compter les fichiers à remapper dans media_parts
        count_query = f"SELECT COUNT(*) FROM media_parts WHERE file LIKE '{old_path}%';"
        count_result = execute_command(ip, f"sqlite3 '{db_path}' \"{count_query}\"", capture_output=True, check=False)
        files_count = int(count_result.stdout.strip()) if count_result.returncode == 0 and count_result.stdout.strip().isdigit() else 0

        # 2d. Remapper section_locations
        update_sections = f"UPDATE section_locations SET root_path = '{new_path}' WHERE root_path = '{old_path}';"
        sections_result = execute_command(ip, f"sqlite3 '{db_path}' \"{update_sections}\"", capture_output=True, check=False)

        if sections_result.returncode != 0:
            error_msg = f"Erreur SQL section_locations: {sections_result.stderr}"
            print(f"      ❌ {error_msg}")
            result['errors'].append(f"{old_path}: {error_msg}")
            continue

        print(f"      ✅ section_locations: {sections_count} section(s)")
        result['sections_remapped'] += sections_count

        # 2e. Remapper media_parts (REPLACE pour les chemins de fichiers)
        if files_count > 0:
            update_files = f"UPDATE media_parts SET file = REPLACE(file, '{old_path}', '{new_path}') WHERE file LIKE '{old_path}%';"
            files_result = execute_command(ip, f"sqlite3 '{db_path}' \"{update_files}\"", capture_output=True, check=False)

            if files_result.returncode != 0:
                error_msg = f"Erreur SQL media_parts: {files_result.stderr}"
                print(f"      ⚠️  {error_msg}")
                result['errors'].append(f"{old_path}: {error_msg}")
            else:
                print(f"      ✅ media_parts: {files_count} fichier(s)")
                result['files_remapped'] += files_count

    # 3. Résumé
    print(f"\n   📊 Remapping terminé:")
    print(f"      Sections: {result['sections_remapped']}")
    print(f"      Fichiers: {result['files_remapped']}")
    if result['skipped'] > 0:
        print(f"      Ignorés: {result['skipped']}")
    if result['errors']:
        print(f"      ⚠️  Erreurs: {len(result['errors'])}")
        print(f"      💾 Backup disponible: {backup_path}")

    return result
