## Titre : Cloud Bursting Hybride pour l'Indexation de Bibliothèque Plex

### 📋 **Objectif Principal**
Déléguer le scan intensif de Plex (génération de métadonnées pour 9 To de médias) vers une instance cloud puissante, puis rapatrier les résultats vers un serveur local (ZimaBoard).

* **Problématique :** Un serveur Plex local hébergé sur un **ZimaBoard** est excellent pour le streaming (faible consommation, faible coût), mais manque de puissance CPU pour les tâches d'indexation initiales ou de fond (scan, analyse des médias, génération des miniatures, analyse Sonic), surtout pour des bibliothèques de plusieurs Téraoctets.
* **Objectif :** Créer un workflow DevOps de type **"Cloud Bursting"** pour déléguer uniquement les tâches d'indexation intensives à une instance cloud Scaleway puissante et éphémère. Une fois le travail terminé, la base de données et les métadonnées de Plex sont rapatriées sur le ZimaBoard, qui reste le serveur de streaming principal.


### 🏗️ **Architecture Technique**

```
ZimaBoard (Local)          Scaleway (Cloud)
    │                           │
    ├── Médias sur S3 ─────────>├── Instance temporaire
    │                           ├── Mount S3 via rclone
    │                           ├── Docker Plex
    │                           ├── Scan/Analyse
    │<── Archive DB/Metadata ────┤
    └── Plex de streaming       └── Destruction
```

L'architecture hybride repose sur 4 piliers :
1.  **Serveur Local (ZimaBoard) :** Point d'entrée pour le streaming Plex. Point de départ et d'arrivée du workflow automatisé.
2.  **Instance Cloud Éphémère (Scaleway) :** Machine puissante (ex: GP1-S) provisionnée à la demande, responsable de tout le travail de calcul. Détruite en fin de processus pour maîtriser les coûts.
3.  **Stockage Média (Bucket S3) :** Le stockage centralisé des fichiers médias, accessible à la fois par l'instance cloud (via `rclone mount`) et par le ZimaBoard.
4.  **Orchestrateur (`automate_scan.py`) :** Le script Python qui pilote l'ensemble du processus de bout en bout, de la création de l'instance à sa destruction.

### 📂 **Structure du Projet**

```
cloud-bursting/
├── automate_scan.py          # Script principal d'orchestration (scan from scratch)
├── automate_delta_sync.py    # ⭐ Delta sync cloud (injection DB existante)
├── test_scan_local.py        # Tests locaux (base vierge)
├── test_delta_sync.py        # Tests locaux (injection DB)
├── common/
│   ├── config.py             # Configuration & environnement
│   ├── executor.py           # Exécution SSH + état
│   ├── scaleway.py           # Infrastructure cloud Scaleway
│   ├── local.py              # Tests locaux
│   ├── plex_setup.py         # Setup & configuration Plex
│   ├── plex_scan.py          # Scan & analyse
│   ├── delta_sync.py         # Synchronisation incrémentale
│   └── tee_logger.py         # Capture terminal output
├── logs/                     # Archives de logs
├── plex_libraries.json       # Configuration des bibliothèques
├── rclone.conf              # Configuration S3
├── .env                     # Variables d'environnement
└── setup_instance.sh        # Cloud-init pour l'instance
```

### 📐 **Conventions de Code**

Pour garantir la cohérence et la maintenabilité du projet, tous les scripts Python suivent un template standardisé.

#### **Structure de fichier**

