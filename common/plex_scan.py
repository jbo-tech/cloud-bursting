#!/usr/bin/env python3
"""
Logique de scan, monitoring et export Plex
"""
import time
import re
import os
from datetime import datetime
from .executor import execute_command, docker_exec


# === PROFILS DE MONITORING ===
# Paramètres adaptés selon le contexte d'exécution
MONITORING_PROFILES = {
    'local_quick': {
        # Test local rapide sans base initiale
        'check_interval': 30,
        'stall_threshold': 5,       # 2.5 min sans progression = arrêt
        'cpu_idle_threshold': 5.0,
        'absolute_timeout': 3600,   # 1h max
        'description': 'Test local rapide (validation workflow)'
    },
    'local_delta': {
        # Test local avec base initiale (delta sync)
        'check_interval': 60,
        'stall_threshold': 10,      # 10 min sans progression = arrêt
        'cpu_idle_threshold': 10.0,
        'absolute_timeout': 7200,   # 2h max
        'description': 'Test local delta sync'
    },
    'cloud_standard': {
        # Cloud avec bibliothèque moyenne
        'check_interval': 60,
        'stall_threshold': 15,      # 15 min sans progression = arrêt
        'cpu_idle_threshold': 10.0,
        'absolute_timeout': 28800,  # 8h max
        'description': 'Cloud scan standard'
    },
    'cloud_intensive': {
        # Cloud avec grosse bibliothèque + Sonic (run de plusieurs jours)
        'check_interval': 120,
        'stall_threshold': 30,      # 1h sans progression = arrêt (30 × 2min)
        'cpu_idle_threshold': 5.0,  # Plus strict car Sonic utilise beaucoup de CPU
        'absolute_timeout': 259200, # 72h max (3 jours)
        'description': 'Cloud scan intensif (grosse lib + Sonic, 3j max)'
    },
    'metadata_refresh': {
        # Refresh metadata séparé (images, paroles, matching)
        # Long timeout car 456k pistes = beaucoup de téléchargements réseau
        'check_interval': 120,
        'stall_threshold': 60,      # 2h sans progression = arrêt (60 × 2min)
        'cpu_idle_threshold': 20.0, # Moins strict car I/O réseau variable
        'absolute_timeout': 14400,  # 4h max pour metadata seul
        'description': 'Refresh metadata (images, paroles, matching)'
    }
}


def get_monitoring_params(profile='cloud_standard'):
    """
    Retourne les paramètres de monitoring selon le profil.

    Args:
        profile: 'local_quick', 'local_delta', 'cloud_standard', 'cloud_intensive'

    Returns:
        dict: Paramètres pour monitor_analysis_phase

    Usage:
        params = get_monitoring_params('local_quick')
        monitor_analysis_phase(ip, container, plex_token, **params)
    """
    if profile not in MONITORING_PROFILES:
        print(f"⚠️  Profil '{profile}' inconnu, utilisation de 'cloud_standard'")
        profile = 'cloud_standard'

    params = MONITORING_PROFILES[profile].copy()
    del params['description']  # Ne pas passer la description à la fonction
    return params


def scan_section_incrementally(ip, container, plex_token, library_section_id, library_section_type, mount_path_internal, mount_path_local, filter_prefixes=None):
    """
    Scan synchrone via CLI (Plus robuste pour les noms avec accents/virgules).
    """
    print(f"🔬 Démarrage du scan SYNCHRONE (CLI Direct) pour section {library_section_id}...")

    # 1. Lister tous les éléments
    try:
        all_items = sorted([d for d in os.listdir(mount_path_local) if os.path.isdir(os.path.join(mount_path_local, d))])

        items = all_items # Par défaut, on prend tout
        if filter_prefixes:
            filtered = [d for d in all_items if d.upper().startswith(tuple(filter_prefixes))]
            print(f"   🔍 Filtre activé : {filter_prefixes}")

            if not filtered:
                # REPLI : Si le filtre ne correspond à rien (ex: 'Q' pour TV), on scanne tout
                print(f"   ⚠️ Aucun dossier ne commence par {filter_prefixes}. Repli sur le scan complet.")
                items = all_items
            else:
                items = filtered
                print(f"   📊 {len(items)} éléments retenus sur {len(all_items)} totaux.")

    except Exception as e:
        print(f"   ❌ Erreur d'accès disque: {e}")
        return 0

    # 2. Initialisation pour éviter l'UnboundLocalError
    total_start = get_section_item_count(ip, container, plex_token, library_section_id, library_section_type)
    current_total = total_start
    new_total = total_start

    for i, item_name in enumerate(items, 1):
        # Construction du chemin interne
        # On utilise shlex.quote plus tard via docker_exec pour gérer les espaces/quotes
        plex_path = os.path.join(mount_path_internal, item_name).replace("\\", "/")

        print(f"   [{i}/{len(items)}] 🚀 Scan: {item_name}...", end='', flush=True)

        # --- CHANGEMENT MAJEUR ICI ---
        # Au lieu de l'API (curl), on utilise le CLI Scanner
        # Syntaxe: Plex Media Scanner --scan --refresh --section X --directory "/path/to/folder"

        # On doit échapper les guillemets doubles pour la commande bash
        safe_path = plex_path.replace('"', '\\"')

        scan_cmd = (
            f"'/usr/lib/plexmediaserver/Plex Media Scanner' "
            f"--refresh --section {library_section_id} "
            f"--directory \"{safe_path}\""
        )

        # On lance en mode "Check=False" car le scanner retourne parfois des codes non-zero non critiques
        docker_exec(ip, container, scan_cmd, check=False)

        # 2. ATTENTE ACTIVE
        # On attend un peu que le process apparaisse
        time.sleep(1)

        wait_cycles = 0
        while True:
            # On vérifie si le scanner tourne
            res = docker_exec(ip, container, "pgrep -f 'Plex Media Scanner'", capture_output=True, check=False)
            if not res.stdout.strip():
                break

            if wait_cycles % 5 == 0: print(".", end='', flush=True)
            time.sleep(1)
            wait_cycles += 1

            if wait_cycles > 600: # 10 minutes max par artiste
                print(" ⚠️ Timeout! (Kill)", end='')
                docker_exec(ip, container, "pkill -f 'Plex Media Scanner'", check=False)
                break

        # 3. Rapport
        new_total = get_track_count(ip, container, plex_token, library_section_id)
        added = new_total - current_total
        current_total = new_total

        if added > 0:
            print(f" ✅ (+{added} pistes | Total: {new_total})")
        else:
            # Si 0 ajout, c'est peut-être normal (déjà scanné) ou un échec silencieux
            print(f" ➖ (Stable | Total: {new_total})")

        time.sleep(0.1)

    print(f"\n✅ Scan terminé.")

    return new_total


def get_track_count(ip, container, plex_token, section_id):
    """Helper pour compter les pistes (Type 10)"""
    cmd = f"curl -s 'http://localhost:32400/library/sections/{section_id}/all?type=10' -H 'X-Plex-Token: {plex_token}' | grep -c '<Track'"
    res = docker_exec(ip, container, cmd, capture_output=True, check=False)
    try:
        return int(res.stdout.strip())
    except:
        return 0


def get_section_item_count(ip, container, plex_token, section_id, section_type):
    """
    Compte les items d'une section selon son type.
    Réutilise la logique de type de get_library_item_counts.

    Returns:
        int: Nombre d'items
    """
    # Mapping type de section → type d'item "feuille" à compter
    type_map = {
        'artist': 10,   # tracks
        'movie': 1,     # movies
        'show': 4,      # episodes
        'photo': 13     # photos
    }
    item_type = type_map.get(section_type, 10)

    cmd = f"curl -s 'http://localhost:32400/library/sections/{section_id}/all?type={item_type}' -H 'X-Plex-Token: {plex_token}' | grep -c 'ratingKey'"
    res = docker_exec(ip, container, cmd, capture_output=True, check=False)
    try:
        return int(res.stdout.strip())
    except:
        return 0


