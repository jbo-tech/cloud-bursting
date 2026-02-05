# 🚀 Plex Cloud Scanner

**Scan your massive Plex media library in the cloud for less than $1**

Delegate heavy Plex media scanning to a powerful cloud instance, then bring back only the database to your local server. Perfect for low-power home servers (Raspberry Pi, ZimaBoard, NAS) with large media libraries.

---

## ⚡ The Problem

- Your home server is too weak to scan 9TB+ of media files
- Full library scans take days and make your server unusable
- Your media is already in cloud storage (S3, Backblaze, etc.)
- You want to keep streaming locally but scan in the cloud

## ✨ The Solution

This tool automatically:
1. Spins up a powerful cloud instance (Scaleway)
2. Mounts your cloud storage (S3/rclone)
3. Runs Plex to scan your entire library
4. Downloads the generated database
5. Destroys the instance (stops billing)
6. Applies the database to your local Plex

**Result**: 6-hour cloud scan for ~$1 instead of 3-day local scan

---

## 📋 Prerequisites

- [Scaleway account](https://www.scaleway.com/fr/cli/) with CLI configured (`scw init`)
- Cloud storage with your media (S3, Backblaze B2, etc.)
- [rclone](https://rclone.org/) configured for your storage
- Python 3.7+ with `python-dotenv`
- Docker (for local testing)
- Local Plex Media Server (for final deployment)

---

## 🛠️ Installation

### 1. Clone the repository
```bash
git clone https://github.com/yourusername/plex-cloud-scanner.git
cd plex-cloud-scanner
```

### 2. Install dependencies
```bash
pip install python-dotenv
```

### 3. Configure environment variables
```bash
# Copy example and edit
cp .env.example .env
nano .env
```

**Example `.env`:**
```bash
# === SCALEWAY INSTANCE CONFIG ===
export LITE_INSTANCE_TYPE="DEV1-S"
export LITE_ROOT_VOLUME_SIZE="20G"
export STANDARD_INSTANCE_TYPE="DEV1-M"
export STANDARD_ROOT_VOLUME_SIZE="20G"
export POWER_INSTANCE_TYPE="GP1-S"
export POWER_ROOT_VOLUME_SIZE="100G"
export SUPERPOWER_INSTANCE_TYPE="GP1-M"
export SUPERPOWER_ROOT_VOLUME_SIZE="100G"

export SCW_DEFAULT_ZONE="fr-par-1"
export IMAGE="debian_bookworm"

# === PLEX CONFIG ===
export PLEX_VERSION="latest"
export S3_BUCKET="media-center"

# === LOCAL CONFIG (for deployment) ===
export PLEX_LOCAL_CONTAINER_NAME="plex"
export ZIMABOARD_IP="192.168.1.97"
export PLEX_CONFIG_PATH="/mnt/smallfeet/DATA/AppData/plex/config"
```

### 4. Set up rclone
```bash
# Interactive configuration
rclone config

# Test your connection
rclone lsd mega-s4:media-center

# Copy config to project
cp ~/.config/rclone/rclone.conf ./
```

### 5. Configure Plex libraries
```bash
# Copy example and edit to match your setup
cp plex_libraries.json.example plex_libraries.json
nano plex_libraries.json
```

**Example `plex_libraries.json`:**
```json
[
  {
    "title": "Movies",
    "type": "movie",
    "agent": "tv.plex.agents.movie",
    "scanner": "Plex Movie",
    "language": "en-US",
    "paths": ["/Media/Movies"]
  },
  {
    "title": "TV Shows",
    "type": "show",
    "agent": "tv.plex.agents.series",
    "scanner": "Plex TV Series",
    "language": "en-US",
    "paths": ["/Media/TV"]
  }
]
```

### 6. Configure path mappings (optional)

If you've migrated your S3 folder structure and need to remap paths in an existing Plex database:

```bash
# Copy example and edit
cp path_mappings.json.example path_mappings.json
nano path_mappings.json
```

**Example `path_mappings.json`:**
```json
{
  "_comment": "Maps old paths to new paths after S3 folder restructuring",
  "mappings": {
    "/Media/TVShows": "/Media/TV",
    "/Media/Kids/TV Shows": "/Media/Kids/TV"
  }
}
```

This is used by `test_delta_sync.py` and `automate_delta_sync.py` to automatically update paths in `section_locations` and `media_parts` tables when injecting an existing database.

---

## 🚀 Usage

### ✅ Validate Installation

Test that everything is set up correctly:

```bash
python3 test_common_modules.py
```

You should see:
```
✅ TOUS LES TESTS PASSENT
Tests réussis : 6/6
```

### 🧪 Local Testing (No Cloud Costs)

Test the workflow locally before running in the cloud:

```bash
# Quick test with 1 library, no scan
python3 test_scan_local.py --test 1 --skip-scan --instance lite

# Full test with scan (15-30 min)
python3 test_scan_local.py --test 1 --instance lite

# Test with power profile (faster)
python3 test_scan_local.py --test 2 --instance power
```

**Local test options:**
- `--test N` : Limit to N libraries
- `--instance {lite,standard,power,superpower}` : Choose performance profile
- `--skip-scan` : Skip scanning phase (setup only)
- `--keep` : Keep Docker container after test

### ☁️ Cloud Production Scan

Once local tests pass, run in the cloud:

```bash
# Test with 2 libraries first
python3 automate_scan.py --test 2 --instance lite

# Full production scan
python3 automate_scan.py --instance power

# Setup only (no scan)
python3 automate_scan.py --skip-scan --instance lite
```

**Cloud options:**
- `--test N` : Limit to N libraries for testing
- `--instance {lite,standard,power,superpower}` : Choose instance type
  - `lite` : DEV1-S (2 vCPU, 2GB RAM) - €0.02/h - For small libraries
  - `standard` : DEV1-M (3 vCPU, 4GB RAM) - €0.04/h - Balanced
  - `power` : GP1-S (8 vCPU, 16GB RAM) - €0.15/h - Recommended ⭐
  - `superpower` : GP1-M (8 vCPU, 32GB RAM) - €0.30/h - Large libraries + Sonic
- `--skip-scan` : Setup environment only (testing)
- `--skip-analysis` : Skip Sonic analysis (faster, metadata only)
- `--keep` : Keep instance running after completion (for debugging)

The script will automatically:
1. ✅ Create cloud instance
2. ✅ Wait for cloud-init to complete
3. ✅ Mount S3 storage with optimized rclone config
4. ✅ Configure Plex libraries
5. ✅ Run scan (discovery + analysis)
6. ✅ Export and download metadata
7. ✅ Destroy instance (stop billing)

**Expected output:**
```
============================================================
✅ WORKFLOW TERMINÉ AVEC SUCCÈS
============================================================
📦 Archive : ./plex_metadata.tar.gz

💡 Prochaine étape :
   Déployer sur ZimaBoard avec : ./update_local_plex.sh
```

### 📦 Deploy to Local Server

Apply the generated database to your local Plex:

```bash
# On your local server
./update_local_plex.sh
```

---

## 📊 Performance Profiles

Rclone mount performance is automatically optimized based on instance type:

| Profile | Instance | vCPU | RAM | Cache | Transfers | Use Case |
|---------|----------|------|-----|-------|-----------|----------|
| `lite` | DEV1-S | 2 | 2GB | 4G | 4 | Quick tests, small libraries |
| `standard` | DEV1-M | 3 | 4GB | 10G | 8 | Medium libraries (< 1TB) |
| `power` | GP1-S | 8 | 16GB | 20G | 16 | **Recommended** for most cases |
| `superpower` | GP1-M | 8 | 32GB | 20G | 32 | Massive libraries (> 5TB) + Sonic |

**Cost estimate** (6-hour scan):
- `lite` : €0.12
- `standard` : €0.24
- `power` : €0.90 ⭐
- `superpower` : €1.80

---

## 📁 Project Structure

```
plex-cloud-scanner/
├── common/                    # 📦 Reusable modules
│   ├── __init__.py
│   ├── executor.py           # Local/remote command execution
│   ├── config.py             # Configuration management
│   ├── plex_setup.py         # Plex lifecycle management
│   ├── plex_scan.py          # Scan & monitoring
│   └── README.md             # Module documentation
│
├── automate_scan.py          # ☁️ Cloud workflow (349 lines)
├── test_scan_local.py        # 🧪 Local testing workflow (227 lines)
├── test_common_modules.py    # ✅ Unit tests
│
├── create_instance.sh        # Scaleway instance creation
├── destroy_instance.sh       # Scaleway instance cleanup
├── setup_instance.sh         # Cloud-init configuration
├── update_local_plex.sh      # Deploy to local server
│
├── .env                      # Environment variables
├── rclone.conf              # S3/storage configuration
├── plex_libraries.json      # Library definitions
│
└── README.md                # This file
```

---

## 🔧 Advanced Usage

### Custom Workflows

The `common/` modules can be used for custom workflows:

```python
#!/usr/bin/env python3
from common.config import load_env, load_libraries
from common.executor import execute_command
from common.plex_setup import mount_s3, start_plex_container

# Your custom workflow
ip = 'localhost'  # or remote IP
env = load_env()

# Mount S3 with custom profile
mount_s3(ip, env['S3_BUCKET'], profile='power')

# Start Plex
start_plex_container(ip, 'claim-token-here')
```

See [common/README.md](common/README.md) for full API documentation.

### Running Without Scan

Useful for testing setup or updating configuration only:

```bash
# Cloud
python3 automate_scan.py --skip-scan --instance lite

# Local
python3 test_scan_local.py --skip-scan --instance lite
```

### Keeping Test Environment

For debugging, keep the local Docker container running:

```bash
python3 test_scan_local.py --keep
# Manually inspect with: docker logs plex
```

---

## 🔍 How It Works

### Architecture

```
┌─────────────────┐
│  Local Machine  │
│  (Orchestrator) │
└────────┬────────┘
         │
         ├──> Create Instance (Scaleway)
         │
┌────────▼─────────────────┐
│  Cloud Instance (Temp)   │
│  ├─ rclone mount S3      │
│  ├─ Docker Plex          │
│  ├─ Scan media           │
│  └─ Export metadata      │
└────────┬─────────────────┘
         │
         ├──> Download archive
         │
┌────────▼────────┐
│  ZimaBoard      │
│  (Plex Server)  │
└─────────────────┘
```

### Workflow Steps

1. **Provisioning**: Create Scaleway instance with cloud-init
2. **Configuration**: Copy rclone config, mount S3 with optimized settings
3. **Plex Setup**: Start Plex container, configure libraries
4. **Discovery Phase**: Scan files, detect media (monitored by bundle count)
5. **Analysis Phase**: Generate thumbnails, intro detection (monitored by CPU)
6. **Export**: Stop Plex, create archive of database + metadata
7. **Download**: Transfer archive locally via SCP
8. **Cleanup**: Destroy instance, stop billing

---

## 🐛 Troubleshooting

### Common Issues

**Import errors**
```bash
# Verify you're in the right directory
cd /path/to/cloud-bursting

# Test imports
python3 -c "from common.config import load_env; print('OK')"
```

**rclone.conf missing**
```bash
# Copy from your home directory
cp ~/.config/rclone/rclone.conf .

# Test connection
rclone lsd mega-s4:media-center
```

**Docker permission denied**
```bash
# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

**S3 mount fails**
```bash
# Test rclone manually
rclone ls mega-s4:media-center | head -5

# Check logs
tail -f /var/log/rclone.log
```

**Plex doesn't start**
```bash
# Check Docker logs
docker logs plex

# Verify ports
curl -I http://localhost:32400/identity
```

**Scan finds no media**
```bash
# Verify mount
mountpoint /mnt/s3-media
ls /mnt/s3-media

# Check paths in plex_libraries.json
# Paths should match your bucket structure
```

### Debug Mode

For detailed debugging:

```bash
# Check all tests
python3 test_common_modules.py

# Test setup only (no scan)
python3 test_scan_local.py --test 1 --skip-scan --keep

# Then inspect manually
docker logs plex
docker exec plex ls /media
```

### Instance Issues

**Instance won't start**
- Check Scaleway quotas in console
- Verify `scw init` configuration
- Check zone availability

**Instance stuck**
- Manually destroy: `./destroy_instance.sh`
- Check Scaleway console
- Verify no leftover instances

---

## 📚 Documentation

- **[common/README.md](common/README.md)** - Module API reference
- **[REFACTORING_STATUS.md](REFACTORING_STATUS.md)** - Refactoring details
- **[CLAUDE.md](CLAUDE.md)** - Project context & architecture

---

## 💡 Tips & Best Practices

### Cost Optimization

1. **Test locally first**: Use `test_scan_local.py` to validate configuration
2. **Start small**: Use `--test 1` for initial cloud runs
3. **Choose right profile**: `lite` for testing, `power` for production
4. **Monitor progress**: The script shows real-time progress
5. **Instance is destroyed**: Billing stops automatically after completion

### Performance

1. **Use power profile**: Faster scans = lower costs
2. **Group similar libraries**: Scan movies separately from TV shows
3. **Skip unnecessary analysis**: Use `--skip-scan` if only updating config
4. **Local testing**: Iterate locally before cloud runs

### Safety

1. **Backup first**: Always backup your local Plex database
2. **Test with subset**: Use `--test 1` before full scan
3. **Verify paths**: Check `plex_libraries.json` paths match your bucket
4. **Monitor costs**: Check Scaleway billing after first run

---

## 🤝 Contributing

Contributions welcome! Areas for improvement:
- Additional cloud providers (AWS, GCP, Azure)
- Better error handling and recovery
- Progress persistence (resume after failure)
- Web UI for monitoring

Please open an issue before starting major changes.

---

## 📝 License

MIT License - See LICENSE file

---

## ⚠️ Important Notes

- **Cloud instances are ephemeral**: Everything is destroyed after completion
- **Media stays in S3**: Only metadata is downloaded (database + thumbnails)
- **Test locally first**: Use `test_scan_local.py` before cloud runs
- **Backup before importing**: Make a backup of your local Plex database
- **Monitor first run**: Watch the process to understand timing and costs

---

## 🎯 Quick Reference

```bash
# Validate installation
python3 test_common_modules.py

# Local test (free)
python3 test_scan_local.py --test 1 --instance lite

# Cloud test (small cost)
python3 automate_scan.py --test 2 --instance lite

# Production scan
python3 automate_scan.py --instance power

# Deploy to local server
./update_local_plex.sh
```

---

**Need help?** Check [common/README.md](common/README.md) for detailed API documentation.
