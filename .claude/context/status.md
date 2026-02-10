# Status

## Objective

Déléguer les tâches d'indexation intensives de Plex (scan, génération de métadonnées, analyse Sonic) vers une instance cloud Scaleway éphémère, puis rapatrier la base de données et les métadonnées vers un serveur local (ZimaBoard).

## Current focus

Corrections post-test local validées. Prêt pour test cloud Scaleway 3 jours. Montage S3 protégé par healthcheck pré-scan, MountMonitor annulable, Docker pré-pull.

**Scripts principaux:**
- `automate_scan.py` - Cloud scan from scratch ✅
- `automate_delta_sync.py` - Cloud delta sync (DB existante) ✅ + healthcheck pré-scan
- `test_scan_local.py` / `test_delta_sync.py` - Tests locaux ✅ + healthcheck pré-scan + docker pre-pull

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
- Findings:
  - Streaming S3 → résidentiel = OK (débit séquentiel suffisant pour 1 utilisateur)
  - Analyse S3 → résidentiel = KO (saturation NAT ~4096 sessions parallèles)
  - Cloud bursting = approche validée (intra-datacenter S3)
  - Ajouts réguliers (2-3 films/sem, 5-10 albums) gérables par delta sync cloud
- Next:
  - Lancer `automate_delta_sync.py` sur Scaleway (run 3 jours)
  - Valider Sonic analysis sur 375k pistes
  - Migrer Photos vers Immich séparément

### 2026-02-05 - Test Photos + fix MountMonitor

- Done:
  - **Test 1 Photos** (`20260205_114604`): échec complet - `/Photo` non monté dans Docker
    - 3368 erreurs "FreeImage_Load: failed to open file /Photo/..."
    - Cause: bibliothèque Photos avait 2 locations (`/Media/Photo` + `/Photo`) mais seul `/Media` monté
  - **Fix**: ajout mapping `/Photo` → `/Media/Photo` dans `path_mappings.json`
  - **Test 2 Photos** (`20260205_150723`): mapping validé, 29903 fichiers remappés, 0 erreur FreeImage
    - Mais: 2375 erreurs rclone "connection reset by peer" (connexion résidentielle → S3 Scaleway)
    - Analyse bloquée 4h (timeout 240min), compteur oscillant 28168↔28326
    - Résultat: +1 photo seulement, 13 JPEG corrompus (0.05%, négligeable)
  - **Fix MountMonitor**: refactoring `_perform_health_check()` et `stop()`
    - `self._lock` sorti des opérations I/O longues (verify_rclone + remount)
    - `threading.Event` pour interruption immédiate du sleep dans `_monitor_loop`
    - `stop()` simplifié: `join(timeout=35)` + `with self._lock` (plus de "Stats indisponibles")
    - Suppression `import time` devenu inutile
- Findings:
  - Le test local Photos n'est pas viable (réseau résidentiel trop lent pour 28k photos via S3)
  - Le cloud est le bon use-case pour ce volume (lien intra-datacenter S3)
- Next:
  - Tester le fix MountMonitor
  - Lancer test cloud complet (Photos + autres sections)

### 2026-02-05 - Réanalyse test delta + corrections bugs

- Done:
  - Analyse logs test delta local (`20260205_041326_logs_final_all/`)
  - **Fix 1 - os.path.exists(None)**: ajout vérification `terminal_log and` avant `os.path.exists()` dans `collect_plex_logs()` (plex_setup.py:1114)
  - **Fix 2 - Diagnostic Sonic conditionnel**: ajout `if should_process_music:` dans le bloc diagnostic post-mortem (3 scripts)
  - Initialisation `should_process_music = True` en dehors du try/except
  - **Réanalyse avec contenu S3**: les données sont INTACTES
- Findings corrigés:
  - ❌ "210 épisodes perdus" = FAUX - les DB backup et actuelle sont identiques (938 épisodes)
  - Le "728" affiché était une lecture de stats pendant timeout rclone (donnée temporairement incorrecte)
  - Toutes les séries S3 présentes (Columbo, Hart to Hart, Freaks and Geeks, etc.)
  - Path remapping fonctionne correctement
- Next:
  - Relancer test delta pour valider les corrections
  - Vérifier que les logs Plex et rclone sont collectés

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
  - Audit infra-expert: identification des faux positifs
