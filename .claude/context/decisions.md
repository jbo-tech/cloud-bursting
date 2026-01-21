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
