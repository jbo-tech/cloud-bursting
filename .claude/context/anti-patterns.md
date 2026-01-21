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
