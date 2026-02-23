# Status

## Objective

Déléguer les tâches d'indexation intensives de Plex (scan, génération de métadonnées, analyse Sonic) vers une instance cloud Scaleway éphémère, puis rapatrier la base de données et les métadonnées vers un serveur local (ZimaBoard).

## Current focus

Diagnostic de 3 échecs consécutifs du test local `test_delta_sync.py --section Movies`. Le scanner Plex voit les dossiers mais pas les fichiers à l'intérieur → supprime 221/224 films. En attente de vérification S3 par l'utilisateur.

**Scripts principaux:**
- `automate_scan.py` - Cloud scan from scratch (MountMonitor, stop avant Export)
- `automate_delta_sync.py` - Cloud delta sync (MountMonitor, stop avant Export)
- `test_scan_local.py` / `test_delta_sync.py` - Tests locaux (sans MountMonitor, résilience rclone seule)

**Décision stratégique:** Photos → Immich (Plex inadapté pour les photos)

## Reference Database

État de la DB de référence pour delta sync (`plex_db_only_20251220_224449.tar.gz`):

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

**Total:** ~490k items | **Archive:** 5.37 GB (compressé) / 15 GB (DB décompressée)

## Log

<!-- Entries added by /retro, newest first -->

### 2026-02-13 - Analyse de 3 échecs test Movies

- Done:
  - **Analyse détaillée de 3 logs de test** (`--section Movies`):
    - Run 1 (`20260213_104705`): Scanner supprime 221/224 films. 0 ajouté. DB 315→94.
    - Run 2 (`20260213_111038`): DB corrompue pendant remapping (`database disk image is malformed`). Plex crashe en boucle.
    - Run 3 (`20260213_140321`): Identique au Run 1. Scanner supprime 221/224 films.
  - **Root cause Run 2**: DB archive possiblement corrompue sur les tables liées à TVShows/Kids TV. Le UPDATE SQL du remapping aggrave la corruption. Plex refuse de démarrer.
  - **Root cause Runs 1 & 3**: Les logs Plex montrent explicitement:
    ```
    File '/Media/Movies/Dune (2021)/Dune (2021) Bluray-720p.mp4' didn't exist, can't skip.
    File '/Media/Movies/GoodFellas (1990)/GoodFellas (1990) Bluray-2160p.mkv' didn't exist, can't skip.
    ```
    - Dossiers visibles (rclone dir-cache OK) mais **fichiers invisibles** à l'intérieur
    - rclone stats: `Listed 586490` mais `Transferred: 0 B`
    - 224 items DB section 3, scanner trouve 0 fichier, supprime 221
  - **Hypothèses restantes** (non encore vérifiées):
    - Les fichiers dans S3 ont été renommés/réorganisés depuis décembre 2025
    - Les fichiers dans S3 existent dans les dossiers mais sous d'autres noms que ceux en DB
    - Le montage rclone FUSE ne liste pas correctement le contenu des sous-répertoires
- Blocked:
  - En attente de vérification par l'utilisateur: `rclone ls mega-s4:media-center/Movies/Dune\ (2021)/ --config ./rclone.conf`
- Next:
  - Vérifier si les fichiers existent dans S3 avec les noms attendus par la DB
  - Si noms différents: la DB de décembre est obsolète, besoin d'un scan from scratch
  - Si noms identiques: diagnostiquer pourquoi rclone FUSE ne les expose pas (bug VFS ?)

### 2026-02-11 - Retrait MountMonitor des scripts locaux + simplification cloud