```python
#!/usr/bin/env python3
"""
<nom_script>.py - <Description courte>

<Paragraphe explicatif du workflow>

Objectif: <Énoncé clair de l'objectif>

Prérequis:
- <Dépendance 1>
- <Dépendance 2>

Usage:
    # <Cas d'usage 1>
    python <script>.py --option1 value

    # <Cas d'usage 2>
    python <script>.py --option2
"""

# === IMPORTS ===
import <stdlib> (ordre alphabétique)

# Imports modules common
from common.<module> import (
    <fonction1>,
    <fonction2>
)

# === CONFIGURATION ===
# Description du contexte
CONSTANTE_1 = valeur
CONSTANTE_2 = valeur

# ============================================================================
# MAIN
# ============================================================================

def main():
    """Point d'entrée principal du script."""

    # === ARGUMENTS CLI ===
    parser = argparse.ArgumentParser(...)
    args = parser.parse_args()

    # === CONFIGURATION ===
    # Chargement environnement

    print("=" * 60)
    print("<TITRE WORKFLOW>")
    print("=" * 60)
    print("=" * 60)

    try:
        # === PHASE 1: <NOM> ===
        print("\n" + "=" * 60)
        print("PHASE 1: <NOM>")
        print("=" * 60)

        # Logique de la phase...

    except KeyboardInterrupt:
        print("\n\n⚠️  <Action> interrompu(e) par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # === DIAGNOSTIC POST-MORTEM ===
        print("\n" + "=" * 60)
        print("🔍 DIAGNOSTIC POST-MORTEM")
        print("=" * 60)

        # Nettoyage...

if __name__ == "__main__":
    main()
```

#### **Règles de style**

**1. Séparateurs de sections**
- Section majeure (MAIN, IMPORTS, etc.) : `# ============================================================================` (80 caractères)
- Sous-section dans main() : `# === NOM SECTION ===`
- Affichage utilisateur : `print("=" * 60)` pour les phases

**2. Organisation des imports**
- Bibliothèque standard en premier (ordre alphabétique)
- Modules `common.*` en second avec parenthèses multi-lignes si >3 imports
- Commentaire `# === IMPORTS ===` et `# Imports modules common`

**3. Phases numérotées**
- Format : `PHASE X: NOM` où X est un numéro séquentiel
- La numérotation respecte la logique propre à chaque script :
  - `test_delta_sync.py` (8 phases) : PRÉPARATION → INJECTION DB → MONTAGE S3 → DÉMARRAGE PLEX → VÉRIFICATION → TRAITEMENT MUSIQUE → VALIDATION AUTRES → EXPORT
  - `test_scan_local.py` (8 phases) : PRÉPARATION → MONTAGE S3 → PRÉPARATION VOLUMES → DÉMARRAGE PLEX → CONFIGURATION BIBLIOTHÈQUES → TRAITEMENT MUSIQUE → VALIDATION AUTRES → EXPORT
  - `automate_delta_sync.py` (10 phases) : CRÉATION INSTANCE → ATTENTE → CONFIGURATION → INJECTION DB → MONTAGE S3 → DÉMARRAGE PLEX → VÉRIFICATION → TRAITEMENT MUSIQUE → VALIDATION AUTRES → EXPORT
  - `automate_scan.py` (10 phases) : CRÉATION INSTANCE → ATTENTE → CONFIGURATION → DÉMARRAGE PLEX → CONFIGURATION BIBLIOTHÈQUES → SCAN → ANALYSE → EXPORT

**4. Messages utilisateur**
- Emojis autorisés uniquement pour les états : ✅ ❌ ⚠️ 🔍 📦 ⏳ 🔄 🔑 📊 🚨 🎹 📋 💾 📂 📤 🔬 🎵 👋
- Format : `<emoji> <Message>` (pas d'emoji au milieu de phrase)
- Ponctuation cohérente : pas d'exclamation excessive, préférer le point

**5. Gestion des erreurs**
- Bloc `try/except/finally` systématique dans `main()`
- Section `DIAGNOSTIC POST-MORTEM` dans le `finally`
- Messages d'erreur courts et informatifs

**6. Documentation**
- Docstring de module obligatoire avec description, objectif, prérequis, usage
- Docstring de fonction pour `main()` : `"""Point d'entrée principal du script."""`
- Commentaires inline pour clarifier la logique complexe uniquement

## Conventions
- Commits: conventional commits, English
- Code: ruff for linting, black for formatting

## Context
When relevant, read:
- Current work: `.claude/context/status.md`
- Past mistakes: `.claude/context/anti-patterns.md`
- Technical decisions: `.claude/context/decisions.md`

## End of session
Run `/retro` before stopping to update context files.
