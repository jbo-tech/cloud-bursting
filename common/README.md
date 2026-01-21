# Common Modules - Documentation

Modules partagés pour le workflow Plex cloud bursting.

## 📚 Vue d'ensemble

Les modules `common/` fournissent une abstraction complète pour exécuter le workflow Plex aussi bien en **local** (tests) qu'en **remote** (production cloud).

### Principe d'abstraction

Toutes les fonctions acceptent un paramètre `ip` :
- `ip = 'localhost'` → exécution locale
- `ip = '1.2.3.4'` → exécution SSH sur l'instance remote

## 📦 Modules

### `executor.py` - Exécution de commandes

Abstraction pour exécuter des commandes localement ou via SSH.

```python
from common.executor import execute_command, execute_script, docker_exec

# Exécution simple
execute_command('localhost', 'ls -la')
execute_command('1.2.3.4', 'ls -la')

# Script bash complexe (évite les problèmes d'échappement)
script = """#!/bin/bash
echo "Hello"
for i in {1..5}; do
    echo "Line $i"
done
"""
execute_script('localhost', script)

# Dans un conteneur Docker
docker_exec('localhost', 'plex', 'ls /config')

# Transfert de fichiers
transfer_file_to_remote('./rclone.conf', '1.2.3.4', '/root/.config/rclone/rclone.conf')
download_file_from_remote('1.2.3.4', '/root/archive.tar.gz', './backup.tar.gz')
```

### `config.py` - Configuration

Centralise le chargement de la configuration et les profils rclone.

```python
from common.config import load_env, load_libraries, get_rclone_profile

# Charger .env
env = load_env()
print(env['S3_BUCKET'])  # 'media-center'

# Charger bibliothèques (avec limite pour tests)
libraries = load_libraries(limit=2)
for lib in libraries:
    print(lib['title'])

# Profils rclone optimisés
config = get_rclone_profile('power')
print(config['cache_size'])  # '20G'
print(config['transfers'])   # '16'
```

**Profils disponibles :**

| Profil | Instance | vCPU | RAM | Cache | Transfers | Use Case |
|--------|----------|------|-----|-------|-----------|----------|
| `lite` | DEV1-S | 2 | 2GB | 4G | 4 | Tests rapides |
| `standard` | DEV1-M | 3 | 4GB | 10G | 8 | Production légère |
| `power` | GP1-S | 4 | 8GB | 20G | 16 | Production standard |
| `superpower` | GP1-M | 4 | 16GB | 20G | 32 | Bibliothèques massives |

### `plex_setup.py` - Cycle de vie Plex

Gère le setup complet de Plex : montage, démarrage, configuration.

```python
from common.plex_setup import (
    cleanup_plex_data,
    setup_rclone_config,
    mount_s3,
    start_plex_container,
    wait_plex_ready,
    add_library,
    stop_plex
)

ip = '1.2.3.4'  # ou 'localhost'

# 1. Nettoyage
cleanup_plex_data(ip)

# 2. Configuration rclone
setup_rclone_config(ip)

# 3. Montage S3 avec profil
mount_s3(ip, 'media-center', profile='power', mount_point='/mnt/s3-media')

# 4. Démarrage Plex
claim_token = 'claim-xxxxxxxxxxxx'
start_plex_container(
    ip=ip,
    claim_token=claim_token,
    version='latest',
    container_name='plex',
    config_path='/opt/plex_data/config',      # défaut (production)
    media_path='/mnt/s3-media',                # défaut (production)
    transcode_path='/opt/plex_data/transcode'  # défaut (production)
)

# Pour tests locaux avec volumes dans ./tmp/
start_plex_container(
    ip='localhost',
    claim_token=claim_token,
    config_path='/home/user/tmp/plex-config',
    media_path='/home/user/tmp/s3-media',
    transcode_path='/home/user/tmp/plex-config/transcode'
)

# 5. Attendre que Plex soit prêt
wait_plex_ready(ip, container='plex', timeout=120)

# 6. Ajouter bibliothèques
library_config = {
    'title': 'Movies',
    'type': 'movie',
    'agent': 'tv.plex.agents.movie',
    'scanner': 'Plex Movie',
    'language': 'fr-FR',
    'paths': ['/Media/Movies']
}
add_library(ip, 'plex', library_config)

# 7. Arrêt propre
stop_plex(ip, container='plex')
```

### `plex_scan.py` - Scan et monitoring

Gère le scan Plex et le monitoring des phases de traitement.

```python
from common.plex_scan import (
    trigger_scan_all,
    monitor_discovery_phase,
    trigger_analysis_all,
    monitor_analysis_phase,
    export_metadata
)

ip = '1.2.3.4'  # ou 'localhost'

# Phase 1 : Découverte (scan des fichiers)
trigger_scan_all(ip, container='plex', force=True)
monitor_discovery_phase(ip, container='plex', check_interval=30, max_idle=5)

# Phase 2 : Analyse (thumbnails, sonic, intro detection)
trigger_analysis_all(ip, container='plex')
monitor_analysis_phase(ip, container='plex', check_interval=60, timeout=7200)

# Export métadonnées
archive_path = export_metadata(ip, container='plex', archive_name='backup.tar.gz')
print(f"Archive créée : {archive_path}")
```

## 🎯 Exemples d'utilisation

### Test local complet