def get_library_item_counts(ip, container, plex_token):
    """
    Récupère les vrais compteurs d'items par type de média.

    Types Plex supportés:
    - artist (musique): artists(8), albums(9), tracks(10)
    - movie (films): movies(1)
    - show (séries): shows(2), episodes(4)
    - photo (photos): photos(13), photoalbums(14)

    Returns:
        dict: {
            'sections': [{'id', 'title', 'type', 'refreshing', 'items'}],
            'totals': {'artists', 'albums', 'tracks', 'movies', 'shows', 'episodes', 'photos'}
        }
    """
    totals = {
        'artists': 0, 'albums': 0, 'tracks': 0,
        'movies': 0, 'shows': 0, 'episodes': 0,
        'photos': 0
    }
    sections = []

    # Récupérer les sections
    api_url = "http://localhost:32400/library/sections"
    curl_cmd = f"curl -s '{api_url}' -H 'X-Plex-Token: {plex_token}'"
    result = docker_exec(ip, container, curl_cmd, capture_output=True, check=False)

    if not result.stdout:
        return {'sections': sections, 'totals': totals}

    # Parser les sections
    for directory_match in re.finditer(r'<Directory[^>]+>', result.stdout):
        tag = directory_match.group(0)

        # Extraire chaque attribut individuellement
        key_match = re.search(r'key="(\d+)"', tag)
        type_match = re.search(r'type="(\w+)"', tag)
        title_match = re.search(r'title="([^"]+)"', tag)
        refreshing_match = re.search(r'refreshing="(\d)"', tag)

        if not all([key_match, type_match, title_match]):
            continue  # Skip si attributs manquants

        section_id = key_match.group(1)
        section_type = type_match.group(1)
        section_title = title_match.group(1)
        refreshing = refreshing_match.group(1) == "1" if refreshing_match else False

        section_info = {
            'id': section_id,
            'title': section_title,
            'type': section_type,
            'refreshing': refreshing,
            'items': {}
        }

        # Compter selon le type de section
        if section_type == 'artist':  # Musique
            # Artistes (type 8)
            cmd = f"curl -s 'http://localhost:32400/library/sections/{section_id}/all?type=8' -H 'X-Plex-Token: {plex_token}' | grep -c 'ratingKey'"
            res = docker_exec(ip, container, cmd, capture_output=True, check=False)
            artists = int(res.stdout.strip()) if res.stdout.strip().isdigit() else 0

            # Albums (type 9)
            cmd = f"curl -s 'http://localhost:32400/library/sections/{section_id}/all?type=9' -H 'X-Plex-Token: {plex_token}' | grep -c 'ratingKey'"
            res = docker_exec(ip, container, cmd, capture_output=True, check=False)
            albums = int(res.stdout.strip()) if res.stdout.strip().isdigit() else 0

            # Pistes (type 10)
            cmd = f"curl -s 'http://localhost:32400/library/sections/{section_id}/all?type=10' -H 'X-Plex-Token: {plex_token}' | grep -c 'ratingKey'"
            res = docker_exec(ip, container, cmd, capture_output=True, check=False)
            tracks = int(res.stdout.strip()) if res.stdout.strip().isdigit() else 0

            section_info['items'] = {'artists': artists, 'albums': albums, 'tracks': tracks}
            totals['artists'] += artists
            totals['albums'] += albums
            totals['tracks'] += tracks

        elif section_type == 'movie':  # Films
            cmd = f"curl -s 'http://localhost:32400/library/sections/{section_id}/all?type=1' -H 'X-Plex-Token: {plex_token}' | grep -c 'ratingKey'"
            res = docker_exec(ip, container, cmd, capture_output=True, check=False)
            movies = int(res.stdout.strip()) if res.stdout.strip().isdigit() else 0
            section_info['items'] = {'movies': movies}
            totals['movies'] += movies

        elif section_type == 'show':  # Séries
            # Shows (type 2)
            cmd = f"curl -s 'http://localhost:32400/library/sections/{section_id}/all?type=2' -H 'X-Plex-Token: {plex_token}' | grep -c 'ratingKey'"
            res = docker_exec(ip, container, cmd, capture_output=True, check=False)
            shows = int(res.stdout.strip()) if res.stdout.strip().isdigit() else 0

            # Episodes (type 4)
            cmd = f"curl -s 'http://localhost:32400/library/sections/{section_id}/all?type=4' -H 'X-Plex-Token: {plex_token}' | grep -c 'ratingKey'"
            res = docker_exec(ip, container, cmd, capture_output=True, check=False)
            episodes = int(res.stdout.strip()) if res.stdout.strip().isdigit() else 0

            section_info['items'] = {'shows': shows, 'episodes': episodes}
            totals['shows'] += shows
            totals['episodes'] += episodes

        elif section_type == 'photo':  # Photos
            # Photos (type 13)
            cmd = f"curl -s 'http://localhost:32400/library/sections/{section_id}/all?type=13' -H 'X-Plex-Token: {plex_token}' | grep -c 'ratingKey'"
            res = docker_exec(ip, container, cmd, capture_output=True, check=False)
            photos = int(res.stdout.strip()) if res.stdout.strip().isdigit() else 0
            section_info['items'] = {'photos': photos}
            totals['photos'] += photos

        sections.append(section_info)

    return {'sections': sections, 'totals': totals}


def format_item_counts(totals):
    """Formate les compteurs pour affichage compact"""
    parts = []
    if totals['tracks']:
        parts.append(f"🎵 {totals['tracks']} pistes")
    if totals['albums']:
        parts.append(f"💿 {totals['albums']} albums")
    if totals['artists']:
        parts.append(f"🎤 {totals['artists']} artistes")
    if totals['movies']:
        parts.append(f"🎬 {totals['movies']} films")
    if totals['episodes']:
        parts.append(f"📺 {totals['episodes']} épisodes")
    if totals.get('shows'):
        parts.append(f"📺 {totals['shows']} séries")
    if totals.get('photos'):
        parts.append(f"📷 {totals['photos']} photos")
    return " | ".join(parts) if parts else "0 items"


def monitor_discovery_phase(ip, container='plex', plex_token=None, check_interval=30, max_idle=5):
    """
    Surveille la phase de découverte (scan) avec vrais compteurs.
    """
    print("👁️  Surveillance de la phase de découverte...")

    previous_total = 0
    idle_count = 0
    max_attempts = 100  # ~50 min max avec check_interval=30

    for attempt in range(max_attempts):
        try:
            # Récupérer les vrais compteurs
            data = get_library_item_counts(ip, container, plex_token)
            sections = data['sections']
            totals = data['totals']

            # Total = tracks + movies + episodes (items "feuilles")
            current_total = totals['tracks'] + totals['movies'] + totals['episodes']

            # Vérifier si scan en cours
            any_refreshing = any(s['refreshing'] for s in sections)

            # Vérifier processus scanner
            scanner_cmd = "pgrep -f 'Plex Media Scanner' || true"
            scanner_result = docker_exec(ip, container, scanner_cmd, capture_output=True, check=False)
            scanner_running = bool(scanner_result.stdout.strip())

            # Affichage
            status_icon = '🟢' if (scanner_running or any_refreshing) else '🔴'
            diff = current_total - previous_total
            diff_str = f" (+{diff})" if diff > 0 else ""

            print(f"   [{time.strftime('%H:%M:%S')}] {format_item_counts(totals)}{diff_str} | Scanner: {status_icon}")

            # Détection de fin
            if current_total == previous_total:
                idle_count += 1
            else:
                idle_count = 0

            if idle_count >= max_idle and not scanner_running and not any_refreshing:
                print(f"✅ Phase de découverte terminée")
                print(f"   📊 {format_item_counts(totals)}")
                return

            previous_total = current_total
            time.sleep(check_interval)

        except Exception as e:
            print(f"⚠️  Erreur lors du monitoring: {e}")
            if attempt >= max_attempts - 1:
                print("❌ Trop d'erreurs, arrêt du monitoring")
                return
            time.sleep(check_interval)


