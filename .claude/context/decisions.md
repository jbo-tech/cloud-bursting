# Decisions

Technical decisions and their context. Added via `/retro`.

<!-- Format:
### [Decision title]
**Decision**: What was decided
**Context**: Why this choice
**Alternatives considered**: What else was considered
**Date**: YYYY-MM-DD
-->

### Cloud Provider: Scaleway

**Decision**: Use Scaleway as the cloud provider with ephemeral instances (GP1-S/M).
**Context**: French provider with competitive pricing, simple CLI (`scw`), and good performance for compute-intensive tasks. The project needs powerful but temporary instances that can be destroyed immediately to stop billing.
**Alternatives considered**: AWS (more complex, higher cost), GCP, OVH.
**Date**: inferred from codebase

### Storage: S3 + rclone mount

**Decision**: Mount S3 bucket via rclone FUSE instead of syncing files locally.
**Context**: Media library is 9 TB - copying to instance would be too slow and expensive (storage + time). rclone mount provides direct access with configurable caching.
**Alternatives considered**: EBS volumes (expensive for 9TB), local sync (too slow), NFS (complexity).
**Date**: inferred from codebase

### Permissions: UID 1000 / GID 1000

**Decision**: Always use `--uid 1000 --gid 1000` with rclone mount.
**Context**: Critical fix. Plex container runs as user plex (UID 1000). Without explicit UID/GID, rclone mounts as root and Plex cannot read files, resulting in empty scans. This was the root cause of early failures.
**Alternatives considered**: Running Plex as root (security risk), chown (not possible on FUSE mount).
**Date**: inferred from codebase

### Orchestration: Pure Python + subprocess

**Decision**: Use Python scripts calling `scw` CLI directly instead of Terraform/Ansible.
**Context**: Single-purpose workflow with simple state (one instance at a time). Python provides flexibility for monitoring and complex logic. State stored in flat files (.current_instance_id).
**Alternatives considered**: Terraform (overkill for ephemeral single instance), Ansible (adds complexity), Bash (harder to maintain).
**Date**: inferred from codebase

### Execution Abstraction: localhost vs remote

**Decision**: Single `execute_command(ip, cmd)` API that routes to local bash or SSH based on ip='localhost'.
**Context**: Enables testing the entire workflow locally before cloud deployment. Same code path for both environments reduces bugs.
**Alternatives considered**: Separate scripts for local/remote (code duplication), Docker-in-Docker (complexity).
**Date**: inferred from codebase

### Containerization: Official Plex Docker Image

**Decision**: Use `plexinc/pms-docker:latest` in Docker instead of bare metal installation.
**Context**: Ensures consistent environment between local tests and cloud. Easy to start/stop/destroy. Pre-pulled in cloud-init to speed up workflow.
**Alternatives considered**: Bare metal Plex (configuration drift), custom image (maintenance burden).
**Date**: inferred from codebase

### Monitoring: DB-based over API-based

**Decision**: Query Plex SQLite database directly for progress tracking instead of relying on Plex API.
**Context**: Plex API can be unreliable during heavy scanning (timeouts, stale data). Direct SQL queries to `com.plexapp.plugins.library.db` provide accurate counts for tracks, Sonic analysis status, etc.
**Alternatives considered**: Plex API only (unreliable), log parsing (fragile).
**Date**: inferred from codebase

### Sequential Strict Processing

**Decision**: Disable all Plex background tasks, process one section at a time, then re-enable.
**Context**: Parallel processing caused resource contention (CPU, I/O) and unpredictable behavior. Sequential processing with explicit task control (`disable_all_background_tasks()` / `enable_music_analysis_only()`) is more reliable.
**Alternatives considered**: Parallel section processing (unstable), letting Plex auto-manage (unpredictable).
**Date**: inferred from codebase

### Global Scan over Chunked Scan

**Decision**: Use `/library/sections/all/refresh?force=1` instead of scanning folder by folder.
**Context**: Chunked scanning (folder by folder) caused Plex to become unstable and block after ~10 minutes. Global scan lets Plex optimize internally while external monitoring tracks progress.
**Alternatives considered**: Folder-by-folder scan (Plex instability), sequential library scan (too slow).
**Date**: inferred from codebase

### Cloud-init with FUSE Configuration

**Decision**: Use cloud-init script (`setup_instance.sh`) that pre-configures FUSE `user_allow_other`.
**Context**: Without `user_allow_other` in `/etc/fuse.conf`, Docker containers cannot access FUSE mounts. This caused freezes. Cloud-init ensures the fix is applied before any rclone mount attempt.
**Alternatives considered**: Manual configuration (error-prone), custom AMI (maintenance burden).
**Date**: inferred from codebase

### Scan vs Analyze Phase Separation

