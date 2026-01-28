# Status

## Objective

Déléguer les tâches d'indexation intensives de Plex (scan, génération de métadonnées, analyse Sonic) vers une instance cloud Scaleway éphémère, puis rapatrier la base de données et les métadonnées vers un serveur local (ZimaBoard).

## Current focus

Validation du fix feedback visuel + test local en cours pour valider les corrections rclone.

**Scripts principaux:**
- `automate_scan.py` - Cloud scan from scratch ✅
- `automate_delta_sync.py` - Cloud delta sync (DB existante) ✅
- `test_scan_local.py` / `test_delta_sync.py` - Tests locaux ✅

**Scripts de déploiement:**
- `update_to_local_plex.sh` - Import métadonnées sur serveur local ✅
- `update_to_distant_plex.sh` - Déploiement distant via SSH ✅
- `export_plex_db.sh` - Export DB Plex pour delta sync ✅

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

### 2026-01-28 - Renommage argument --profile → --monitoring

- Done:
  - Clarification de la différence entre `--instance` (ressources: rclone, Docker) et `--profile` (monitoring: timeouts)
  - Renommage `--profile` → `--monitoring` dans 3 fichiers pour plus de clarté
  - Fichiers modifiés: `test_scan_local.py`, `test_delta_sync.py`, `automate_delta_sync.py`
  - Conservation des valeurs `local/cloud` (plus explicites que `quick/patient`)
- Next:
  - Poursuivre validation test local avec corrections rclone
  - Tester workflow complet en cloud

### 2026-01-28 - Test local en cours + investigation blocage

- Done:
  - Modification `ensure_mount_healthy()` : ajout feedback visuel "🔍 Vérification du montage S3..."
  - Test local lancé pour valider les corrections rclone
- Observed:
  - Blocage 30+ minutes après "6.2 Scan de la section Musique..." (ancien code sans feedback)
  - `ls /mnt/s3/Music` retournait "No such file or directory" pendant le blocage
  - Test a repris après - probablement remontage automatique réussi
- Next:
  - Attendre fin du test pour analyse complète des logs rclone
  - Vérifier si le remontage automatique a fonctionné ou si autre cause

### 2026-01-27 - Fix feedback visuel healthchecks

- Done:
  - Ajout message de progression dans `ensure_mount_healthy()` avant `verify_rclone_mount_healthy()`
  - Affichage "🔍 Vérification du montage S3..." avec spinner pendant la vérification
  - Affichage du temps de réponse en cas de succès: "✅ (0.5s)"
  - Affichage "❌" en cas d'échec avant les messages de remontage
- Next:
  - Relancer test local pour valider l'affichage du feedback
  - Tester workflow complet en cloud

### 2026-01-24 - Fix déconnexions rclone

- Done:
  - Analyse logs test local (20260123_193715): 1248 erreurs socket, x13 vs test précédent
  - Diagnostic: montage rclone se déconnecte après ~30min (dernier log 20:13, erreurs 02:41)
  - Les erreurs "Permission denied" sont un faux positif (effet secondaire du socket mort)
  - Fix profils rclone (`config.py`): timeout 30m, contimeout 300s, retries 10, retries_sleep 30s, cache 5G
  - Fix commande mount (`plex_setup.py`): ajout --retries, --retries-sleep, --stats 5m
  - Nouvelles fonctions healthcheck: `verify_rclone_mount_healthy()`, `remount_s3_if_needed()`
- Next:
  - Relancer test local pour valider les corrections rclone
  - Si OK, tester workflow complet en cloud

### 2026-01-24 - Refonte scripts de déploiement

- Done:
  - Renommage cohérent: `update_to_local_plex.sh` / `update_to_distant_plex.sh` / `export_plex_db.sh`
  - Suppression données personnelles hardcodées (user, hostname, chemins)
  - Variables d'environnement obligatoires pour déploiement distant (`PLEX_REMOTE_HOST`, `PLEX_REMOTE_PATH`)
  - Arguments CLI pour chemins Plex (avec défaut standard Linux)
  - Backup archive automatique avant import
  - Mode non-interactif (`-y`) pour exécution scriptée
  - Détection dynamique `$(whoami)@$(hostname)` pour instructions SCP
  - Commit et push GitHub (8a72436)
- Next:
  - Tester workflow complet: export → delta sync cloud → deploy distant
  - Valider workflow Sonic avec nouveau profil 3 phases

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
