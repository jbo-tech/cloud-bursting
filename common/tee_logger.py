#!/usr/bin/env python3
"""
tee_logger.py - Capture stdout/stderr vers fichier en temps réel

Ce module fournit une classe TeeLogger qui permet de capturer toute
la sortie terminal (print statements) vers un fichier log, tout en
continuant à afficher les messages à l'écran.

Objectif: Permettre l'export de l'output terminal pour debug/archivage

Usage:
    # Mode context manager (recommandé)
    with TeeLogger() as logger:
        print("Ce message va au terminal ET au fichier log")

    # Mode manuel
    logger = TeeLogger()
    logger.start()
    print("Ce message va au terminal ET au fichier log")
    logger.stop()
"""

# === IMPORTS ===
import os
import sys
from datetime import datetime


# ============================================================================
# TEE LOGGER
# ============================================================================

class TeeLogger:
    """Capture stdout/stderr vers fichier tout en affichant en temps réel."""

    def __init__(self, log_dir: str = "logs", timestamp: str = None):
        """
        Initialise le TeeLogger.

        Args:
            log_dir: Répertoire de destination des logs (créé si inexistant)
            timestamp: Horodatage à utiliser (format YYYYMMDD_HHMMSS), généré si non fourni
        """
        os.makedirs(log_dir, exist_ok=True)
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(log_dir, f"terminal_{timestamp}.log")
        self.log_file = None
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        self._started = False

    def write(self, message):
        """Écrit le message au terminal ET au fichier log."""
        self.original_stdout.write(message)
        if self.log_file:
            self.log_file.write(message)
            self.log_file.flush()  # Flush immédiat pour crash recovery

    def flush(self):
        """Flush les buffers (requis pour compatibilité avec print())."""
        self.original_stdout.flush()
        if self.log_file:
            self.log_file.flush()

    def start(self):
        """Active la capture stdout/stderr."""
        if self._started:
            return

        # Ouvrir le fichier en mode line-buffered
        self.log_file = open(self.log_path, 'w', encoding='utf-8', buffering=1)

        # Écrire header
        self.log_file.write(f"# Terminal log started at {datetime.now().isoformat()}\n")
        self.log_file.write(f"# Working directory: {os.getcwd()}\n")
        self.log_file.write("=" * 60 + "\n\n")
        self.log_file.flush()

        # Rediriger stdout/stderr
        sys.stdout = self
        sys.stderr = self
        self._started = True

    def stop(self):
        """Désactive la capture et ferme le fichier."""
        if not self._started:
            return

        # Restaurer stdout/stderr
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr

        # Écrire footer et fermer
        if self.log_file:
            self.log_file.write("\n" + "=" * 60 + "\n")
            self.log_file.write(f"# Terminal log ended at {datetime.now().isoformat()}\n")
            self.log_file.close()
            self.log_file = None

        self._started = False

    def __enter__(self):
        """Support context manager."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Fermeture automatique en sortie de context."""
        self.stop()
        return False  # Ne pas supprimer les exceptions

    def get_log_path(self) -> str:
        """Retourne le chemin du fichier log."""
        return self.log_path