- Done:
  - **Analyse de 2 tests échoués**:
    - Test 1 (`20260210_192052`, `--section Movies`): MountMonitor 6/6 faux positifs, remontages inutiles pendant l'export, +0 delta alors que des fichiers ont été ajoutés (remontage a vidé le dir-cache rclone)
    - Test 2 (`20260211_012555`, `--section 'TV Shows'`): bloqué en Phase 7, machine gelée (deadlock FUSE probable lors du remontage pendant I/O active)
  - **Diagnostic root cause**: timeout 30s du healthcheck trop agressif pour connexion résidentielle → faux positifs systématiques → remontages inutiles → dir-cache purgé → scan échoue silencieusement
  - **Retrait MountMonitor des scripts locaux** (`test_delta_sync.py`, `test_scan_local.py`):
    - Retiré imports MountHealthMonitor et ensure_mount_healthy
    - Retiré création/start/stop du monitor
    - Retiré healthcheck pré-scan (ensure_mount_healthy avant chaque section)
    - Retiré health_check_fn dans wait_sonic_complete
    - Corrigé indentation (bloc sur-indenté après retrait du if/else)
  - **Simplification scripts cloud** (`automate_delta_sync.py`, `automate_scan.py`):
    - Retiré ensure_mount_healthy (MountMonitor continu suffit en cloud)
    - Déplacé mount_monitor.stop() avant la phase Export (plus nécessaire pour lecture disque local)
    - Gardé filet de sécurité dans finally (arrêt propre + stats en cas d'exception)
  - **Validation infra-expert**: stop() dans finally est correct (arrête le thread, affiche stats, empêche remontages — ne déclenche jamais de remontage)
- Next:
  - Valider test local `test_delta_sync.py --section Movies` (en cours)
  - Vérifier que le delta de scan détecte les nouveaux fichiers
  - Lancer `automate_delta_sync.py` sur Scaleway

### 2026-02-09 - Fix montage dégradé + MountMonitor annulable + Docker pre-pull

- Done:
  - **Analyse logs test** (`20260209_221640`): montage rclone dégradé → Plex supprime 221/224 films
    - Dir-cache 72h = répertoires listables mais fichiers I/O bloqué
    - Scanner Plex interprète "fichiers inaccessibles" comme "fichiers supprimés"
  - **Solution A - Healthcheck pré-scan**: `ensure_mount_healthy()` avant chaque `trigger_section_scan()`
    - Si montage cassé: scan annulé, `music_section_id = None`, `stats_after_scan = stats_before`
    - Implémenté dans `test_delta_sync.py` et `automate_delta_sync.py`
  - **Solution B - Remount annulable**: `remount_s3_if_needed()` accepte `stop_event`
    - `_interrupted()` + `_sleep()` helpers, 3 checkpoints dans la boucle de retry
    - `mount_monitor.py`: passe `self._stop_event`, join timeout 35s → 60s
  - **Solution C - Docker pre-pull**: `docker pull` en Phase 1 dans `test_delta_sync.py` et `test_scan_local.py`
    - Cloud: déjà dans `setup_instance.sh:60`, pas de changement nécessaire
  - **Documentation**: 3 anti-patterns + 2 decisions ajoutés
  - **Décision**: risque résiduel (montage tombe PENDANT scan) accepté, pas de watchdog (sur-ingénierie)
- Bugs corrigés pendant implémentation:
  - Control flow cassé en Phase 6 (elif après mount check → restructuré avec if/else)
  - Variable `rclone_profile` vs `profile` dans automate_delta_sync.py
  - f-strings sans placeholders (ruff)
- Next:
  - Relancer test local `test_delta_sync.py --section Movies` pour valider les 3 fixes
  - Lancer `automate_delta_sync.py` sur Scaleway (run 3 jours)
  - Valider Sonic analysis sur 375k pistes
  - Migrer Photos vers Immich séparément

### 2026-02-05 - Timeouts 3 jours + décision Photos→Immich

- Done:
  - **Analyse architecture**: streaming (séquentiel, 1 fichier) OK sur résidentiel, analyse (parallèle, 1000s requêtes) nécessite cloud
  - **Décision Photos → Immich**: Plex inadapté pour photos, saturation NAT résidentielle confirmée
  - **Timeouts cloud 3 jours** pour run Sonic complet (375k pistes restantes):
    - `cloud_intensive.absolute_timeout`: 86400 (24h) → 259200 (72h)
    - `wait_plex_fully_ready`: 600s → 900s
    - `wait_section_idle` musique: ajout explicit `timeout=14400` (4h)
    - `wait_section_idle` autres sections (scan + analyze): 3600 → 14400 (4h)
  - **MountMonitor refactoré**: I/O hors lock, threading.Event, stop() fiable
- Next:
  - Lancer `automate_delta_sync.py` sur Scaleway (run 3 jours)
  - Valider Sonic analysis sur 375k pistes
  - Migrer Photos vers Immich séparément

### 2026-02-05 - Feature Path Remapping + audit faux positifs

- Done:
  - Fix montage FUSE stale: résolu via `fusermount -u`
  - Fix vérification intégrité DB: remplacé `PRAGMA integrity_check` par requête simple (tables FTS incompatibles)
  - **Feature Path Remapping:**
    - `path_mappings.json` - fichier de config des mappings
    - `load_path_mappings()` - charge et valide le fichier JSON
    - `remap_library_paths()` - remappe `section_locations` + `media_parts` avec backup
    - Argument `--path-mappings FILE` dans test_delta_sync.py et automate_delta_sync.py
  - Mise en conformité `automate_delta_sync.py` avec la feature remapping
- Next:
  - Relancer test local TV Shows pour valider le path remapping