**Decision**: Treat scan (discovery) and analyze (metadata generation) as separate phases with different monitoring strategies.
**Context**: Scan progress is tracked by item count increase. Analysis progress requires CPU/process monitoring because item count doesn't change. Different timeouts and stall detection for each phase.
**Alternatives considered**: Single combined phase (harder to monitor), Plex-managed (unpredictable timing).
**Date**: inferred from codebase

### Adaptive Timeouts by Section Type

**Decision**: Photos section gets 4h timeout instead of standard 1h.
**Context**: Photo thumbnail generation is much slower than video/music processing. Fixed timeouts caused premature termination. `section_type='photo'` parameter triggers extended timeout.
**Alternatives considered**: Single timeout for all (premature kills), infinite timeout (runaway costs).
**Date**: inferred from codebase

### Instance Profiles

**Decision**: Define 4 instance profiles (lite/standard/power/superpower) with matched rclone configurations.
**Context**: Different workloads need different resources. Testing uses lite (DEV1-S), production uses power (GP1-S) or superpower (GP1-M) for Sonic analysis. Profiles include instance type, volume size, and rclone cache/transfer settings.
**Alternatives considered**: Single instance type (waste or insufficient), manual configuration (error-prone).
**Date**: inferred from codebase

### Retry pattern for Plex token retrieval

**Decision**: `get_plex_token()` retries toutes les 10s pendant 120s (configurable) au lieu d'un appel unique.
**Context**: Le token Plex (PlexOnlineToken) peut mettre plusieurs secondes à apparaître dans Preferences.xml après le claim initial. Un appel unique échouait systématiquement si le timing n'était pas parfait.
**Alternatives considered**: Sleep fixe avant appel (fragile), augmenter le délai d'init (ne résout pas le problème).
**Date**: 2026-01-21

### Automatic Docker logs capture on init timeout

**Decision**: En cas de timeout de `wait_plex_fully_ready()`, capturer automatiquement les 20 dernières lignes de logs Docker pour diagnostic.
**Context**: Les échecs d'init étaient impossibles à diagnostiquer car le conteneur était souvent arrêté avant qu'on puisse voir les logs. La capture automatique préserve le contexte.
**Alternatives considered**: Collecte manuelle (souvent trop tard), logs complets (trop verbeux).
**Date**: 2026-01-21

### Extended timeouts for cloud with injected DB

**Decision**: Timeouts plus longs en cloud: init Plex 600s (vs 300s), token 180s (vs 120s), Plex Pass 120s (vs 60s).
**Context**: Avec une DB de 15GB injectée, Plex peut mettre plus de temps à initialiser (migration DB, index). Les timeouts locaux étaient insuffisants pour le contexte cloud.
**Alternatives considered**: Timeouts identiques (échecs fréquents), timeouts infinis (coûts cloud).
**Date**: 2026-01-21

### Three-phase Sonic workflow: Refresh → Stabilize → Analyze

**Decision**: Séparer le traitement Sonic en 3 sous-phases distinctes: (a) metadata refresh optionnel, (b) stabilisation (attente idle), (c) analyse Sonic pure.
**Context**: Le flag `--force` de Plex Scanner déclenche un refresh metadata complet AVANT l'action demandée. Pour 450k pistes, ce refresh prend 2h+ (téléchargement images/paroles). L'analyse Sonic ne démarre jamais si le refresh n'est pas terminé.
**Alternatives considered**: Tout en une phase (impossible de distinguer refresh vs analyse), --force systématique (2h+ de refresh à chaque run).
**Date**: 2026-01-23

### Stabilization check before intensive analysis

**Decision**: Nouvelle fonction `wait_plex_stabilized()` qui attend: (1) 0 activités API, (2) scanner stoppé, (3) CPU < seuil pendant N checks consécutifs.
**Context**: Lancer Sonic immédiatement après scan/refresh peut échouer car Plex a des tâches de fond en cours. La stabilisation garantit que Plex est vraiment idle avant de démarrer une nouvelle phase intensive.
**Alternatives considered**: Sleep fixe (fragile), monitoring CPU seul (insuffisant), pas d'attente (conflits de ressources).
**Date**: 2026-01-23

### Metadata refresh monitoring profile

**Decision**: Nouveau profil `metadata_refresh` avec timeout 4h, CPU threshold 20%, check interval 2min.
**Context**: Le refresh metadata (images, paroles, matching) est une opération longue et CPU-intensive différente du scan ou de l'analyse. Profil dédié pour éviter les faux positifs de stall detection.
**Alternatives considered**: Réutiliser profil scan (timeouts inadaptés), profil cloud_intensive (thresholds incorrects).
**Date**: 2026-01-23

### Mandatory environment variables for deployment scripts