def monitor_discovery_phase_clean(ip, container, plex_token, libraries, check_interval=60, max_idle=10):
    """
    Monitoring avec détail par bibliothèque.
    CORRIGÉ : Ne s'arrête pas tant que le scanner tourne (pour gérer le Directory Walk sur S3).
    """
    print("👁️  Surveillance du scan...")

    idle_count = 0
    last_total = 0
    last_per_section = {}
    start_time = time.time()
    scanner_active = True # On assume qu'il est actif au démarrage

    # Compter les fichiers réels au départ (une fois)
    print("\n📊 Comptage des fichiers réels dans le montage...")
    real_counts = count_real_media_files(ip, container, libraries)

    # CONDITION CORRIGÉE : On continue tant qu'on n'est pas idle OU que le scanner tourne encore
    while idle_count < max_idle or scanner_active:
        # Récupérer les sections avec leur contenu
        cmd = f"curl -s 'http://localhost:32400/library/sections' -H 'X-Plex-Token: {plex_token}'"
        result = docker_exec(ip, container, cmd, capture_output=True, check=False)

        # Parser chaque section
        sections = {}
        import re
        for match in re.finditer(r'<Directory.*?key="(\d+)".*?title="([^"]+)".*?>', result.stdout):
            section_id = match.group(1)
            section_name = match.group(2)

            count_cmd = f"curl -s 'http://localhost:32400/library/sections/{section_id}/all' -H 'X-Plex-Token: {plex_token}' | grep -o 'ratingKey' | wc -l"
            count_result = docker_exec(ip, container, count_cmd, capture_output=True, check=False)

            count_str = count_result.stdout.strip()
            count = int(count_str) if count_str and count_str.isdigit() else 0
            sections[section_name] = count

        total_items = sum(sections.values())

        # Vérifier le scanner
        scanner_cmd = "pgrep -f 'Plex Media Scanner' || true"
        scanner_result = docker_exec(ip, container, scanner_cmd, capture_output=True, check=False)
        scanner_active = bool(scanner_result.stdout.strip())

        # Gestion de l'inactivité
        if total_items > last_total:
            idle_count = 0
        else:
            idle_count += 1

        # Affichage détaillé
        elapsed = int(time.time() - start_time)
        status = f"   [{elapsed//60:02d}:{elapsed%60:02d}] Total: {total_items}"

        if total_items > last_total:
            status += f" (+{total_items - last_total})"
        elif scanner_active and idle_count >= max_idle:
            # Message rassurant : le compteur ne bouge pas, mais le scanner bosse
            status += f" | ⏳ Exploration en cours (Directory Walk)..."
        elif idle_count > 1:
            status += f" | Idle: {idle_count}/{max_idle}"

        status += f" | Scanner: {'🟢' if scanner_active else '🔴'}"
        print(status)

        # Détail par section
        for name, count in sections.items():
            diff = count - last_per_section.get(name, 0)
            real = real_counts.get(name, '?')
            detail = f"         └─ {name}: {count}/{real}"
            if diff > 0:
                detail += f" (+{diff})"
            print(detail)

        last_per_section = sections
        last_total = total_items

        # SORTIE : Si plus de scanner ET quota d'attente atteint
        if not scanner_active and idle_count >= max_idle:
            print(f"\n✅ Scan terminé : {total_items} médias trouvés")

            # Comparaison finale
            print("\n📊 Comparaison avec les fichiers réels:")
            for name, plex_count in sections.items():
                real = real_counts.get(name, '?')
                if real != '?' and real > 0:
                    percentage = (plex_count / real * 100)
                    print(f"   {name}: {plex_count}/{real} ({percentage:.1f}%)")
                else:
                    print(f"   {name}: {plex_count}/? (non compté)")
            return total_items

        time.sleep(check_interval)

    return total_items


def count_real_media_files(ip, container, libraries):
    """
    Compte les vrais fichiers média pour chaque bibliothèque.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur Plex
        libraries: Liste des configs de bibliothèques (depuis plex_libraries.json)
                   Ex: [{"name": "Movies", "paths": ["/Media/Movies"]}, ...]

    Returns:
        dict: {nom_biblio: nombre_fichiers}
              Ex: {"Movies": 230, "TV": 766, "Adult": 125}
    """
    counts = {}

    # Extensions vidéo communes
    extensions = "mp4 mkv avi mov wmv flv webm m4v mpg mpeg mp3 flac wav m4a aac ogg wma"
    find_pattern = ' -o '.join([f'-iname *.{ext}' for ext in extensions.split()])

    for lib in libraries:
        lib_name = lib['title']

        # Une bibliothèque peut avoir plusieurs chemins (paths est une liste)
        total_count = 0
        for lib_path in lib['paths']:
            cmd = f"find {lib_path} -type f \\( {find_pattern} \\) 2>/dev/null | wc -l"
            result = docker_exec(ip, container, cmd, capture_output=True, check=False)
            count = int(result.stdout.strip() or "0")
            total_count += count

        counts[lib_name] = total_count

    # Affichage compact
    summary = ", ".join([f"{name}={count}" for name, count in counts.items()])
    print(f"   Fichiers réels trouvés: {summary}")

    return counts


def diagnose_scan_issues(ip, container='plex', plex_token=None):
    """
    Diagnostic générique des problèmes de scan.
    Fonctionne pour tous les types de bibliothèques.
    """
    print("\n🔍 DIAGNOSTIC SCAN")
    print("=" * 50)

    # 1. État général des bibliothèques
    print("\n1️⃣ État des bibliothèques:")
    if plex_token:
        cmd = f"curl -s 'http://localhost:32400/library/sections?X-Plex-Token={plex_token}' | grep -E 'title=|type=|path=|count=' | head -20"
        result = execute_command(ip, cmd, capture_output=True, check=False)
        if result.stdout:
            print(result.stdout)
        else:
            print("   Pas d'infos API disponibles")

    # 2. Vérifier ce que Plex voit réellement comme fichiers
    print("\n2️⃣ Échantillon de fichiers accessibles:")
    # Lister quelques fichiers depuis le point de montage
    cmd = f"docker exec {container} sh -c 'find /Media -type f \\( -name \"*.mp4\" -o -name \"*.mkv\" -o -name \"*.avi\" \\) 2>/dev/null | head -10'"
    result = execute_command(ip, cmd, capture_output=True, check=False)
    if result.stdout:
        print("   Fichiers visibles dans /media:")
        for line in result.stdout.strip().split('\n'):
            print(f"   ✓ {line}")
    else:
        print("   ⚠️ Aucun fichier média trouvé dans /media")

    # 3. Permissions
    print("\n3️⃣ Vérification des permissions:")
    cmd = f"docker exec {container} sh -c 'ls -la /media | head -5'"
    result = execute_command(ip, cmd, capture_output=True, check=False)
    print(result.stdout or "   Impossible de vérifier")

    # 4. Structure réelle des métadonnées créées
    print("\n4️⃣ Métadonnées générées:")
    cmd = f"docker exec {container} sh -c 'find \"/config/Library/Application Support/Plex Media Server/Metadata\" -type d -maxdepth 2 2>/dev/null | wc -l'"
    result = execute_command(ip, cmd, capture_output=True, check=False)
    count = result.stdout.strip() if result.stdout else "0"
    print(f"   Dossiers de métadonnées créés: {count}")

    # 5. Dernières lignes des logs INTERNES du scanner
    print("\n5️⃣ Activité détaillée du scanner (Interne):")
    log_dir = "/config/Library/Application Support/Plex Media Server/Logs/"

    # 5.1 Lister les fichiers disponibles pour comprendre la rotation
    print("   📂 Fichiers de logs disponibles :")
    ls_cmd = f"docker exec {container} ls -lh '{log_dir}' | grep 'Scanner'"
    execute_command(ip, ls_cmd, check=False)

    # 5.2 Lire le fichier principal
    log_path = f"{log_dir}/Plex Media Scanner.log"

    # On vérifie d'abord si le fichier existe
    check_cmd = f"docker exec {container} test -f '{log_path}' && echo 'OK' || echo 'NOK'"
    check_res = execute_command(ip, check_cmd, capture_output=True, check=False)

    if 'OK' in check_res.stdout:
        # On récupère les 30 dernières lignes brutes
        cmd = f"docker exec {container} tail -n 30 '{log_path}'"
        result = execute_command(ip, cmd, capture_output=True, check=False)
        if result.stdout:
            print("   --- Début de l'extrait ---")
            print(result.stdout.strip())
            print("   --- Fin de l'extrait ---")
        else:
            print("   Fichier de log vide.")
    else:
        print("   ⚠️ Fichier de log 'Plex Media Scanner.log' introuvable. Le scanner n'a peut-être jamais démarré.")

    # 6. Test rapide de l'API
    print("\n6️⃣ Réponse de l'API Plex:")
    if plex_token:
        sections = get_plex_sections(ip, container, plex_token)
        for section in sections:
            # Récupérer le nombre d'items
            cmd = f"curl -s 'http://localhost:32400/library/sections/{section}/all?X-Plex-Token={plex_token}' | grep -c 'ratingKey'"
            result = execute_command(ip, cmd, capture_output=True, check=False)
            count = result.stdout.strip() if result.stdout else "0"
            print(f"   Section {section}: {count} items")


