# Anti-patterns

Errors encountered and how to avoid them. Added via `/retro`.

<!-- Format:
### [Short title]
**Problem**: What went wrong
**Cause**: Why it happened
**Solution**: How to fix/avoid
**Date**: YYYY-MM-DD
-->

### Missing sqlite3 on cloud instance

**Problem**: All DB-based monitoring returns 0, causing false "already complete" detection and 60min timeouts with no progress tracking.
**Cause**: `sqlite3` not installed in `setup_instance.sh`. Commands fail silently (`check=False`) returning empty strings converted to 0.
**Solution**: Add `sqlite3` to apt-get install in cloud-init: `apt-get install -y ... sqlite3`
**Date**: 2026-01-20

### Missing timestamps in monitoring output

**Problem**: Monitoring logs without timestamps make it impossible to correlate events or measure durations.
**Cause**: Using simple `print(f"Activité: {n}")` without time context.
**Solution**: Always include `[HH:MM:SS]` prefix: `print(f"[{time.strftime('%H:%M:%S')}] 📋 {activity_name} ({progress}%)")`
**Date**: inferred from codebase

### Generic "scanner actif" messages

**Problem**: Same message displayed during both scan and analyze phases, making it hard to track progress.
**Cause**: Not differentiating between scan and analyze phases in `wait_section_idle()`.
**Solution**: Use `phase='scan'` or `phase='analyze'` parameter to customize icons and messages per phase.
**Date**: inferred from codebase

### Fixed timeout for all section types

**Problem**: Photos section terminates prematurely because thumbnail generation is much slower than video/music.
**Cause**: Using 1h timeout for all section types.
**Solution**: Pass `section_type` to `wait_section_idle()`. Photos automatically get 4h timeout.
**Date**: inferred from codebase

### Parallel section processing

**Problem**: Launching all scans then waiting globally causes resource contention and unpredictable behavior.
**Cause**: `for s in sections: trigger_scan(s)` then `wait_all()` pattern.
**Solution**: Sequential processing: `for s in sections: trigger_scan(s); wait_idle(s)`. Disable background tasks before isolated processing.
**Date**: inferred from codebase

### Forgetting photos in statistics

**Problem**: Statistics summaries missing photo counts.
**Cause**: Photos have `metadata_type=13` which is easy to forget.
**Solution**: Always include `'photos': 0` in stats dictionaries. Use DB queries with correct metadata_type mapping.
**Date**: inferred from codebase

### API-only counting

**Problem**: Plex API returns stale or incomplete data during heavy scanning.
**Cause**: Relying solely on Plex API for progress tracking.
**Solution**: Prefer direct SQLite queries to `com.plexapp.plugins.library.db` for accurate counts.
**Date**: inferred from codebase

### Using undefined function

**Problem**: Script crashes with `NameError` because function doesn't exist.
**Cause**: Calling `trigger_analysis_all()` which was never implemented (copy-paste error).
**Solution**: Verify function existence in source module before using. Correct function: `trigger_deep_analysis()`.
**Date**: inferred from codebase

### Missing imports after refactoring

**Problem**: Script crashes because function is used but not imported.
**Cause**: Refactoring moves code to `common/` but forgets to update imports.
**Solution**: After any refactoring, verify all imports. Common culprits: `find_latest_db_archive`, `read_state_file`, `trigger_scan_all`.
**Date**: inferred from codebase

### Using undefined CLI argument

**Problem**: Script crashes with `AttributeError: args has no attribute 'skip_scan'`.
**Cause**: Using `args.skip_scan` without `parser.add_argument('--skip-scan')`.
**Solution**: Verify every `args.xxx` has a corresponding `add_argument('--xxx')` definition.
**Date**: inferred from codebase

### Redundant CLI arguments

**Problem**: Confusing UX with multiple arguments doing similar things (`--skip-analysis` + `--quick-test`).
**Cause**: Organic growth without harmonization.
**Solution**: One argument per behavior. Use `--quick-test` consistently across scripts.
**Date**: inferred from codebase

### Wrong stall timeout calculation

**Problem**: Comments say "10 min stall timeout" but actual timeout is different.
**Cause**: Forgetting formula: `stall_threshold × check_interval = actual_time`.
**Solution**: Always verify: `local_quick: 5 × 30s = 2.5min`, `local_delta: 10 × 60s = 10min`, `cloud_intensive: 30 × 120s = 60min`.
**Date**: inferred from codebase

### Wildcard imports

**Problem**: Unclear which functions are available, potential name collisions.
**Cause**: Using `from module import *` for convenience.
**Solution**: Always use explicit imports: `from common.plex_scan import trigger_section_scan, wait_section_idle`.
**Date**: inferred from codebase

### Imports inside function body

**Problem**: Hidden dependencies, harder to track what module needs.
**Cause**: Lazy loading without clear justification.
**Solution**: Imports at top of file. Exception: lazy loading for expensive optional dependencies (document why).
**Date**: inferred from codebase

### Duplicating code between scripts

**Problem**: Bug fixes need to be applied in multiple places, drift between implementations.
**Cause**: Copy-pasting instead of extracting to common module.
**Solution**: Centralize shared logic in `common/` modules. Scripts should only contain orchestration logic.
**Date**: inferred from codebase

### Single-shot token retrieval

**Problem**: Token Plex non récupéré car appel unique sans retry. Le token peut mettre plusieurs secondes à apparaître dans Preferences.xml après le claim.
**Cause**: `get_plex_token()` faisait un seul appel et abandonnait immédiatement si le token n'était pas présent.
**Solution**: Implémenter retry avec timeout (120s par défaut, intervalle 10s). Le token apparaît généralement après quelques secondes.
**Date**: 2026-01-21