```python
#!/usr/bin/env python3
from common.config import load_env, load_libraries
from common.executor import execute_command
from common.plex_setup import *
from common.plex_scan import *

# Configuration
ip = 'localhost'
env = load_env()
libraries = load_libraries(limit=1)

# Workflow complet
cleanup_plex_data(ip)
setup_rclone_config(ip)
mount_s3(ip, env['S3_BUCKET'], profile='lite')
start_plex_container(ip, 'claim-xxxxx')
wait_plex_ready(ip)

for lib in libraries:
    add_library(ip, 'plex', lib)

trigger_scan_all(ip, force=True)
monitor_discovery_phase(ip)
trigger_analysis_all(ip)
monitor_analysis_phase(ip)

archive = export_metadata(ip)
stop_plex(ip)
```

### Production cloud

```python
#!/usr/bin/env python3
from common.config import load_env, load_libraries
from common.plex_setup import *
from common.plex_scan import *

# Après création de l'instance Scaleway
ip = '1.2.3.4'  # IP publique de l'instance
env = load_env()
libraries = load_libraries()  # Toutes les bibliothèques

# Workflow identique !
setup_rclone_config(ip)
mount_s3(ip, env['S3_BUCKET'], profile='power')
start_plex_container(ip, env['PLEX_CLAIM'])
wait_plex_ready(ip)

for lib in libraries:
    add_library(ip, 'plex', lib)

trigger_scan_all(ip, force=True)
monitor_discovery_phase(ip)
trigger_analysis_all(ip)
monitor_analysis_phase(ip)

archive = export_metadata(ip)
# Puis destruction de l'instance
```

## 🔧 Gestion des erreurs

Les fonctions lèvent des exceptions en cas d'erreur :

```python
try:
    wait_plex_ready(ip, timeout=120)
except TimeoutError:
    print("Plex n'a pas démarré à temps")
except subprocess.CalledProcessError as e:
    print(f"Erreur lors de l'exécution : {e}")
```

## 📊 Monitoring

Les fonctions de monitoring affichent la progression en temps réel :

```
👁️  Surveillance de la phase de découverte...
   [21:30:15] Bundles: 42 | Scanner: 🟢
   [21:30:45] Bundles: 89 | Scanner: 🟢
   [21:31:15] Bundles: 156 | Scanner: 🟢
   [21:31:45] Bundles: 156 | Scanner: 🔴
✅ Phase de découverte terminée : 156 médias détectés
```

## 🚀 Performance

Les profils rclone sont optimisés selon les ressources :

```python
# Instance légère → cache modeste
mount_s3(ip, bucket, profile='lite')     # 4G cache, 4 transfers

# Instance puissante → cache agressif
mount_s3(ip, bucket, profile='power')    # 20G cache, 16 transfers
```

## ⚙️ Configuration requise

### Fichiers nécessaires
- `.env` - Variables d'environnement
- `rclone.conf` - Configuration rclone (accès S3)
- `plex_libraries.json` - Liste des bibliothèques

### Dépendances
```bash
pip install python-dotenv
```

### Outils système
- `docker` - Pour lancer Plex
- `rclone` - Pour monter S3
- `fusermount3` - Pour démonter
- `ssh`, `scp` - Pour remote (production)

## 📝 Notes

### Script vs commande simple

Utiliser `execute_script()` pour les commandes complexes :

```python
# ❌ Problèmes d'échappement
execute_command(ip, "cd /opt && tar -czf backup.tar.gz 'Library/Application Support'")

# ✅ Robuste
script = """
cd /opt
tar -czf backup.tar.gz 'Library/Application Support'
"""
execute_script(ip, script)
```

### Chemins locaux

En local, tous les volumes sont organisés dans `./tmp/` pour faciliter le nettoyage :

```python
# Production (cloud) : volumes système
mount_s3('1.2.3.4', bucket, mount_point='/mnt/s3-media')
start_plex_container(
    ip='1.2.3.4',
    claim_token=token,
    config_path='/opt/plex_data/config',      # défaut
    media_path='/mnt/s3-media',                # défaut
    transcode_path='/opt/plex_data/transcode' # défaut
)

# Tests locaux : volumes dans ./tmp/
TEST_DIR = Path(__file__).parent / "tmp"
mount_s3('localhost', bucket, mount_point=str(TEST_DIR / 's3-media'))
start_plex_container(
    ip='localhost',
    claim_token=token,
    config_path=str(TEST_DIR / 'plex-config'),
    media_path=str(TEST_DIR / 's3-media'),
    transcode_path=str(TEST_DIR / 'plex-config/transcode')
)

# Structure ./tmp/ (créée automatiquement)
# tmp/
# ├── s3-media/           # Point de montage rclone
# └── plex-config/        # Configuration Plex
#     └── transcode/      # Fichiers de transcode
```

## 🔍 Débogage

Activer le mode verbose :

```python
# Les fonctions affichent déjà les commandes exécutées
execute_command('localhost', 'echo test')
# 🔧 [LOCAL] echo test...
```

Pour capturer la sortie :

```python
result = execute_command(ip, 'ls -la', capture_output=True)
print(result.stdout)
print(result.returncode)
```

## 🎓 Bonnes pratiques

1. **Toujours utiliser `execute_script()` pour les scripts multi-lignes**
2. **Vérifier les timeouts** selon la taille de votre bibliothèque
3. **Utiliser le profil adapté** à vos ressources
4. **Limiter les bibliothèques** pendant les tests (`load_libraries(limit=1)`)
5. **Nettoyer proprement** avec `stop_plex()` avant export

## 📚 Ressources

- [REFACTORING.md](../REFACTORING.md) - Plan de refactorisation complet
- [REFACTORING_STATUS.md](../REFACTORING_STATUS.md) - État d'avancement
- [CLAUDE.md](../CLAUDE.md) - Documentation du projet global
