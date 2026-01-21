# Status

## Objective

Déléguer les tâches d'indexation intensives de Plex (scan, génération de métadonnées, analyse Sonic) vers une instance cloud Scaleway éphémère, puis rapatrier la base de données et les métadonnées vers un serveur local (ZimaBoard).

## Current focus

Projet stable en v2.7. Architecture modulaire dans `common/` (7 modules), 4 scripts principaux harmonisés.

**Scripts:**
- `automate_scan.py` - Cloud scan from scratch
- `automate_delta_sync.py` - Cloud delta sync (DB existante)
- `test_scan_local.py` / `test_delta_sync.py` - Tests locaux

## Reference Database

État de la DB de référence pour delta sync (`plex_db_only_20251220_224449.tar.gz`):

| Bibliothèque | Type | Items | État |
|--------------|------|-------|------|
| Music | artist | 456,473 pistes | Sonic 17% |
| TV Shows | show | 738 épisodes | Thumbs manquants |
| Movies | movie | 221 films | Thumbs manquants |
| A voir | movie | 32 films | Thumbs manquants |
| Photos | photo | 1,565 photos | OK |
| Kids - Movies | movie | 5 films | Thumbs manquants |
| Kids - TV Shows | show | 200 épisodes | Thumbs manquants |
| Adult | movie | 57 films | Thumbs manquants |

**Total:** 534,875 items | **DB actuelles:** 2.3 GB | **Archive:** 5.4 GB (compressé) / 15 GB (avec backups) | **Metadata bundles:** 0%

## Log

<!-- Entries added by /retro, newest first -->

### 2026-01-20 - Fix sqlite3 manquant sur instance cloud

**Problème:** Test Scaleway (6€) avec résultats décevants - tous les compteurs à 0, timeouts systématiques de 60min.

**Cause:** `sqlite3` non installé dans `setup_instance.sh`. Les requêtes DB échouaient silencieusement (`check=False`), désactivant tout le monitoring v2.7.

**Fix:** Ajout de `sqlite3` aux paquets installés dans cloud-init.

### 2026-01-20 - Initialisation contexte Claude

Extraction du contexte depuis CLAUDE.md vers `.claude/context/`:
- `decisions.md` : 12 décisions techniques (Scaleway, rclone, UID 1000, etc.)
- `anti-patterns.md` : 14 anti-patterns documentés + références Plex
- `status.md` : État actuel du projet

Versions majeures (détails dans git history):
- v2.7 (2026-01-16): Monitoring différencié scan/analyse
- v2.6 (2026-01-15): Harmonisation scripts, fix imports
- v2.5 (2026-01-11): Détection "déjà analysé", --force-refresh
- v2.4 (2026-01-10): TeeLogger, collecte logs dynamique
- v2.3 (2026-01-08): Timestamps, timeouts adaptatifs photos
- v2.2 (2026-01-07): Séquentiel Strict, isolation tâches fond
- v2.1 (2025-12-29): Modularisation common/
- v2.0 (2025-12-28): Migration Bash → Python