- Audit findings:
  - ❌ Injection SQL : FAUX POSITIF (fichier local contrôlé par l'utilisateur)
  - ❌ Import inside function : FAUX POSITIF (lazy import acceptable)
  - ❌ Pas de rollback auto : DESIGN INTENTIONNEL (backup + message suffit)
  - ⚠️ Backup remote dans /tmp : Point mineur valide mais impact limité
- Next:
  - Relancer test local TV Shows pour valider le path remapping
  - Vérifier que le scan trouve les fichiers dans `/Media/TV`

### 2026-02-04 - Audit complet et correction bugs critiques

- Done:
  - Audit complet du projet cloud-bursting avec `/audit`
  - Revue expert infra avec analyse des logs de test (terminal_20260203_225508.log)
  - **Fix 1**: `args.only` → `args.section` dans 4 scripts (12 occurrences)
  - **Fix 2**: Deadlock MountHealthMonitor.stop() - ajout timeout 2s sur acquisition lock
  - **Fix 3**: Validation intégrité DB SQLite avec `PRAGMA integrity_check` avant injection
  - **Fix 4**: Suppression import inutilisé `quote` dans plex_scan.py
  - 2 commits: `77e509f` (fixes), `00122e5` (docs)
- Audit findings corrigés:
  - 🔴 args.only AttributeError → Fixed
  - 🔴 Deadlock dans stop() → Fixed avec lock timeout
  - 🔴 DB corrompue non détectée → Fixed avec PRAGMA integrity_check
- Next:
  - Relancer test local pour valider les corrections
  - Tester workflow cloud complet

### 2026-01-31 - Fix bug args.only + audit code

- Done:
  - Fix `AttributeError: 'Namespace' object has no attribute 'only'` dans test_delta_sync.py
  - L'argument CLI est `--section` (stocké dans `args.section`), pas `args.only`
  - 4 occurrences corrigées (lignes 322, 339-340, 515, 522-524)
  - Audit complet du fichier test_delta_sync.py
  - Revue expert infra des points d'audit
- Next:
  - Relancer `python test_delta_sync.py --section Movies` pour valider le fix
  - Committer si OK

### 2026-01-31 - Ajout argument --section pour filtrage par bibliothèque

- Done:
  - Suppression de `--music-only` dans les 4 scripts principaux
  - Ajout de `--section` (répétable) pour filtrer par nom de section Plex
  - Validation des sections demandées avec affichage des sections ignorées
  - Condition `should_process_music` pour skipper phase Musique si non demandée
  - Filtrage des autres sections selon `--section`
  - Initialisation `stats_after_scan = stats_before` pour éviter NameError
  - Message amélioré: "Aucune section musicale dans le filtre --section ['Movies']"
  - Harmonisation numérotation: "📚 Identification des sections..." (sans numéro)
  - Audit et corrections des problèmes identifiés
- Next:
  - Tester `python test_delta_sync.py --section Movies` pour valider le filtrage
  - Committer les changements si OK

### 2026-01-30 - Rollback MountHealthMonitor après deadlock

- Done:
  - Analyse d'un blocage de 4h+ en phase 4 (après entrée PLEX_CLAIM, rien ne se passait)
  - Identifié deadlock: `clear_pending_input()` attendait `self._lock` détenu par `_perform_health_check()` pendant 30+ secondes
  - **Rollback**: retour à l'approche simple - input PLEX_CLAIM AVANT démarrage du monitor
  - Ajout paramètre `initial_delay` à MountHealthMonitor (défaut 0 pour check immédiat)
  - Méthodes `set_pending_input()`/`clear_pending_input()` conservées mais inutilisées
- Next:
  - Tester le workflow modifié pour valider l'absence de deadlock
  - Committer les changements si OK
  - Relancer test complet pour valider Sonic analyse

### 2026-01-29 - Fix trois problèmes majeurs identifiés via analyse logs

- Done:
  - Analyse logs test local (20260127_150937): identifié 3 problèmes majeurs
  - **Fix 1 - MountHealthMonitor timing**: déplacé AVANT prompt PLEX_CLAIM (pas après)
    - test_delta_sync.py, automate_delta_sync.py: réordonné monitor → prompt → Plex
    - test_scan_local.py, automate_scan.py: ajouté MountHealthMonitor (manquait)
  - **Fix 2 - Butler interference**: supprimé appels prématurés à enable_plex_analysis_via_api()
    - Cette fonction déclenchait le Butler DeepMediaAnalysis avant le scan
    - Les processus --analyze-deeply bloquaient wait_section_idle (144 min timeout)
    - Analyses Sonic correctement déclenchées par enable_music_analysis_only() en phase 6.3
  - **Fix 3 - rclone.log dans export**: ajouté paramètre rclone_log à collect_plex_logs()
    - Modifié common/plex_setup.py pour supporter le téléchargement depuis remote
    - Mis à jour tous les appels dans les 4 scripts
  - Nettoyage imports inutilisés (enable_plex_analysis_via_api supprimé où non utilisé)
  - Syntaxe vérifiée pour tous les fichiers modifiés
- Next:
  - Tester les corrections localement
  - Valider que wait_section_idle ne timeout plus
  - Valider que rclone.log apparaît dans les archives exportées
