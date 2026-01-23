# Status

## Objective

Déléguer les tâches d'indexation intensives de Plex (scan, génération de métadonnées, analyse Sonic) vers une instance cloud Scaleway éphémère, puis rapatrier la base de données et les métadonnées vers un serveur local (ZimaBoard).

## Current focus

Fix du workflow Sonic. L'analyse Sonic ne progressait pas (compteur bloqué à 81035 pendant 2h malgré CPU à 407%). Cause identifiée et corrigée : `--force` déclenchait un refresh metadata complet avant l'analyse audio.

**Scripts:**
- `automate_scan.py` - Cloud scan from scratch ✅ refactorisé
- `automate_delta_sync.py` - Cloud delta sync (DB existante) ✅ refactorisé
- `test_scan_local.py` / `test_delta_sync.py` - Tests locaux ✅ refactorisés

## Reference Database

État de la DB de référence pour delta sync (`plex_db_only_20251220_224449.tar.gz`):

| Bibliothèque | Type | Items | État |
|--------------|------|-------|------|
| Music | artist | 456,534 pistes | Sonic 17.8% (81,035) |
| TV Shows | show | 938 épisodes | OK |
| Movies | movie | 315 films | OK |
| A voir | movie | 32 films | OK |
| Photos | photo | 28,338 photos | OK |
| Kids - Movies | movie | 5 films | OK |
| Kids - TV Shows | show | 200 épisodes | OK |
| Adult | movie | 57 films | OK |

**Total:** ~490k items | **Archive:** 5.37 GB (compressé) / 15 GB (DB décompressée)

## Log

<!-- Entries added by /retro, newest first -->

### 2026-01-23 - Fix workflow Sonic + refactoring majeur

- Done:
  - Diagnostic du problème Sonic : `--force` déclenchait un refresh metadata complet (2h+) avant l'analyse audio
  - Analyse logs : CPU 407% = téléchargement métadonnées (fanart.tv, lastfm), pas Chromaprint
  - Vérification compteurs SQL : méthode `ms:musicAnalysisVersion` correcte (81,035 = bon comptage)
  - Fix `trigger_sonic_analysis()` : retiré `--force`
  - Nouveau profil monitoring `metadata_refresh` (timeout 4h, CPU threshold 20%)
  - Nouvelle fonction `wait_plex_stabilized()` (attente idle avant Sonic)
  - Nouveau workflow en 3 sous-phases : 6.Xa Metadata Refresh → 6.Xb Stabilisation → 6.Xc Sonic
  - Ajout argument `--force-refresh` dans tous les scripts
  - Refactoring `automate_scan.py` : supprimé fonctions inexistantes, aligné sur workflow commun
  - Harmonisation des 4 scripts principaux avec même workflow
- Next:
  - Relancer test avec `--force-refresh` pour valider le nouveau workflow
  - Vérifier que Sonic progresse vraiment (lecture fichiers S3)

### 2026-01-21 - Améliorations diagnostic init Plex

- Done:
  - Analyse des logs de test cloud (20260121_000027) et local (20260121_205911)
  - Ajout `print_phase_header()` pour horodatage des phases dans tous les scripts
  - Amélioration `get_plex_token()` avec retry (120s timeout, 10s interval)
  - Amélioration `wait_plex_fully_ready()` avec diagnostic détaillé + capture logs Docker
  - Augmentation timeouts cloud (600s init, 180s token, 120s Plex Pass)
  - Commit et push sur GitHub (06342b3)
- Blocked:
  - Plex init timeout malgré 10 processus actifs et sections trouvées
  - Critère `/identity` ne retourne pas "Plex" - cause inconnue
- Next:
  - Relancer test avec nouveau diagnostic pour voir pourquoi `/identity` échoue
  - Analyser les logs Docker capturés automatiquement

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