### Insufficient init failure diagnostics

**Problem**: `wait_plex_fully_ready()` timeout sans indication de pourquoi l'API `/identity` échoue. Impossible de diagnostiquer.
**Cause**: Logging minimal ("Plex initialisation... (processus: N)") sans détail sur l'état de l'API.
**Solution**: Logger le code HTTP et la réponse à chaque itération. En cas de timeout, capturer automatiquement les 20 dernières lignes des logs Docker.
**Date**: 2026-01-21

### Using --force with Sonic analysis triggers metadata refresh

**Problem**: Sonic analysis blocked for 2h (compteur 81,035 → 81,035) malgré CPU 407%. Aucun fichier audio lu depuis S3.
**Cause**: `--force` dans `trigger_sonic_analysis()` déclenche un refresh metadata complet (fanart.tv, lastfm, paroles) AVANT l'analyse audio Chromaprint. Le CPU était occupé à télécharger des images, pas à analyser l'audio.
**Solution**: Retirer `--force` de l'analyse Sonic. Séparer explicitement: (1) metadata refresh optionnel avec `--force-refresh`, (2) stabilisation (attente idle), (3) analyse Sonic sans --force.
**Date**: 2026-01-23

### Permission denied errors as symptom of dead rclone mount

**Problem**: Erreurs "Permission denied" lors de la création de bundles metadata (24 erreurs), semblant indiquer un problème de droits.
**Cause**: Le montage rclone FUSE s'est déconnecté (socket mort) mais reste monté en apparence. Le noyau Linux retourne des erreurs incohérentes: "Socket not connected" pour les lectures, "Permission denied" pour les écritures.
**Solution**: Ces erreurs sont un faux positif. Ne pas corriger les permissions - corriger la stabilité du montage rclone. Les erreurs disparaîtront une fois le montage fiabilisé.
**Date**: 2026-01-24

### Rclone mount disconnecting after ~30 minutes

**Problem**: Test de nuit échoue avec 1248 erreurs "Socket not connected". Le montage S3 devient inaccessible après ~30 minutes d'inactivité.
**Cause**: Configuration rclone par défaut insuffisante pour les connexions longue durée: timeout 10m trop court, pas de retries automatiques, pas de reconnexion.
**Solution**: Augmenter les paramètres de résilience: `--timeout 30m`, `--contimeout 300s`, `--retries 10`, `--retries-sleep 30s`, `--low-level-retries 10`. Ajouter `--stats 5m` pour monitoring.
**Date**: 2026-01-24

### MountHealthMonitor started too late

**Problem**: Le montage rclone devient instable pendant le prompt PLEX_CLAIM (délai utilisateur). Plex démarre avec un montage défaillant.
**Cause**: MountHealthMonitor démarré APRÈS le prompt utilisateur, pas avant. Pendant que l'utilisateur entre son claim token (potentiellement plusieurs minutes), le montage n'est pas surveillé.
**Solution**: Démarrer MountHealthMonitor AVANT le prompt PLEX_CLAIM. Le monitor surveille le montage pendant le délai utilisateur et peut faire un remontage automatique si nécessaire.
**Date**: 2026-01-29

### enable_plex_analysis_via_api() called before scan

**Problem**: wait_section_idle() timeout après 144 minutes. Le scan CLI termine rapidement mais l'attente est bloquée.
**Cause**: enable_plex_analysis_via_api() appelée en phase 4 (avant scan) déclenche le Butler DeepMediaAnalysis. Les processus `Plex Media Scanner --analyze-deeply` sont détectés par pgrep comme "scanner running", bloquant wait_section_idle().
**Solution**: Ne PAS appeler enable_plex_analysis_via_api() avant le scan. Les analyses Sonic sont correctement déclenchées par enable_music_analysis_only() en phase 6.3 (après le scan). Pour les autres sections, trigger_section_analyze() déclenche l'analyse par section via API.
**Date**: 2026-01-29

### Confusing Plex Scanner flags behavior

**Problem**: Flag `--force` fait plus que forcer l'analyse - il déclenche aussi un refresh de toutes les métadonnées.
**Cause**: Documentation Plex insuffisante sur les effets de bord de `--force`.
**Solution**: Documenter les flags Plex Scanner: `--force` = refresh metadata + action demandée. Pour analyse seule, ne pas utiliser `--force`. Vérifier dans les logs: "Updating Metadata" = refresh, "Fingerprinting"/"Sonic" = analyse audio.
**Date**: 2026-01-23

---

## Reference: Plex Metadata Types

```python
PLEX_METADATA_TYPES = {
    1: 'movie',      # Film
    2: 'show',       # Série TV
    3: 'season',     # Saison
    4: 'episode',    # Épisode
    8: 'artist',     # Artiste musical
    9: 'album',      # Album musical
    10: 'track',     # Piste audio
    13: 'photo',     # Photo
    14: 'photoalbum' # Album photo
}

PLEX_SECTION_TYPES = {
    'artist': 'Musique',
    'movie': 'Films',
    'show': 'Séries TV',
    'photo': 'Photos'
}
```

## Reference: Timeout Formulas

```python
# Temps réel avant arrêt sur stall
temps_stall = stall_threshold × check_interval

# Profils actuels:
# local_quick    : 5 × 30s   = 2.5 min
# local_delta    : 10 × 60s  = 10 min
# cloud_intensive: 30 × 120s = 60 min (1h)
```
