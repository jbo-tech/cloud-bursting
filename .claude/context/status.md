# Status

## Objective

Déléguer les tâches d'indexation intensives de Plex (scan, génération de métadonnées, analyse Sonic) vers une instance cloud Scaleway éphémère, puis rapatrier la base de données et les métadonnées vers un serveur local (ZimaBoard).

## Current focus

VFS cache warming implémenté pour éviter les ENOENT massifs lors de l'analyse Plex. Prêt pour test intégré (`test_delta_sync.py --section 'TV Shows'`). Diagnostic Movies (fichiers invisibles rclone FUSE) toujours en attente de vérification S3.

**Scripts principaux:**
- `automate_scan.py` - Cloud scan from scratch (MountMonitor, stop avant Export)
- `automate_delta_sync.py` - Cloud delta sync (MountMonitor, stop avant Export)
- `test_scan_local.py` / `test_delta_sync.py` - Tests locaux (sans MountMonitor, résilience rclone seule)

**Décision stratégique:** Photos → Immich (Plex inadapté pour les photos)

## Reference Database

État de la DB de référence pour delta sync (`plex_delta_sync_20260221_214329.tar.gz`):

| Bibliothèque | Type | Items | État |
|--------------|------|-------|------|
| Music | artist | 456,534 pistes | Sonic 17.8% (81,035) |
| TV Shows | show | 738 épisodes | OK |
| Movies | movie | 315 films | OK |
| A voir | movie | 32 films | OK |
| Photos | photo | 28,338 photos | OK |
| Kids - Movies | movie | 5 films | OK |
| Kids - TV Shows | show | 200 épisodes | OK |
| Adult | movie | 57 films | OK |

**Total:** ~490k items | **Archive:** 5.50 GB (compressé) / 15 GB (DB décompressée)

## Log

<!-- Entries added by /retro, newest first -->

### 2026-02-24 - VFS cache warming avant analyse

- Done:
  - **`warm_vfs_cache(ip, config_path, section_id, mount_point)`** ajouté dans `common/plex_scan.py`:
    - Requête DB pour lister les fichiers de la section (media_parts → media_items → metadata_items)
    - Conversion chemins DB `/Media/...` → chemins hôte `mount_point/...` via sed
    - Lecture 64 Ko par fichier en parallèle (xargs -P4, -d'\n' pour noms avec espaces)
    - Timeout 600s, stats de retour (total/warmed/errors)
  - **Intégration dans `test_delta_sync.py`**: appel entre wait_section_idle(phase='scan') et trigger_section_analyze() dans la boucle other_sections
  - **Intégration dans `automate_delta_sync.py`**: même position, chemins cloud (/opt/plex_data/config, /opt/media)
  - Compilation vérifiée (py_compile) sur les 3 fichiers
- Next:
  - Tester `test_delta_sync.py --section 'TV Shows'` pour valider le warm-up
  - Comparer taux ENOENT avec/sans cache warming (objectif <10% vs 80% avant)
  - Vérifier les fichiers S3 Movies (diagnostic rclone FUSE toujours ouvert)

### 2026-02-23 - Repair DB + unification output stats

- Done:
  - **`repair_plex_db(ip, db_path)`** ajouté dans `common/delta_sync.py`:
    - Détecte la corruption via `SELECT COUNT(*) FROM media_parts`
    - Répare via `sqlite3 .recover | sqlite3 repaired.db` (pas `.dump` qui échoue sur corruption B-tree)
    - Vérifie la DB réparée, affiche stats tables avant/après
    - Return False (saine), True (réparée), RuntimeError (échec)
  - **Intégration dans `remap_library_paths()`**: appelé après backup, avant boucle de remapping
  - **Validation 3 scénarios**: DB saine (no-op), index corrompus (508946 entrées récupérées), destruction totale (RuntimeError)
  - **Unification output stats** (`test_delta_sync.py` + `automate_delta_sync.py`):
    - Suppression du récapitulatif intermédiaire (7.3 / 9.3) qui dupliquait la lecture DB
    - Ajout des deltas (+N) pour Films, Épisodes, Photos dans le résumé final (8.4 / 10.4)
    - Un seul bloc cohérent en fin de script avec tous les compteurs et deltas
- Next:
  - Lancer `test_delta_sync.py` complet avec la DB corrompue réelle du ZimaBoard
  - Vérifier les fichiers S3 Movies (diagnostic rclone FUSE toujours ouvert)
  - Lancer `automate_delta_sync.py` sur Scaleway

