"""Descarta páginas GRIB limpias tras completar todas las pasadas retenidas.

Conserva los archivos. No modifica límites ni vacía la caché global del kernel.
"""
from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
import tempfile
import threading

logger = logging.getLogger(__name__)


def release_completed_grib_cache() -> dict:
    from server.services.forecast_store import get_forecast_store, retained_manifests

    if not hasattr(os, 'posix_fadvise') or not hasattr(os, 'POSIX_FADV_DONTNEED'):
        return {'skipped': 'unsupported'}
    manifests = retained_manifests(get_forecast_store())
    if not manifests or any(m.get('status') != 'complete' for m in manifests):
        return {'skipped': 'unfinished_runs'}
    if any(t.name.startswith('arome-prefetch') and t.is_alive()
           for t in threading.enumerate()):
        return {'skipped': 'prefetch_active'}
    root = Path(os.getenv('METEOLABX_AROME_PACKAGE_CACHE_DIR') or
                str(Path(tempfile.gettempdir()) / 'meteolabx-arome-packages'))
    stamps = {str(m['run']).replace('-', '').replace(':', '')[:11] for m in manifests}
    # Los paquetes tienen nombres IP1-YYYYMMDDTHH-00H06H.grib2.
    count = total = 0
    for path in root.glob('*.grib2'):
        parts = path.stem.split('-')
        if len(parts) != 3 or parts[1] not in stamps:
            continue
        try:
            with path.with_suffix('.lock').open('a+') as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with path.open('rb') as stream:
                    # DONTNEED no garantiza descartar páginas sucias. Sincronizar
                    # solo este archivo, nunca todo el volumen.
                    os.fsync(stream.fileno())
                    os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                    total += os.fstat(stream.fileno()).st_size
                    count += 1
        except OSError as exc:
            logger.warning('No se pudo liberar caché GRIB %s: %s', path.name, exc)
    if count:
        # En modo --watch esta limpieza preventiva se repite cada minuto. Es
        # telemetría útil al diagnosticar memoria, pero no un evento operativo.
        logger.debug('Solicitada liberación de caché de %d GRIB (%.2f GB en disco); archivos conservados.', count, total / 1e9)
    return {'files_advised': count, 'file_bytes': total}