def debug_library_creation(ip, container='plex', plex_token=None):
    """
    Diagnostic spécifique de la création des bibliothèques
    """
    print("\n🔍 DIAGNOSTIC CRÉATION BIBLIOTHÈQUES")
    print("=" * 50)

    # 1. Vérifier que Plex répond avec plus de détails
    health_cmd = "curl -s -o /dev/null -w '%{http_code}' http://localhost:32400/identity || echo 'FAIL'"
    result = docker_exec(ip, container, health_cmd, capture_output=True, check=False)
    http_code = result.stdout.strip()
    print(f"Health check HTTP: {http_code}")

    # Vérifier le contenu de la réponse
    content_cmd = "curl -s http://localhost:32400/identity | head -5 || echo 'NO_RESPONSE'"
    content_result = docker_exec(ip, container, content_cmd, capture_output=True, check=False)
    print(f"Health content: {content_result.stdout}")

    # 2. Vérifier le token avec plus de détails
    if plex_token:
        token_test = f"curl -s -o /dev/null -w '%{{http_code}}' 'http://localhost:32400/library/sections' -H 'X-Plex-Token: {plex_token}'"
        result = docker_exec(ip, container, token_test, capture_output=True, check=False)
        print(f"Test token HTTP: {result.stdout}")

        # Vérifier le contenu avec token
        sections_cmd = f"curl -s 'http://localhost:32400/library/sections' -H 'X-Plex-Token: {plex_token}'"
        sections_result = docker_exec(ip, container, sections_cmd, capture_output=True, check=False)
        print(f"Sections API response: {sections_result.stdout[:200]}...")

    # 3. Vérifier les dossiers Media
    media_check = "ls -la /Media | head -10"
    result = docker_exec(ip, container, media_check, capture_output=True, check=False)
    print(f"Dossier /Media: {'✅ Accessible' if result.stdout else '❌ Vide ou inaccessible'}")
    if result.stdout:
        print(f"Contenu: {result.stdout}")


def debug_plex_scan_status(ip, container='plex', plex_token=None, verbose=False):
    """
    Diagnostic détaillé de l'état du scan Plex
    """
    if not verbose:
        return

    print("\n🔍 DIAGNOSTIC SCAN PLEX")
    print("=" * 50)

    # 1. Vérifier les sections
    section_ids = get_plex_sections(ip, container, plex_token)
    print(f"Sections configurées: {len(section_ids)}")

    for section_id in section_ids:
        api_url = f"http://localhost:32400/library/sections/{section_id}"
        if plex_token:
            curl_cmd = f"curl -s '{api_url}' -H 'X-Plex-Token: {plex_token}'"
        else:
            curl_cmd = f"curl -s '{api_url}'"

        result = docker_exec(ip, container, curl_cmd, capture_output=True, check=False)
        if result.returncode == 0 and result.stdout:
            # Extraire le nom de la section si possible
            import re
            name_match = re.search(r'title="([^"]+)"', result.stdout)
            section_name = name_match.group(1) if name_match else "Inconnu"
            print(f"Section {section_id}: {section_name}")
        else:
            print(f"Section {section_id}: Erreur de récupération")

    # 2. Vérifier les processus (avec check=False)
    print("\nProcessus Plex en cours:")
    processes_cmd = "docker exec plex ps aux | grep -i plex || true"
    result = execute_command(ip, processes_cmd, capture_output=True, check=False)
    print(result.stdout if result.stdout else "Aucun processus Plex trouvé")

    # 3. Vérifier les logs récents (avec check=False)
    print("\nLogs Plex récents:")
    logs_cmd = "docker logs plex --tail 30 2>&1 | grep -E '(scan|library|error|fail)' || echo 'Aucun log pertinent récent'"
    result = execute_command(ip, logs_cmd, capture_output=True, check=False)
    print(result.stdout)

    # 4. Vérifier l'accessibilité des dossiers montés (maintenant /Media)
    print("\nVérification des dossiers montés:")
    mount_check = f"docker exec {container} ls -la /Media | head -10"
    result = execute_command(ip, mount_check, capture_output=True, check=False)
    print(result.stdout if result.stdout else "⚠️ Impossible de lire /Media")


def get_plex_sections(ip, container='plex', plex_token=None):
    """
    Récupère la liste des IDs de sections (bibliothèques) Plex.
    Version améliorée avec debug.
    """
    api_url = "http://localhost:32400/library/sections"

    if plex_token:
        curl_cmd = f"curl -s '{api_url}' -H 'X-Plex-Token: {plex_token}'"
    else:
        curl_cmd = f"curl -s '{api_url}'"

    result = docker_exec(ip, container, curl_cmd, capture_output=True, check=False)

    if result.returncode == 0 and result.stdout and 'Directory' in result.stdout:
        # Extraire les IDs des sections
        import re
        section_ids = re.findall(r'key="(\d+)"', result.stdout)
        return section_ids
    else:
        print(f"⚠️  Aucune section trouvée dans la réponse: {result.stdout}")
        return []


def trigger_sonic_analysis(ip, music_section_id, container='plex'):
    """
    Lance l'analyse Sonic via CLI (arrière-plan).
    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        plex_token: Token d'authentification Plex (X-Plex-Token)
        music_section_id: ID de la section musique à analyser
    """
    print(f"🎹 Lancement de l'analyse Sonic pour section {music_section_id}...")

    # Note: On n'utilise PAS --force ici car cela déclencherait un refresh
    # metadata complet avant l'analyse audio. Le refresh doit être fait
    # séparément en amont si nécessaire.
    sonic_cmd = (
        f"nohup '/usr/lib/plexmediaserver/Plex Media Scanner' "
        f"--analyze --section {music_section_id} --server-action sonic "
        f"</dev/null >/dev/null 2>&1 &"
    )
    docker_exec(ip, container, sonic_cmd, check=False)
    time.sleep(5)

    print("✅ Analyse Sonic lancée en arrière-plan.")


def is_sonic_running(ip, container='plex'):
    """Vérifie si le processus Sonic est actif."""
    cmd = "pgrep -f 'server-action sonic' > /dev/null && echo 'running' || echo 'stopped'"
    result = docker_exec(ip, container, cmd, capture_output=True, check=False)
    return 'running' in result.stdout


def get_container_cpu(ip, container='plex'):
    """Retourne le % CPU du conteneur Docker."""
    cpu_result = execute_command(
        ip,
        f"docker stats {container} --no-stream --format '{{{{.CPUPerc}}}}'",
        capture_output=True, check=False
    )
    cpu_str = cpu_result.stdout.strip().replace('%', '')
    return float(cpu_str) if cpu_str and cpu_str != 'N/A' else 0.0