### 2026-02-23 - Timeout adaptatif wait_section_idle()

- Done:
  - **Helper `get_container_cpu()`**: extrait le pattern `docker stats --no-stream` dupliqué 3 fois (wait_plex_stabilized, wait_sonic_complete, et le nouveau wait_section_idle)
  - **Refactoring `wait_section_idle()`** dans `common/plex_scan.py`:
    - Monitoring CPU ajouté: `is_truly_idle = activity['is_idle'] and cpu_percent < 20%`
    - Paramètres adaptatifs: phase analyze = 120s × 5 = 10min silence (phase scan inchangée: 30s × 3)
    - Timeouts de sécurité par section: movie 4h, show 2h, photo 8h, artist 4h (défaut 2h)
    - Grace period 60s au démarrage (évite faux idle avant que le Scanner lance)
    - CPU affiché dans toutes les lignes de status
    - Message timeout changé en `🚨 Timeout de sécurité` (anomalie, pas terminaison normale)
  - Rétrocompatibilité totale: callers avec params explicites respectés, aucun script modifié
  - Validation: import OK (4 scripts), ruff clean (0 nouvelle erreur)
- Next:
  - Tester en conditions réelles (cloud ou local)
  - Vérifier les fichiers S3 Movies (diagnostic rclone FUSE toujours ouvert)
  - Lancer `automate_delta_sync.py` sur Scaleway

### 2026-02-13 - Analyse de 3 échecs test Movies

- Done:
  - **Analyse détaillée de 3 logs de test** (`--section Movies`):
    - Run 1 (`20260213_104705`): Scanner supprime 221/224 films. 0 ajouté. DB 315→94.
    - Run 2 (`20260213_111038`): DB corrompue pendant remapping (`database disk image is malformed`). Plex crashe en boucle.
    - Run 3 (`20260213_140321`): Identique au Run 1. Scanner supprime 221/224 films.
  - **Root cause Runs 1 & 3**: fichiers invisibles dans rclone FUSE (dossiers OK, fichiers non)
  - **Root cause Run 2**: corruption DB latente exposée par UPDATE SQL massif
- Blocked:
  - En attente de vérification par l'utilisateur: `rclone ls mega-s4:media-center/Movies/Dune\ (2021)/ --config ./rclone.conf`
- Next:
  - Vérifier si les fichiers existent dans S3 avec les noms attendus par la DB

### 2026-02-11 - Retrait MountMonitor des scripts locaux + simplification cloud

- Done:
  - **Analyse de 2 tests échoués**:
    - Test 1 (`20260210_192052`, `--section Movies`): MountMonitor 6/6 faux positifs, remontages inutiles pendant l'export, +0 delta alors que des fichiers ont été ajoutés (remontage a vidé le dir-cache rclone)
    - Test 2 (`20260211_012555`, `--section 'TV Shows'`): bloqué en Phase 7, machine gelée (deadlock FUSE probable lors du remontage pendant I/O active)
  - **Diagnostic root cause**: timeout 30s du healthcheck trop agressif pour connexion résidentielle → faux positifs systématiques → remontages inutiles → dir-cache purgé → scan échoue silencieusement
  - **Retrait MountMonitor des scripts locaux** (`test_delta_sync.py`, `test_scan_local.py`)
  - **Simplification scripts cloud**: stop() avant Export, filet sécurité dans finally
- Next:
  - Valider test local `test_delta_sync.py --section Movies`

### 2026-02-09 - Fix montage dégradé + MountMonitor annulable + Docker pre-pull

- Done:
  - Healthcheck pré-scan `ensure_mount_healthy()`, remount annulable via `stop_event`, Docker pre-pull local
  - 3 anti-patterns + 2 decisions documentés
- Next:
  - Relancer test local Movies

### 2026-02-05 - Timeouts 3 jours + décision Photos→Immich

- Done:
  - Timeouts cloud 3 jours pour run Sonic complet (375k pistes restantes)
  - MountMonitor refactoré: I/O hors lock, threading.Event, stop() fiable
- Next:
  - Lancer `automate_delta_sync.py` sur Scaleway (run 3 jours)

### 2026-02-05 - Feature Path Remapping + audit faux positifs

- Done:
  - Fix montage FUSE stale, fix vérification intégrité DB (tables FTS)
  - Feature Path Remapping: `path_mappings.json`, `load_path_mappings()`, `remap_library_paths()`
- Next:
  - Relancer test local TV Shows pour valider le path remapping
