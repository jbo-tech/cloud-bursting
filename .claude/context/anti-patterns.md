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

### Holding lock during long-running operations (deadlock)

**Problem**: Script bloqué indéfiniment après entrée PLEX_CLAIM. Aucun message, aucune progression pendant 4+ heures.
**Cause**: `_perform_health_check()` dans MountHealthMonitor détient `self._lock` pendant 30+ secondes (vérification montage + remontage éventuel). `clear_pending_input()` appelé depuis le thread principal tente d'acquérir le même lock et reste bloqué.
**Solution**: Ne jamais détenir un lock pendant des opérations I/O longues. Mieux: éviter le pattern où le thread principal interagit avec le thread monitor. Solution adoptée: input AVANT démarrage du monitor.
**Date**: 2026-01-30

### Monitor starting before user input (UX + timing issues)

**Problem**: Messages du MountHealthMonitor s'affichent pendant que l'utilisateur attend le prompt PLEX_CLAIM, créant confusion.
**Cause**: Le monitor démarre avant l'input(), le premier health check s'exécute immédiatement, et les messages (stdout) apparaissent avant ou après le prompt, masquant l'attente d'input.
**Solution**: Toujours demander les inputs utilisateur AVANT de démarrer les threads de monitoring. L'input interactif doit être isolé de tout background processing.
**Date**: 2026-01-30

## Reference: Timeout Formulas

```python
# Temps réel avant arrêt sur stall
temps_stall = stall_threshold × check_interval

# Profils actuels:
# local_quick    : 5 × 30s   = 2.5 min
# local_delta    : 10 × 60s  = 10 min
# cloud_intensive: 30 × 120s = 60 min (1h)
```

### Uninitialized variable in conditional block

**Problem**: `NameError: name 'stats_after_scan' is not defined` si la phase Music est skippée via `--section Movies`.
**Cause**: `stats_after_scan` était assignée uniquement dans le bloc `if should_process_music:`. Si la condition est False, la variable n'existe pas mais est utilisée plus tard.
**Solution**: Initialiser la variable AVANT le bloc conditionnel: `stats_after_scan = stats_before`. Toujours initialiser les variables qui seront utilisées hors du bloc où elles sont potentiellement assignées.
**Date**: 2026-01-31

### CLI argument name mismatch (args.X vs --Y)

**Problem**: `AttributeError: 'Namespace' object has no attribute 'only'` - le script crashe à l'accès d'un attribut inexistant.
**Cause**: L'argument CLI est défini comme `--section` (stocké dans `args.section`) mais le code utilise `args.only` (copie d'un autre script ou refactoring incomplet).
**Solution**: Vérifier que chaque `args.xxx` correspond à un `add_argument('--xxx')`. Après renommage d'arguments, rechercher toutes les occurrences de l'ancien nom dans le fichier.
**Date**: 2026-01-31

### Deadlock in cleanup method due to lock held by worker thread

**Problem**: Script bloqué après "✅ DELTA SYNC TERMINÉ" - la méthode `stop()` de MountHealthMonitor ne retourne jamais.
**Cause**: Dans `stop()`, appel à `_print_stats()` qui tente d'acquérir `self._lock`. Ce lock est déjà détenu par le thread de health check (`_run()` → `_perform_health_check()`). KeyboardInterrupt peut arriver pendant que le thread détient le lock.
**Solution**: Acquérir le lock avec timeout dans les méthodes de cleanup: `if self._lock.acquire(timeout=2): ... else: print("lock timeout")`. Ne jamais bloquer indéfiniment dans finally/cleanup.
**Date**: 2026-02-04

### Silent database corruption causing Plex restart loop

**Problem**: Plex démarre puis crashe en boucle avec "database disk image is malformed" dans les logs. L'erreur n'est visible que dans les logs Docker, pas dans le script.
**Cause**: Archive DB corrompue injectée sans validation. L'extraction réussit mais la DB est inutilisable. Pas de vérification d'intégrité avant démarrage Plex.
**Solution**: Exécuter `PRAGMA integrity_check;` après extraction de la DB. Vérifier que le résultat est exactement "ok" (lowercase). Échouer immédiatement si la DB est corrompue, avant de démarrer Plex.
**Date**: 2026-02-04

### PRAGMA integrity_check fails on Plex FTS tables