**Decision**: Les scripts de déploiement distant (`update_to_distant_plex.sh`) utilisent des variables d'environnement obligatoires sans valeurs par défaut (`PLEX_REMOTE_HOST`, `PLEX_REMOTE_PATH`).
**Context**: Les scripts sont versionnés dans Git. Des valeurs par défaut hardcodées (user, hostname, chemins) exposeraient des informations personnelles. Forcer l'utilisateur à définir explicitement les variables garantit qu'aucune donnée sensible n'est commitée.
**Alternatives considered**: Valeurs par défaut avec override (risque d'oubli et commit de données personnelles), fichier .env non versionné (ajoute complexité), arguments CLI uniquement (moins pratique pour usage répété).
**Date**: 2026-01-24

### Rclone resilience parameters for long-running mounts

**Decision**: Configuration rclone avec paramètres de résilience: `--timeout 30m`, `--contimeout 300s`, `--retries 10`, `--retries-sleep 30s`, `--low-level-retries 10`, `--stats 5m`.
**Context**: Les tests de nuit (6h+) échouaient car le montage rclone se déconnectait après ~30 minutes d'inactivité relative. Les paramètres par défaut (timeout 10m, 2 retries) sont insuffisants pour les workflows longs avec lectures sporadiques.
**Alternatives considered**: Cron de remontage périodique (complexe), watchdog externe (surcharge), augmentation cache uniquement (ne résout pas les déconnexions réseau).
**Date**: 2026-01-24

### Rclone mount healthcheck with automatic remount

**Decision**: Nouvelles fonctions `verify_rclone_mount_healthy()` et `remount_s3_if_needed()` pour détecter et corriger les montages morts.
**Context**: Même avec des paramètres de résilience, un montage FUSE peut devenir "stale" (socket déconnecté mais toujours monté). Un healthcheck avec timeout de 10s et remontage automatique permet de récupérer sans intervention manuelle.
**Alternatives considered**: Supervision systemd (ne détecte pas les sockets morts), test manuel avant chaque phase (fastidieux), ignorer et espérer (échecs nocturnes).
**Date**: 2026-01-24

### CLI argument naming: --instance vs --monitoring

**Decision**: Renommer `--profile` en `--monitoring` pour clarifier son rôle. Conserver `--instance` pour les ressources matérielles.
**Context**: Confusion entre deux arguments aux noms similaires mais aux rôles différents. `--instance` (lite/standard/power/superpower) contrôle les ressources (rclone, Docker limits). `--profile` contrôlait les timeouts de monitoring mais le nom suggérait autre chose. `--monitoring` (local/cloud) est plus explicite.
**Alternatives considered**: `--timeout-profile` (trop long), `--patience` (pas assez technique), `--run-mode` (suggère d'autres différences).
**Date**: 2026-01-28

### MountHealthMonitor before user prompt

**Decision**: Démarrer MountHealthMonitor AVANT le prompt PLEX_CLAIM, pas après.
**Context**: Le montage rclone peut devenir instable pendant que l'utilisateur entre son claim token. Si le délai est long (plusieurs minutes), le montage peut être défaillant au démarrage de Plex. En démarrant le monitor avant le prompt, il surveille et peut remonter automatiquement si nécessaire.
**Alternatives considered**: Vérification ponctuelle avant Plex (ne surveille pas pendant le délai), pas de changement (montage potentiellement mort au démarrage).
**Date**: 2026-01-29

### Sonic analysis triggered by enable_music_analysis_only(), not enable_plex_analysis_via_api()

**Decision**: Utiliser enable_music_analysis_only() en phase 6.3 pour déclencher Sonic. Ne pas utiliser enable_plex_analysis_via_api() avant le scan.
**Context**: enable_plex_analysis_via_api() déclenche le Butler DeepMediaAnalysis globalement. Les processus `--analyze-deeply` sont détectés comme "scanner running" par wait_section_idle(), causant un timeout de 144 minutes. enable_music_analysis_only() est appelée APRÈS le scan, au bon moment.
**Alternatives considered**: Modifier wait_section_idle() pour distinguer --scan vs --analyze-deeply (plus complexe, risque de régression), garder enable_plex_analysis_via_api() avec un flag pour ne pas déclencher Butler (API incohérente).
**Date**: 2026-01-29

### Include rclone.log in exported archives

**Decision**: Ajouter paramètre `rclone_log` à collect_plex_logs() et l'inclure dans l'archive combinée.
**Context**: Les logs rclone sont essentiels pour diagnostiquer les problèmes de montage S3 (déconnexions, timeouts, erreurs I/O). Sans ces logs dans l'export, le diagnostic post-mortem est incomplet.
**Alternatives considered**: Export séparé de rclone.log (deux archives à gérer), copie manuelle (oubli fréquent).
**Date**: 2026-01-29