def wait_plex_stabilized(ip, container, plex_token, cooldown_checks=3, check_interval=60, cpu_threshold=20.0, timeout=1800):
    """
    Attend que Plex soit complètement stabilisé (aucune activité de fond).

    Utilisé entre le refresh metadata et le lancement de Sonic pour s'assurer
    que toutes les tâches de fond (téléchargement images, paroles, matching)
    sont terminées.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        plex_token: Token d'authentification Plex
        cooldown_checks: Nombre de checks consécutifs "idle" requis (défaut: 3)
        check_interval: Intervalle entre checks en secondes (défaut: 60)
        cpu_threshold: Seuil CPU en % sous lequel Plex est considéré idle (défaut: 20%)
        timeout: Timeout absolu en secondes (défaut: 1800 = 30min)

    Returns:
        bool: True si stabilisé, False si timeout
    """
    print(f"⏳ [{time.strftime('%H:%M:%S')}] Attente stabilisation Plex (cooldown: {cooldown_checks} × {check_interval}s)...")

    start_time = time.time()
    idle_count = 0

    while time.time() - start_time < timeout:
        # Vérifier les activités globales
        activities_cmd = f"curl -s 'http://localhost:32400/activities' -H 'X-Plex-Token: {plex_token}'"
        activities_result = docker_exec(ip, container, activities_cmd, capture_output=True, check=False)
        active_tasks = activities_result.stdout.count('<Activity') if activities_result.stdout else 0

        # Vérifier les processus Scanner
        scanner_cmd = "pgrep -f 'Plex Media Scanner' > /dev/null && echo 'running' || echo 'stopped'"
        scanner_result = docker_exec(ip, container, scanner_cmd, capture_output=True, check=False)
        scanner_running = 'running' in scanner_result.stdout

        # Vérifier le CPU
        cpu_percent = get_container_cpu(ip, container)

        is_idle = (active_tasks == 0 and not scanner_running and cpu_percent < cpu_threshold)

        if is_idle:
            idle_count += 1
            print(f"   [{time.strftime('%H:%M:%S')}] ⏸️  Idle {idle_count}/{cooldown_checks} (CPU: {cpu_percent:.1f}%)")
            if idle_count >= cooldown_checks:
                print(f"   [{time.strftime('%H:%M:%S')}] ✅ Plex stabilisé après {int(time.time() - start_time)}s")
                return True
        else:
            if idle_count > 0:
                idle_count = 0  # Reset si activité reprend
            status_parts = []
            if active_tasks > 0:
                status_parts.append(f"{active_tasks} tâches")
            if scanner_running:
                status_parts.append("scanner actif")
            status_parts.append(f"CPU: {cpu_percent:.1f}%")
            print(f"   [{time.strftime('%H:%M:%S')}] 🔄 Activité: {', '.join(status_parts)}")

        time.sleep(check_interval)

    print(f"   [{time.strftime('%H:%M:%S')}] ⚠️  Timeout stabilisation ({timeout}s)")
    return False


def get_section_activity(ip, container, plex_token, section_id):
    """
    Vérifie l'activité d'UNE section spécifique avec détails.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        plex_token: Token d'authentification Plex
        section_id: ID de la section à vérifier

    Returns:
        dict: {
            'refreshing': bool,
            'activities': int,
            'activity_details': list,  # Liste des activités avec détails
            'scanner_running': bool,
            'is_idle': bool
        }
    """
    # Vérifier le flag refreshing sur la section
    cmd = f"curl -s 'http://localhost:32400/library/sections/{section_id}' -H 'X-Plex-Token: {plex_token}'"
    result = docker_exec(ip, container, cmd, capture_output=True, check=False)
    refreshing = 'refreshing="1"' in result.stdout if result.stdout else False

    # Récupérer les activités détaillées pour cette section
    activities_cmd = f"curl -s 'http://localhost:32400/activities' -H 'X-Plex-Token: {plex_token}'"
    activities_result = docker_exec(ip, container, activities_cmd, capture_output=True, check=False)

    activities = 0
    activity_details = []

    if activities_result.stdout:
        # Parser chaque activité liée à cette section
        for match in re.finditer(r'<Activity[^>]+librarySectionID="' + str(section_id) + r'"[^>]*>', activities_result.stdout):
            tag = match.group(0)
            activities += 1

            # Extraire les détails
            title_match = re.search(r'title="([^"]+)"', tag)
            type_match = re.search(r'type="([^"]+)"', tag)
            progress_match = re.search(r'progress="([^"]+)"', tag)
            subtitle_match = re.search(r'subtitle="([^"]+)"', tag)

            detail = {
                'title': title_match.group(1) if title_match else 'Inconnu',
                'type': type_match.group(1) if type_match else 'unknown',
                'progress': int(progress_match.group(1)) if progress_match else 0,
                'subtitle': subtitle_match.group(1) if subtitle_match else ''
            }
            activity_details.append(detail)

    # Vérifier les processus Scanner
    scanner_cmd = "pgrep -f 'Plex Media Scanner' > /dev/null && echo 'running' || echo 'stopped'"
    scanner_result = docker_exec(ip, container, scanner_cmd, capture_output=True, check=False)
    scanner_running = 'running' in scanner_result.stdout

    is_idle = not (refreshing or activities > 0 or scanner_running)

    return {
        'refreshing': refreshing,
        'activities': activities,
        'activity_details': activity_details,
        'scanner_running': scanner_running,
        'is_idle': is_idle
    }


