# Status

## Objective

Déléguer les tâches d'indexation intensives de Plex (scan, génération de métadonnées, analyse Sonic) vers une instance cloud Scaleway éphémère, puis rapatrier la base de données et les métadonnées vers un serveur local (ZimaBoard).

## Current focus

6 fixes infra (audit sécurité/optimisation) appliqués. Prêt pour un run cloud complet avec `--force-deep-scan`.

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
| TV Shows | show | 847 media_parts | **~717 chemins obsolètes** (dossiers renommés sur S3) |
| Movies | movie | 315 films | OK (+36 nouveaux détectés) |
| A voir | movie | 32 films | OK |
| Photos | photo | 28,338 photos | OK |
| Kids - Movies | movie | 5 films | OK |
| Kids - TV Shows | show | 200 épisodes | À vérifier (même risque que TV Shows) |
| Adult | movie | 57 films | OK |

**Total:** ~490k items | **Archive:** 5.37 GB (compressé) / 15 GB (DB décompressée)

**Séries avec chemins S3 restructurés (TV Shows):**
- Kaamelott: DB=`Kaamelott Integrale (Livres I a VI + Bonus)/Livre I/` → S3=`Kaamelott/Season 1/` (416 fichiers)
- Hart to Hart: DB=`hart-to-hart-s01/` → S3=`Season 1/` (218 fichiers)
- Columbo: DB=flat `Columbo.S00E01...` → S3=`Season 00/Season 01/...` (69 fichiers)
- Daryl Dixon, Aventures/Coeur Caraibes: absents ou renommés (14 fichiers)

## Log

<!-- Entries added by /retro, newest first -->

### 2026-02-26 - 6 fixes infra (audit sécurité/optimisation)

- Done:
  - **Fix 1 - Self-destruct cloud-init**: `setup_instance.sh` - auto-shutdown après 96h via `nohup` (survit au crash du script d'orchestration)
  - **Fix 3 - Masquer token Plex**: `plex_setup.py` - token affiché par longueur uniquement, PLEX_CLAIM masqué dans le print docker run
  - **Fix 4 - Vérification archive**: `executor.py` - `verify_archive()` (tar -tzf) appelée après chaque download dans les 4 scripts
  - **Fix 6 - SSH keepalive**: `executor.py` + `scaleway.py` - `ServerAliveInterval=30` + `ServerAliveCountMax=5` sur tous les SSH/SCP (4 emplacements)
  - **Fix 7 - Docker CPU limits**: `config.py` - power=8.0 CPU/24g RAM (GP1-S), superpower=16.0 CPU/48g RAM (GP1-M)
  - **Fix 9 - .gitignore**: `.current_instance_ip` ajouté
- Next:
  - Lancer `automate_delta_sync.py --force-deep-scan` sur Scaleway
  - Valider les fixes en conditions réelles (SSH keepalive, auto-shutdown)

### 2026-02-26 - Diagnostic VFS warming + --force-deep-scan

- Done:
  - **Analyse 3 test runs** (Movies, TV Shows, Adult): les 3 terminent proprement, VFS warming et idle detection fonctionnels
  - **Diagnostic VFS warming TV Shows** (197/847 OK, 650 FAIL):
    - Hypothèse expert infra (apostrophes shell) invalidée : Kaamelott utilise des underscores, seulement ~4 fichiers avec apostrophes
    - **Cause réelle identifiée** : 717/847 fichiers ont des chemins obsolètes dans la DB (dossiers restructurés sur S3 depuis déc 2025)
    - Test `[ -e "$file" ]` sur le mount S3 : 130 OK, 717 MISSING
  - **Scan incrémental (force=0) ne détecte pas les réorganisations** : Plex ne parcourt que les chemins connus en DB, pas les nouveaux dossiers
  - **`--force-deep-scan`** (renommé depuis `--force-scan`): propagé à toutes les sections (pas seulement Music) dans `test_delta_sync.py` et `automate_delta_sync.py`
  - Help mis à jour : "Forcer un rescan complet de toutes les sections (utile si des dossiers ont été renommés/déplacés sur S3)"
- Next:
  - Vérifier si Music a le même problème de chemins obsolètes
  - Lancer un test local avec `--force-deep-scan` pour valider que Plex redécouvre les fichiers déplacés
  - Lancer `automate_delta_sync.py --force-deep-scan` sur Scaleway

### 2026-02-24 - VFS cache warming avant analyse

- Done:
  - **`warm_vfs_cache(ip, config_path, section_id, mount_point)`** ajouté dans `common/plex_scan.py`
  - Intégration dans `test_delta_sync.py` et `automate_delta_sync.py`
- Next:
  - Tester `test_delta_sync.py --section 'TV Shows'` pour valider le warm-up

### 2026-02-23 - Repair DB + unification output stats

- Done:
  - **`repair_plex_db(ip, db_path)`** ajouté dans `common/delta_sync.py`
  - Unification output stats (suppression récapitulatif intermédiaire, deltas +N en fin de script)
- Next:
  - Lancer `test_delta_sync.py` complet avec la DB corrompue réelle

### 2026-02-23 - Timeout adaptatif wait_section_idle()

- Done:
  - **Helper `get_container_cpu()`**, **refactoring `wait_section_idle()`** avec CPU monitoring + paramètres adaptatifs par phase
- Next:
  - Tester en conditions réelles

### 2026-02-13 - Analyse de 3 échecs test Movies

- Done:
  - Root cause Runs 1 & 3: fichiers invisibles dans rclone FUSE (dossiers OK, fichiers non)
  - Root cause Run 2: corruption DB latente exposée par UPDATE SQL massif

### 2026-02-11 - Retrait MountMonitor des scripts locaux + simplification cloud

- Done:
  - Retrait MountMonitor des scripts locaux, simplification scripts cloud

### 2026-02-09 - Fix montage dégradé + MountMonitor annulable + Docker pre-pull

- Done:
  - Healthcheck pré-scan, remount annulable via stop_event, Docker pre-pull local

### 2026-02-05 - Timeouts 3 jours + décision Photos→Immich

- Done:
  - Timeouts cloud 3 jours, MountMonitor refactoré

### 2026-02-05 - Feature Path Remapping + audit faux positifs

- Done:
  - Fix montage FUSE stale, feature Path Remapping (`path_mappings.json`)