**Problem**: `PRAGMA integrity_check;` échoue avec "unknown tokenizer: collating" sur une DB Plex valide.
**Cause**: Plex utilise des tables FTS (Full-Text Search) avec tokenizers personnalisés non supportés par le sqlite3 système. Le PRAGMA essaie de vérifier ces tables et échoue.
**Solution**: Remplacer `PRAGMA integrity_check` par une requête simple sur une table basique: `SELECT COUNT(*) FROM library_sections;`. Valide que la DB est lisible sans toucher aux tables FTS.
**Date**: 2026-02-05

### Audit false positive: SQL injection in local config files

**Problem**: Audit signale une injection SQL sur des chemins insérés dans des requêtes sqlite3.
**Cause**: Le fichier `path_mappings.json` est contrôlé par l'utilisateur local, pas exposé à des inputs externes.
**Réalité**: FAUX POSITIF. Un attaquant ayant accès à ce fichier aurait déjà un accès complet au système. Le risque réel est proche de zéro dans ce contexte.
**Note**: Seul cas valide: si les chemins contiennent des apostrophes (`O'Brien`), échapper avec `s.replace("'", "''")`.
**Date**: 2026-02-05

### Audit false positive: imports inside functions

**Problem**: Audit critique les `import json`, `import shutil` à l'intérieur des fonctions au lieu du haut du fichier.
**Cause**: Pattern de lazy import pour éviter de charger des modules inutilisés.
**Réalité**: FAUX POSITIF. Python cache les imports, pas d'impact performance. Pattern acceptable et cohérent avec le reste du projet (ex: `import traceback` dans les blocs except).
**Date**: 2026-02-05

### Forgetting to update all scripts after adding a feature

**Problem**: Feature ajoutée dans `test_delta_sync.py` mais pas dans `automate_delta_sync.py`. Le test local fonctionne mais la production cloud échoue.
**Cause**: Les scripts local et cloud partagent les mêmes modules `common/` mais ont leur propre orchestration. Facile d'oublier de propager les changements.
**Solution**: Après ajout d'une feature touchant le workflow, toujours vérifier les 4 scripts: `test_scan_local.py`, `test_delta_sync.py`, `automate_scan.py`, `automate_delta_sync.py`.
**Date**: 2026-02-05

### os.path.exists(None) crashes with cryptic error

**Problem**: `collect_plex_logs()` crashe avec "stat: path should be string, bytes, os.PathLike or integer, not NoneType".
**Cause**: `terminal_log=None` passé par défaut, puis `os.path.exists(terminal_log)` appelé sans vérifier que la variable n'est pas None.
**Solution**: Toujours vérifier `if variable and os.path.exists(variable)` pour les chemins optionnels. Pattern: `if terminal_log and os.path.exists(terminal_log)`.
**Date**: 2026-02-05

### Stats reading during rclone timeout gives wrong values

**Problem**: Le récapitulatif affiche "728 épisodes (+-210)" alors que la DB contient réellement 938 épisodes.
**Cause**: La lecture des stats via sqlite3 a été effectuée pendant un timeout rclone (montage bloqué). La requête a retourné une valeur partielle ou incorrecte.
**Solution**: Vérifier l'état du montage rclone avant de lire les stats. Les stats finales doivent être lues après stabilisation du montage, pas pendant une période de timeout/remontage.
**Date**: 2026-02-05

### Diagnostic Sonic displayed even when Music not selected

**Problem**: Le diagnostic post-mortem affiche "🎹 DIAGNOSTIC SONIC" même quand `--section TV Shows` (pas de musique).
**Cause**: Le bloc diagnostic Sonic n'était pas conditionné par `should_process_music`.
**Solution**: Conditionner avec `if should_process_music:` et initialiser `should_process_music = True` en dehors du try/except pour qu'elle soit accessible dans finally.
**Date**: 2026-02-05

### Holding lock during long I/O operations (MountMonitor v2)

**Problem**: `stop()` affiche "Stats indisponibles (lock timeout)" au lieu des statistiques. Le script met 7+ secondes à s'arrêter.
**Cause**: `_perform_health_check()` détenait `self._lock` pendant toute la durée du health check (30s) + remontage potentiel. `stop()` ne pouvait pas acquérir le lock (join 5s + acquire 2s < health check 30s).
**Solution**: Séparer lock et I/O : les opérations longues (verify_rclone, remount) s'exécutent SANS lock. Le lock n'est acquis que pour les mises à jour d'état (microsecondes). Utiliser `threading.Event` pour interrompre le sleep immédiatement.
**Date**: 2026-02-05

