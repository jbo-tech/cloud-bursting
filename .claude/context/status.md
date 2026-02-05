# Status

## Objective

Déléguer les tâches d'indexation intensives de Plex (scan, génération de métadonnées, analyse Sonic) vers une instance cloud Scaleway éphémère, puis rapatrier la base de données et les métadonnées vers un serveur local (ZimaBoard).

## Current focus

Feature Path Remapping implémentée. Permet de remapper les chemins DB après migration de structure S3 (ex: `/Media/TVShows` → `/Media/TV`).

**Scripts principaux:**
- `automate_scan.py` - Cloud scan from scratch ✅
- `automate_delta_sync.py` - Cloud delta sync (DB existante) ✅ + path remapping
- `test_scan_local.py` / `test_delta_sync.py` - Tests locaux ✅ + path remapping

**Nouveaux fichiers:**
- `path_mappings.json` - Configuration des remappings de chemins

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
- Audit findings:
  - 🔴 Must fix (2 points) → Réévalués comme faux positifs ou risques mitigés
  - 🟡 Consider (3 points) → 1 valide (timeout Phase 7), 2 faux positifs
  - 💡 Suggestions (2 points) → Rejetées comme sur-engineering pour ce projet
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
- Blocked:
  - Changements non committés - en attente de validation par test
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