def wait_section_idle(ip, container, plex_token, section_id, section_type=None, phase='scan', config_path=None, timeout=3600, check_interval=30, consecutive_idle=3, health_check_fn=None):
    """
    Attend qu'une section soit VRAIMENT inactive (API + CPU).

    Combine la détection d'activité Plex (API + scanner process) avec le monitoring
    CPU du conteneur pour éviter les faux idle quand FFMPEG ou le Butler travaillent
    encore en arrière-plan.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        plex_token: Token d'authentification Plex
        section_id: ID de la section
        section_type: Type de section ('artist', 'movie', 'show', 'photo') pour timeout adaptatif
        phase: 'scan' ou 'analyze' - personnalise les messages et paramètres
        config_path: Chemin config Plex (optionnel, pour comptage progression DB)
        timeout: Timeout de sécurité en secondes (défaut: 3600)
        check_interval: Intervalle entre checks (défaut: 30s)
        consecutive_idle: Nombre de checks idle consécutifs requis (défaut: 3)
        health_check_fn: Fonction optionnelle pour vérifier la santé du montage
                         Doit retourner {'healthy': bool, 'error': str|None}

    Returns:
        bool: True si idle atteint, False si timeout ou health check échoué
    """
    # Icônes et messages selon la phase
    if phase == 'analyze':
        phase_icon = "🔬"
        phase_msg = "Analyse en cours"
    else:  # scan (défaut)
        phase_icon = "🔍"
        phase_msg = "Scan en cours"

    # Paramètres adaptatifs par phase (sauf si le caller a passé des valeurs explicites)
    caller_explicit_timeout = (timeout != 3600)
    caller_explicit_interval = (check_interval != 30)
    caller_explicit_idle = (consecutive_idle != 3)

    if phase == 'analyze':
        if not caller_explicit_interval:
            check_interval = 120  # 2min entre checks (analyse longue)
        if not caller_explicit_idle:
            consecutive_idle = 5  # 5 × 120s = 10min de silence confirmé

    # Timeouts de sécurité par type de section (si le caller n'a pas passé de valeur explicite)
    SAFETY_TIMEOUTS = {
        'movie': 14400,   # 4h
        'show': 7200,     # 2h
        'photo': 28800,   # 8h
        'artist': 14400,  # 4h
    }
    if not caller_explicit_timeout:
        timeout = SAFETY_TIMEOUTS.get(section_type, 7200)

    # Seuil CPU pour considérer le conteneur idle
    cpu_threshold = 20.0

    # Grace period : ignorer les premiers checks pour laisser le Scanner démarrer
    grace_period = 60

    timeout_str = f"{timeout//3600}h" if timeout >= 3600 else f"{timeout//60}min"
    idle_window = check_interval * consecutive_idle
    idle_window_str = f"{idle_window//60}min" if idle_window >= 60 else f"{idle_window}s"
    print(f"⏳ [{time.strftime('%H:%M:%S')}] Attente section {section_id} (timeout: {timeout_str}, idle: {idle_window_str})")

    # Comptage initial si config_path fourni
    initial_count = 0
    if config_path and section_type:
        initial_count = get_section_item_count_from_db(ip, config_path, section_id, section_type)

    start_time = time.time()
    idle_count = 0

    while time.time() - start_time < timeout:
        elapsed = int(time.time() - start_time)

        # Vérifier la santé du montage si callback fourni
        if health_check_fn:
            health = health_check_fn()
            if not health.get('healthy', True):
                print(f"   [{time.strftime('%H:%M:%S')}] ⚠️  Health check failed: {health.get('error')}")
                return False

        activity = get_section_activity(ip, container, plex_token, section_id)
        cpu_percent = get_container_cpu(ip, container)

        # Idle = API idle ET CPU bas (évite faux idle quand FFMPEG/Butler travaille)
        is_truly_idle = activity['is_idle'] and cpu_percent < cpu_threshold

        # Grace period : ne pas compter les idles dans les premières secondes
        in_grace = elapsed < grace_period

        if is_truly_idle and not in_grace:
            idle_count += 1
            print(f"   [{time.strftime('%H:%M:%S')}] ⏸️  Idle {idle_count}/{consecutive_idle} (CPU: {cpu_percent:.1f}%)")

            if idle_count >= consecutive_idle:
                print(f"   [{time.strftime('%H:%M:%S')}] ✅ Section {section_id} inactive après {elapsed//60}min")
                return True
        else:
            idle_count = 0
            status_parts = []

            if in_grace:
                status_parts.append("⏳ Grace period")

            if activity['refreshing']:
                status_parts.append("🔄 Refreshing")

            # Message différencié selon la phase (remplace "Scanner actif")
            if activity['scanner_running']:
                status_parts.append(f"{phase_icon} {phase_msg}")

            # Afficher les détails des activités API Plex
            if activity['activity_details']:
                for detail in activity['activity_details']:
                    progress_str = f" ({detail['progress']}%)" if detail['progress'] > 0 else ""
                    subtitle_str = f" - {detail['subtitle']}" if detail['subtitle'] else ""
                    status_parts.append(f"📋 {detail['title']}{progress_str}{subtitle_str}")
            elif activity['activities'] > 0:
                status_parts.append(f"📋 {activity['activities']} activité(s)")

            # Comptage de progression DB (si config_path fourni)
            if config_path and section_type and initial_count > 0:
                current_count = get_section_item_count_from_db(ip, config_path, section_id, section_type)
                if phase == 'scan':
                    # Pendant scan: montrer le delta d'items ajoutés
                    delta = current_count - initial_count
                    status_parts.append(f"+{delta} items")
                else:
                    # Pendant analyze: montrer le pourcentage analysé
                    analyzed = get_section_analyzed_count_from_db(ip, config_path, section_id, section_type)
                    pct = int(analyzed / current_count * 100) if current_count > 0 else 0
                    status_parts.append(f"{analyzed}/{current_count} ({pct}%)")

            # Toujours afficher le CPU
            status_parts.append(f"CPU: {cpu_percent:.1f}%")

            elapsed_str = f"{elapsed//60:02d}:{elapsed%60:02d}"
            print(f"   [{time.strftime('%H:%M:%S')}] {elapsed_str} | {' | '.join(status_parts)}")

        time.sleep(check_interval)

    elapsed = int(time.time() - start_time)
    print(f"   [{time.strftime('%H:%M:%S')}] 🚨 Timeout de sécurité après {elapsed//60}min (anomalie)")
    return False


def get_section_item_count_from_db(ip, config_path, section_id, section_type):
    """
    Compte les items d'une section en DB.

    Args:
        ip: 'localhost' ou IP remote
        config_path: Chemin vers la config Plex (contient la DB)
        section_id: ID de la section
        section_type: Type de section ('artist', 'movie', 'show', 'photo')

    Returns:
        int: Nombre d'items dans la section
    """
    db_path = f"{config_path}/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

    # Mapping type de section → metadata_type en DB
    type_map = {
        'artist': 10,   # tracks (pistes audio)
        'movie': 1,     # movies
        'show': 4,      # episodes
        'photo': 13     # photos
    }

    metadata_type = type_map.get(section_type)
    if not metadata_type:
        return 0

    sql_query = f"SELECT COUNT(*) FROM metadata_items WHERE library_section_id={section_id} AND metadata_type={metadata_type}"

    cmd = f"sqlite3 '{db_path}' \"{sql_query}\""
    result = execute_command(ip, cmd, capture_output=True, check=False)

    try:
        return int(result.stdout.strip())
    except:
        return 0


def get_section_analyzed_count_from_db(ip, config_path, section_id, section_type):
    """
    Compte les items analysés d'une section (thumbnails générés, metadata enrichies).

    Pour les films/séries: items avec thumb généré
    Pour les photos: items avec thumb généré
    Pour la musique: utiliser get_sonic_count_from_db() à la place

    Args:
        ip: 'localhost' ou IP remote
        config_path: Chemin vers la config Plex
        section_id: ID de la section
        section_type: Type de section

    Returns:
        int: Nombre d'items analysés
    """
    db_path = f"{config_path}/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

    # Mapping type de section → metadata_type en DB
    type_map = {
        'artist': 10,
        'movie': 1,
        'show': 4,
        'photo': 13
    }

    metadata_type = type_map.get(section_type)
    if not metadata_type:
        return 0

    # Compter les items avec un thumb généré (signe d'analyse complète)
    # user_thumb_url doit être non-NULL ET non-vide pour être considéré comme analysé
    sql_query = f"""
        SELECT COUNT(*) FROM metadata_items
        WHERE library_section_id={section_id}
        AND metadata_type={metadata_type}
        AND user_thumb_url IS NOT NULL AND user_thumb_url != ''
    """

    cmd = f"sqlite3 '{db_path}' \"{sql_query}\""
    result = execute_command(ip, cmd, capture_output=True, check=False)

    try:
        return int(result.stdout.strip())
    except:
        return 0


def get_sonic_count_from_db(ip, config_path):
    """
    Compte les pistes avec analyse Sonic directement en DB.

    Args:
        ip: 'localhost' ou IP remote
        config_path: Chemin vers la config Plex (contient la DB)

    Returns:
        int: Nombre de pistes analysées par Sonic
    """
    db_path = f"{config_path}/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

    sql_query = """
        SELECT COUNT(*) FROM metadata_items
        WHERE metadata_type=10
        AND extra_data LIKE '%ms:musicAnalysisVersion%';
    """

    if ip == 'localhost':
        cmd = f"sqlite3 '{db_path}' \"{sql_query}\""
    else:
        cmd = f"sqlite3 '{db_path}' \"{sql_query}\""

    result = execute_command(ip, cmd, capture_output=True, check=False)

    try:
        return int(result.stdout.strip())
    except:
        return 0