### Residential internet NAT saturation during parallel S3 access

**Problem**: 2375 erreurs rclone "connection reset by peer" lors de l'analyse de 28k photos via S3 Scaleway. Analyse bloquée 4h avec compteur oscillant (28168↔28326).
**Cause**: Les box résidentielles (Free/Orange) limitent les sessions NAT concurrentes (~4096). L'analyse parallèle de milliers de fichiers sature cette limite. Le streaming (1 fichier séquentiel) fonctionne car il n'ouvre qu'une connexion.
**Solution**: Les workloads d'analyse massive doivent s'exécuter en cloud (même datacenter que S3). Les tests locaux ne sont viables que pour les petites bibliothèques (Movies: ~300 items). Ne pas confondre "le streaming marche" avec "l'analyse marchera".
**Date**: 2026-02-05

### Scan on degraded rclone mount deletes DB entries

**Problem**: Plex scanner supprime 221/224 films de la DB. Le scan progresse (0%→99%) mais ne trouve aucun fichier. Résultat: `Films: 94 (+-221)`.
**Cause**: Le montage rclone est en état dégradé (I/O bloqué). Le dir-cache (72h) permet de lister les répertoires, mais les fichiers sont inaccessibles. Plex interprète "répertoire listable, fichiers inaccessibles" comme "fichiers supprimés" et purge la DB.
**Solution**: Appeler `ensure_mount_healthy()` avant chaque `trigger_section_scan()`. Si le montage est cassé, annuler le scan. Ne JAMAIS scanner sur un montage dégradé — les dégâts sont irréversibles.
**Risque résiduel**: Le montage peut tomber PENDANT un scan (fenêtre de 60s entre les checks du MountMonitor). Pas de solution simple sans sur-ingénierie. Accepter le risque.
**Date**: 2026-02-09

### MountMonitor remount survives stop() and runs during cleanup

**Problem**: Après `mount_monitor.stop()`, des messages "🔄 Tentative de remontage 1/3..." apparaissent pendant le cleanup (après suppression des dossiers de test).
**Cause**: `remount_s3_if_needed()` prenait ~3-4 min (3 retries avec cooldowns). Le `join(timeout=35s)` expirait, le thread daemon continuait en arrière-plan pendant le cleanup.
**Solution**: Passer un `stop_event` (threading.Event) à `remount_s3_if_needed()`. Remplacer `time.sleep()` par `stop_event.wait(timeout=)` et vérifier `stop_event.is_set()` entre chaque retry. Le thread s'arrête en quelques secondes au lieu de minutes.
**Date**: 2026-02-09

### Docker image pull during scan phase wastes 30 minutes

**Problem**: 30 min d'écart entre `docker run` et le démarrage effectif de Plex. Le MountMonitor tourne pour rien, le claim token peut expirer (4 min de validité).
**Cause**: L'image `plexinc/pms-docker:latest` n'était pas en cache. `docker run` télécharge l'image avant de démarrer le conteneur.
**Solution**: Ajouter `docker pull` en Phase 1 (préparation), avant le montage S3 et le MountMonitor. Déjà fait dans `setup_instance.sh` pour le cloud, ajouté dans les scripts locaux.
**Date**: 2026-02-09

### Plex library with multiple locations pointing to different mount paths

**Problem**: Bibliothèque Photos a 2 locations (`/Media/Photo` + `/Photo`), mais le Docker ne monte que `/Media`. Toutes les photos sous `/Photo` échouent avec "FreeImage_Load: failed to open file".
**Cause**: Configuration Plex historique avec un chemin local (`/Photo`) en plus du chemin S3 (`/Media/Photo`). Le chemin local n'est pas monté dans le conteneur cloud.
**Solution**: Ajouter un mapping dans `path_mappings.json` pour consolider les chemins vers S3 (`/Photo` → `/Media/Photo`). Vérifier systématiquement que TOUS les chemins de la DB sont accessibles via le montage Docker.
**Date**: 2026-02-05

