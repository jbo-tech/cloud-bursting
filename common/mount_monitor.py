#!/usr/bin/env python3
"""
Monitoring continu du montage rclone pendant les phases longues.

Ce module fournit un thread daemon qui surveille la santé du montage S3 via rclone
et tente un remontage automatique en cas de défaillance détectée.
"""
import threading
import time
from datetime import datetime
from .plex_setup import verify_rclone_mount_healthy, remount_s3_if_needed


class MountHealthMonitor:
    """
    Thread daemon surveillant le montage rclone pendant les phases longues.

    Caractéristiques:
    - Vérifie la santé du montage à intervalles réguliers (défaut: 2 min)
    - Tente un remontage automatique en cas de défaillance
    - Fournit une fonction injectable dans les wait loops (health_check_fn)
    - Thread daemon: s'arrête automatiquement si le programme principal se termine
    - Lock partagé pour éviter les race conditions avec ensure_mount_healthy()

    Usage:
        monitor = MountHealthMonitor(
            ip='localhost',
            mount_point='/mnt/s3-media',
            rclone_remote='bucket-name',
            profile='lite',
            cache_dir='/tmp/rclone-cache',
            log_file='/var/log/rclone.log'
        )

        try:
            monitor.start()
            # Phase longue (Sonic, scan, etc.)
            sonic_result = wait_sonic_complete(
                ...,
                health_check_fn=monitor.get_health_check_fn()
            )
        finally:
            monitor.stop()
    """

    # Lock global partagé pour éviter les remontages concurrents
    _global_remount_lock = threading.Lock()

    def __init__(self, ip, mount_point, rclone_remote, profile,
                 cache_dir, log_file, check_interval=120, remount_retries=3):
        """
        Initialise le moniteur de santé.

        Args:
            ip: 'localhost' ou IP remote
            mount_point: Point de montage à surveiller
            rclone_remote: Nom du bucket/chemin S3 (pour remontage)
            profile: Profil rclone (lite, balanced, performance)
            cache_dir: Répertoire de cache rclone
            log_file: Fichier de logs rclone
            check_interval: Intervalle entre vérifications en secondes (défaut: 120)
            remount_retries: Nombre max de tentatives de remontage (défaut: 3)
        """
        self.ip = ip
        self.mount_point = mount_point
        self.rclone_remote = rclone_remote
        self.profile = profile
        self.cache_dir = cache_dir
        self.log_file = log_file
        self.check_interval = check_interval
        self.remount_retries = remount_retries

        # État interne
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        self._last_health = {'healthy': True, 'error': None, 'response_time': 0}
        self._last_check_time = None

        # Statistiques
        self._stats = {
            'checks_total': 0,
            'checks_failed': 0,
            'remounts_attempted': 0,
            'remounts_successful': 0,
            'start_time': None,
            'stop_time': None
        }

    @classmethod
    def get_global_lock(cls):
        """Retourne le lock global pour synchroniser les remontages."""
        return cls._global_remount_lock

    def start(self):
        """Démarre le thread de monitoring en arrière-plan."""
        if self._running:
            print("   [MountMonitor] Déjà en cours d'exécution")
            return

        self._running = True
        self._stats['start_time'] = datetime.now()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print(f"   [MountMonitor] Démarré (check toutes les {self.check_interval}s)")

    def stop(self):
        """Arrête le monitoring et affiche les statistiques."""
        if not self._running:
            return

        self._running = False
        self._stats['stop_time'] = datetime.now()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        self._print_stats()

    def _monitor_loop(self):
        """Boucle principale du monitoring (exécutée dans un thread)."""
        while self._running:
            try:
                self._perform_health_check()
            except Exception as e:
                print(f"   [MountMonitor] Erreur: {e}")

            # Attendre l'intervalle (mais vérifier _running régulièrement)
            for _ in range(int(self.check_interval)):
                if not self._running:
                    break
                time.sleep(1)

    def _perform_health_check(self):
        """Effectue une vérification de santé et remonte si nécessaire."""
        with self._lock:
            self._stats['checks_total'] += 1
            self._last_check_time = datetime.now()

            # Vérification du montage (test simple sans lecture fichier pour le monitoring)
            health = verify_rclone_mount_healthy(
                self.ip,
                self.mount_point,
                timeout=30
            )

            self._last_health = health

            if health['healthy']:
                # Tout va bien - pas de log pour ne pas polluer
                return

            # Problème détecté - essayer d'acquérir le lock global avant remontage
            self._stats['checks_failed'] += 1

            # Essayer d'acquérir le lock sans bloquer
            if not self._global_remount_lock.acquire(blocking=False):
                # Un autre thread fait déjà un remontage, on attend
                print(f"\n   [MountMonitor] ⏳ Remontage en cours par un autre processus, attente...")
                return

            try:
                print(f"\n   [MountMonitor] ⚠️  Problème détecté: {health['error']}")
                print(f"   [MountMonitor] Tentative de remontage automatique...")

                # Tentative de remontage (skip_lock=True car on a déjà le lock)
                self._stats['remounts_attempted'] += 1
                success = remount_s3_if_needed(
                    self.ip,
                    self.rclone_remote,
                    profile=self.profile,
                    mount_point=self.mount_point,
                    cache_dir=self.cache_dir,
                    log_file=self.log_file,
                    max_retries=self.remount_retries,
                    skip_lock=True
                )

                if success:
                    self._stats['remounts_successful'] += 1
                    print(f"   [MountMonitor] ✅ Remontage réussi")
                    # Mettre à jour l'état après remontage
                    self._last_health = {'healthy': True, 'error': None, 'response_time': 0}
                else:
                    print(f"   [MountMonitor] ❌ Échec du remontage")
            finally:
                self._global_remount_lock.release()

    def get_health_check_fn(self):
        """
        Retourne une fonction injectable dans les wait loops.

        La fonction retournée renvoie le dernier état de santé connu,
        sans effectuer de nouvelle vérification (pour éviter les doublons).

        Returns:
            Callable: Fonction retournant {'healthy': bool, 'error': str|None}
        """
        def check_fn():
            with self._lock:
                return self._last_health.copy()

        return check_fn

    def get_last_health(self):
        """Retourne le dernier état de santé connu."""
        with self._lock:
            return self._last_health.copy()

    def get_stats(self):
        """Retourne les statistiques de monitoring."""
        with self._lock:
            return self._stats.copy()

    def _print_stats(self):
        """Affiche les statistiques de monitoring."""
        stats = self.get_stats()

        duration = None
        if stats['start_time'] and stats['stop_time']:
            duration = (stats['stop_time'] - stats['start_time']).total_seconds() / 60

        print(f"\n   [MountMonitor] Statistiques:")
        print(f"      Vérifications : {stats['checks_total']}")
        if stats['checks_failed'] > 0:
            print(f"      Échecs détectés: {stats['checks_failed']}")
            print(f"      Remontages     : {stats['remounts_successful']}/{stats['remounts_attempted']}")
        else:
            print(f"      Aucun problème détecté")
        if duration:
            print(f"      Durée          : {duration:.1f} min")