def get_unanalyzed_track_count(ip, config_path, section_id=None):
    """
    Compte les pistes NON analysées par Sonic dans une section.

    Args:
        ip: 'localhost' ou IP remote
        config_path: Chemin vers la config Plex
        section_id: ID de la section (optionnel, toutes sections si None)

    Returns:
        dict: {
            'total_tracks': int,
            'analyzed_tracks': int,
            'unanalyzed_tracks': int,
            'percent_complete': float
        }
    """
    db_path = f"{config_path}/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

    # Requête pour le total des pistes
    if section_id:
        total_query = f"""
            SELECT COUNT(*) FROM metadata_items
            WHERE metadata_type=10
            AND library_section_id={section_id};
        """
        analyzed_query = f"""
            SELECT COUNT(*) FROM metadata_items
            WHERE metadata_type=10
            AND library_section_id={section_id}
            AND extra_data LIKE '%ms:musicAnalysisVersion%';
        """
    else:
        total_query = """
            SELECT COUNT(*) FROM metadata_items
            WHERE metadata_type=10;
        """
        analyzed_query = """
            SELECT COUNT(*) FROM metadata_items
            WHERE metadata_type=10
            AND extra_data LIKE '%ms:musicAnalysisVersion%';
        """

    # Exécuter les requêtes
    total_result = execute_command(ip, f"sqlite3 '{db_path}' \"{total_query}\"", capture_output=True, check=False)
    analyzed_result = execute_command(ip, f"sqlite3 '{db_path}' \"{analyzed_query}\"", capture_output=True, check=False)

    try:
        total = int(total_result.stdout.strip())
        analyzed = int(analyzed_result.stdout.strip())
        unanalyzed = total - analyzed
        percent = (analyzed / total * 100) if total > 0 else 100.0

        return {
            'total_tracks': total,
            'analyzed_tracks': analyzed,
            'unanalyzed_tracks': unanalyzed,
            'percent_complete': percent
        }
    except:
        return {
            'total_tracks': 0,
            'analyzed_tracks': 0,
            'unanalyzed_tracks': 0,
            'percent_complete': 100.0
        }


def wait_sonic_complete(ip, config_path, section_id, container='plex', timeout=86400, check_interval=120, health_check_fn=None):
    """
    Attend la fin du Sonic avec indicateur DB fiable.

    Args:
        ip: 'localhost' ou IP remote
        config_path: Chemin vers la config Plex
        section_id: ID de la section musique
        container: Nom du conteneur
        timeout: Timeout absolu en secondes (défaut: 86400 = 24h)
        check_interval: Intervalle entre checks (défaut: 120s = 2min)
        health_check_fn: Fonction optionnelle pour vérifier la santé du montage
                         Doit retourner {'healthy': bool, 'error': str|None}

    Returns:
        dict: {
            'success': bool,
            'initial_count': int,
            'final_count': int,
            'delta': int,
            'duration_minutes': int,
            'reason': str  # 'completed', 'stall', 'timeout', 'already_complete', 'health_check_failed'
        }
    """
    # Vérifier d'abord s'il y a des pistes à analyser
    status = get_unanalyzed_track_count(ip, config_path, section_id)
    if status['unanalyzed_tracks'] == 0:
        print(f"✅ [{time.strftime('%H:%M:%S')}] Toutes les pistes sont déjà analysées ({status['analyzed_tracks']}/{status['total_tracks']})")
        return {
            'success': True,
            'initial_count': status['analyzed_tracks'],
            'final_count': status['analyzed_tracks'],
            'delta': 0,
            'duration_minutes': 0,
            'reason': 'already_complete'
        }

    print(f"🎹 [{time.strftime('%H:%M:%S')}] Surveillance de l'analyse Sonic (section {section_id})...")
    print(f"   [{time.strftime('%H:%M:%S')}] 📊 Pistes à analyser: {status['unanalyzed_tracks']} sur {status['total_tracks']} ({status['percent_complete']:.1f}% déjà fait)")

    start_time = time.time()
    initial_count = get_sonic_count_from_db(ip, config_path)
    print(f"   [{time.strftime('%H:%M:%S')}] 📊 Pistes déjà analysées: {initial_count}")

    last_count = initial_count
    stall_count = 0
    max_stall = 30  # 30 checks sans delta = 1h sans progression

    while time.time() - start_time < timeout:
        elapsed = int(time.time() - start_time)

        # Vérifier la santé du montage si callback fourni
        if health_check_fn:
            health = health_check_fn()
            if not health.get('healthy', True):
                duration_minutes = int(elapsed / 60)
                print(f"\n   [{time.strftime('%H:%M:%S')}] ⚠️  Health check failed: {health.get('error')}")
                final_count = get_sonic_count_from_db(ip, config_path)
                return {
                    'success': False,
                    'initial_count': initial_count,
                    'final_count': final_count,
                    'delta': final_count - initial_count,
                    'duration_minutes': duration_minutes,
                    'reason': 'health_check_failed'
                }

        # Compter les pistes analysées
        current_count = get_sonic_count_from_db(ip, config_path)
        delta_since_last = current_count - last_count
        delta_total = current_count - initial_count

        # Vérifier le CPU du conteneur
        cpu_percent = get_container_cpu(ip, container)

        # Vérifier si Sonic tourne
        sonic_running = is_sonic_running(ip, container)

        # Affichage progression
        eta_str = "calcul..."
        if delta_total > 0 and elapsed > 0:
            # Estimation grossière basée sur le rythme actuel
            rate = delta_total / (elapsed / 60)  # pistes/min
            if rate > 0:
                # On ne peut pas estimer un ETA sans connaître le total de pistes
                eta_str = f"{rate:.1f} pistes/min"

        status_parts = [
            f"Analysées: {current_count} (+{delta_total})",
            f"Delta: +{delta_since_last}",
            f"CPU: {cpu_percent:.1f}%",
            f"Sonic: {'🎹' if sonic_running else '⏹️ '}",
            f"Rythme: {eta_str}"
        ]

        elapsed_str = f"{elapsed//60:02d}:{elapsed%60:02d}"
        print(f"   [{time.strftime('%H:%M:%S')}] {elapsed_str} | {' | '.join(status_parts)}")

        # Détection de stall
        if delta_since_last == 0 and cpu_percent < 5.0 and not sonic_running:
            stall_count += 1
            print(f"   [{time.strftime('%H:%M:%S')}] ⏸️  Stall détecté: {stall_count}/{max_stall}")

            if stall_count >= max_stall:
                duration_minutes = int(elapsed / 60)
                print(f"\n   [{time.strftime('%H:%M:%S')}] ✅ Analyse Sonic terminée (stall confirmé)")
                return {
                    'success': True,
                    'initial_count': initial_count,
                    'final_count': current_count,
                    'delta': delta_total,
                    'duration_minutes': duration_minutes,
                    'reason': 'stall'
                }
        else:
            stall_count = 0

        last_count = current_count
        time.sleep(check_interval)

    # Timeout absolu
    duration_minutes = int((time.time() - start_time) / 60)
    final_count = get_sonic_count_from_db(ip, config_path)
    print(f"\n   [{time.strftime('%H:%M:%S')}] ⚠️  Timeout absolu atteint ({timeout//3600}h)")

    return {
        'success': False,
        'initial_count': initial_count,
        'final_count': final_count,
        'delta': final_count - initial_count,
        'duration_minutes': duration_minutes,
        'reason': 'timeout'
    }