### MountMonitor with aggressive timeout on slow networks causes silent scan failure

**Problem**: Scan de Movies retourne +0 delta alors que des fichiers ont été ajoutés. Le scan semble réussir (220/221 analysés) mais ne détecte aucun nouveau fichier. Dans un second test, la machine gèle complètement.
**Cause**: Le healthcheck du MountMonitor utilise un timeout de 30s. Sur connexion résidentielle (latence variable, NAT), les lectures S3 dépassent régulièrement 30s → faux positif → remontage automatique → dir-cache rclone purgé → Plex ne voit que les fichiers déjà en DB, pas les nouveaux. Le remontage pendant des I/O FUSE actives peut aussi geler le système.
**Solution**: Ne pas utiliser MountMonitor en local. Les paramètres rclone de résilience (`--timeout 120m`, `--retries 20`) suffisent. Réserver MountMonitor pour le cloud (latence S3 <1ms, timeout 30s = vrai problème).
**Date**: 2026-02-11

### MountMonitor running during export/cleanup phases

**Problem**: Messages "Tentative de remontage" pendant l'export de la DB et le diagnostic post-mortem. Le monitor remonte inutilement alors que le montage S3 n'est plus nécessaire.
**Cause**: `mount_monitor.stop()` dans le `finally` block, donc le monitor tourne pendant toute la phase Export qui ne lit que le disque local.
**Solution**: Stopper le monitor AVANT la phase Export (`mount_monitor.stop(); mount_monitor = None`). Garder un filet de sécurité dans finally pour le cas d'exception avant l'export.
**Date**: 2026-02-11

### Scanner sees directories but not files inside (rclone FUSE)

**Problem**: Plex scanner trouve les dossiers (`Processing directory /Media/Movies/Dune (2021)`) mais pas les fichiers dedans (`File 'Dune (2021) Bluray-720p.mp4' didn't exist`). Résultat: 0 ajouté, 221 supprimé en 2 secondes.
**Cause**: Soit les fichiers en S3 ont des noms différents de ceux enregistrés en DB (archive DB de décembre 2025, fichiers potentiellement renommés depuis), soit le montage rclone FUSE ne liste pas correctement le contenu des sous-répertoires. Le rclone stats montre `Listed 586490` mais `Transferred: 0 B`.
**Solution**: Avant de lancer un delta sync, vérifier que les fichiers existent dans S3 avec les MÊMES noms que dans la DB. Commande: `rclone ls mega-s4:media-center/Movies/<dossier>/ --config ./rclone.conf`. Si les noms ont changé, un scan from scratch est nécessaire (pas un delta sync).
**Date**: 2026-02-13

### DB corruption during SQL remapping (intermittent)

**Problem**: `database disk image is malformed` pendant le UPDATE SQL de remapping des chemins. Plex crashe en boucle ensuite.
**Cause**: La DB Plex (15 GB, tables FTS) peut avoir une corruption latente non détectée par `SELECT COUNT(*) FROM library_sections`. Le simple SELECT lit quelques pages, pas les tables FTS ni les index. Un UPDATE massif (30k+ lignes dans media_parts) expose la corruption.
**Solution**: `repair_plex_db()` dans `common/delta_sync.py` détecte la corruption via `SELECT COUNT(*) FROM media_parts` et répare via `.recover`. Appelée automatiquement avant le remapping.
**Date**: 2026-02-13 (résolu 2026-02-23)

### sqlite3 .dump fails on B-tree corruption (produces empty file)

**Problem**: `sqlite3 db '.dump' | sqlite3 repaired.db` produit un fichier de 0 octets sur une DB avec index B-tree corrompus.
**Cause**: `.dump` traverse les index et les données séquentiellement. Si un index corrompu bloque la lecture d'une table, le dump s'arrête avec "database disk image is malformed" et ne produit aucune sortie SQL pour cette table.
**Solution**: Utiliser `.recover` au lieu de `.dump`. `.recover` parcourt les pages raw de la DB et reconstruit les données indépendamment des index. Les tables internes SQLite (sqlite_stat1, sqlite_sequence) sont perdues (7/82) mais Plex les recrée au démarrage. Toutes les tables de données (media_parts, metadata_items, etc.) sont récupérées.
**Date**: 2026-02-23