def warm_vfs_cache(ip, config_path, section_id, mount_point):
    """
    Préchauffe le cache VFS rclone en lisant les premiers octets de chaque fichier.

    Lit 64 Ko de chaque fichier média d'une section pour forcer rclone à créer
    l'entrée VFS cache. Évite les ENOENT quand FFMPEG ouvre les fichiers en parallèle
    pendant l'analyse Plex.

    Args:
        ip: 'localhost' ou IP remote
        config_path: Chemin vers la config Plex (contient la DB)
        section_id: ID de la section à préchauffer
        mount_point: Point de montage S3 sur l'hôte (ex: '/opt/media' ou 'tmp/s3-media')

    Returns:
        dict: {'total': N, 'warmed': N, 'errors': N}
    """
    print(f"🔄 Préchauffage du cache VFS pour section {section_id}...")

    db_path = f"{config_path}/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"

    # Lister les fichiers de la section depuis la DB
    query = (
        "SELECT mp.file FROM media_parts mp "
        "JOIN media_items mi ON mp.media_item_id = mi.id "
        "JOIN metadata_items mdi ON mi.metadata_item_id = mdi.id "
        f"WHERE mdi.library_section_id = {section_id};"
    )

    result = execute_command(ip, f"sqlite3 '{db_path}' \"{query}\"", capture_output=True, check=False)

    if not result.stdout or not result.stdout.strip():
        print(f"   ⚠️  Aucun fichier trouvé en DB pour section {section_id}")
        return {'total': 0, 'warmed': 0, 'errors': 0}

    files = result.stdout.strip().split('\n')
    total = len(files)
    print(f"   📊 {total} fichiers à préchauffer")

    # Convertir les chemins DB (/Media/...) en chemins hôte (mount_point/...)
    # et écrire dans un fichier temporaire pour xargs
    # On utilise sed directement sur la sortie sqlite3 pour éviter les problèmes de quotes
    mount_escaped = mount_point.rstrip('/').replace('/', '\\/')
    write_cmd = f"sqlite3 '{db_path}' \"{query}\" | sed 's/^\\/Media\\//{mount_escaped}\\//' > /tmp/vfs_warmup_files.txt"
    execute_command(ip, write_cmd, check=False)

    # Lire 64K de chaque fichier en parallèle (4 workers)
    # -d'\\n' force xargs à séparer par ligne (gère les espaces dans les noms)
    warmup_cmd = (
        "xargs -d'\\n' -P4 -I{} sh -c 'head -c 65536 \"{}\" > /dev/null 2>&1 && echo OK || echo FAIL' "
        "< /tmp/vfs_warmup_files.txt"
    )
    warmup_result = execute_command(ip, warmup_cmd, capture_output=True, check=False, timeout=600)

    # Compter les résultats
    lines = warmup_result.stdout.strip().split('\n') if warmup_result.stdout else []
    warmed = sum(1 for l in lines if l.strip() == 'OK')
    errors = sum(1 for l in lines if l.strip() == 'FAIL')

    # Nettoyage
    execute_command(ip, "rm -f /tmp/vfs_warmup_files.txt", check=False)

    if errors > 0:
        print(f"   ⚠️  Préchauffage terminé : {warmed}/{total} OK, {errors} erreurs")
    else:
        print(f"   ✅ Préchauffage terminé : {warmed}/{total} fichiers en cache")

    return {'total': total, 'warmed': warmed, 'errors': errors}


def trigger_section_scan(ip, container, plex_token, section_id, force=False):
    """
    Déclenche le scan d'UNE section.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        plex_token: Token d'authentification Plex
        section_id: ID de la section
        force: Si True, force le re-scan complet

    Returns:
        bool: True si succès, False sinon
    """
    force_param = "force=1" if force else "force=0"
    api_url = f"http://localhost:32400/library/sections/{section_id}/refresh?{force_param}"

    cmd = f"curl -s -X GET '{api_url}' -H 'X-Plex-Token: {plex_token}'"
    result = docker_exec(ip, container, cmd, capture_output=True, check=False)

    if result.returncode == 0:
        print(f"   ✅ Scan déclenché pour section {section_id}")
        return True
    else:
        print(f"   ❌ Échec du scan pour section {section_id}")
        return False


def trigger_section_analyze(ip, container, plex_token, section_id):
    """
    Déclenche l'analyse d'une section spécifique via API.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        plex_token: Token d'authentification Plex
        section_id: ID de la section à analyser

    Returns:
        bool: True si succès, False sinon
    """
    api_url = f"http://localhost:32400/library/sections/{section_id}/analyze"

    cmd = f"curl -s -X PUT '{api_url}' -H 'X-Plex-Token: {plex_token}'"
    result = docker_exec(ip, container, cmd, capture_output=True, check=False)

    if result.returncode == 0:
        print(f"   ✅ Analyse déclenchée pour section {section_id}")
        return True
    else:
        print(f"   ❌ Échec de l'analyse pour section {section_id}")
        return False


def export_intermediate(ip, container, config_path, output_dir, label="checkpoint"):
    """
    Export de sécurité après une phase critique (export à chaud, sans arrêter Plex).

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        config_path: Chemin vers la config Plex
        output_dir: Répertoire de destination
        label: Label pour le nom d'archive

    Returns:
        str: Chemin de l'archive ou None si échec
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"plex_{label}_{timestamp}.tar.gz"

    print(f"💾 Export intermédiaire ({label})...")

    if ip == 'localhost':
        archive_path = f"{output_dir}/{archive_name}"
    else:
        archive_path = f"/tmp/{archive_name}"

    # Export DB only (pas de Metadata, trop lourd)
    db_path = f"{config_path}/Library/Application Support/Plex Media Server/Plug-in Support/Databases"

    tar_cmd = f"""
        tar -czf {archive_path} \
        --ignore-failed-read \
        -C '{config_path}/Library/Application Support/Plex Media Server' \
        'Plug-in Support/Databases' \
        2>/dev/null || echo 'Erreur tar'
    """

    result = execute_command(ip, tar_cmd, check=False, capture_output=True)

    # Vérifier que l'archive existe
    if ip == 'localhost':
        if os.path.exists(archive_path):
            size_mb = os.path.getsize(archive_path) / (1024*1024)
            print(f"   ✅ Export intermédiaire créé: {archive_path} ({size_mb:.1f} MB)")
            return archive_path
    else:
        check_result = execute_command(ip, f"test -f {archive_path} && ls -lh {archive_path}", capture_output=True, check=False)
        if check_result.returncode == 0:
            print(f"   ✅ Export intermédiaire créé: {archive_path}")
            print(f"      {check_result.stdout.strip()}")
            return archive_path

    print(f"   ⚠️  Échec de l'export intermédiaire")
    return None


def export_metadata(ip, container='plex', archive_name=None, config_path=None):
    """
    Exporte la base de données et les métadonnées dans une archive.

    Args:
        ip: 'localhost' ou IP remote
        container: Nom du conteneur
        archive_name: Nom de l'archive (auto-généré si None)
        config_path: Chemin du volume config Plex (REQUIS en local, optionnel en remote)

    Returns:
        str: Chemin de l'archive, ou None en cas d'échec
    """
    if not archive_name:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"plex_metadata_{timestamp}.tar.gz"

    print(f"\n📦 Export des métadonnées Plex...")

    try:
        if ip == 'localhost':
            # === LOCAL : config_path DOIT être fourni ===
            if not config_path:
                raise ValueError("config_path requis pour export local")

            base_path = f"{config_path}/Library/Application Support/Plex Media Server"
            archive_path = f"./{archive_name}"

            tar_cmd = f"""
                tar -czf {archive_path} \
                --ignore-failed-read \
                -C '{base_path}' \
                'Plug-in Support/Databases' \
                'Metadata' \
                'Media' 2>/dev/null || \
                tar -czf {archive_path} \
                --ignore-failed-read \
                -C '{base_path}' \
                'Plug-in Support/Databases' \
                'Metadata' 2>/dev/null
            """

            execute_command('localhost', tar_cmd)

            if os.path.exists(archive_path):
                size_mb = os.path.getsize(archive_path) / (1024*1024)
                print(f"   ✅ Archive créée : {archive_path} ({size_mb:.1f} MB)")
                return archive_path
            else:
                print(f"   ❌ Archive non créée")
                return None
        else:
            # === REMOTE : valeur par défaut ===
            if not config_path:
                config_path = "/opt/plex_data/config"

            base_path = f"{config_path}/Library/Application Support/Plex Media Server"
            archive_path = f"/root/{archive_name}"

            tar_cmd = f"""
                tar -czf {archive_path} \
                --ignore-failed-read \
                -C '{base_path}' \
                'Plug-in Support/Databases' \
                'Metadata' \
                'Media' 2>/dev/null || \
                tar -czf {archive_path} \
                --ignore-failed-read \
                -C '{base_path}' \
                'Plug-in Support/Databases' \
                'Metadata'
            """

            execute_command(ip, tar_cmd)

            check_cmd = f"ls -lh {archive_path}"
            result = execute_command(ip, check_cmd, capture_output=True)
            print(f"   ✅ Archive créée : {result.stdout.strip()}")

            return archive_path

    except Exception as e:
        print(f"   ❌ Échec de l'export : {e}")
        return None
