"""Descarta páginas de GRIB y mapas de las pasadas completas.

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
    store = get_forecast_store()
    manifests = [m for m in retained_manifests(store)
                 if not (m.get("progress") or {}).get("active_jobs")]
    # Basta con que la pasada del fichero esté completa. Exigirlo de todas las
    # conservadas dejaba que una vieja atascada en «publishing» bloqueara la
    # liberación para siempre: el 22/09/2026 fueron 7,2 GB de caché de páginas
    # durante todo el día, que Railway factura.
    completas = {str(m['run']).replace('-', '').replace(':', '')[:11]
                 for m in manifests if m.get('status') == 'complete'}
    if not completas:
        return {'skipped': 'unfinished_runs'}
    if any(t.name.startswith('arome-prefetch') and t.is_alive()
           for t in threading.enumerate()):
        return {'skipped': 'prefetch_active'}
    root = Path(os.getenv('METEOLABX_AROME_PACKAGE_CACHE_DIR') or
                str(Path(tempfile.gettempdir()) / 'meteolabx-arome-packages'))
    # Los paquetes tienen nombres IP1-YYYYMMDDTHH-00H06H.grib2. Los de una
    # pasada aún en curso se dejan en paz: alguien los está leyendo.
    stamps = completas
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
    maps, map_bytes = _release_completed_map_cache(store, manifests)
    result = {'files_advised': count + maps, 'file_bytes': total + map_bytes}
    if maps:
        result.update(map_files_advised=maps, map_file_bytes=map_bytes)
    return result


def _release_completed_map_cache(store, manifests):
    from server.services.forecast_store import LocalObjectStore, frame_key

    # Remote object stores do not expose local files to advise.
    if not isinstance(store, LocalObjectStore):
        return 0, 0
    count = total = 0
    for manifest in manifests:
        if manifest.get('status') != 'complete':
            continue
        run = str(manifest['run'])
        # Derive the run directory with the same scope/revision rules as writers.
        key = frame_key(run, 'temperature-2m', run,
                        scope=str(manifest.get('calculation_scope', 'model')))
        root = store._path(key).parent.parent
        for path in root.rglob('*.grid.gz'):
            try:
                with path.open('rb') as stream:
                    os.fsync(stream.fileno())
                    os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                    total += os.fstat(stream.fileno()).st_size
                    count += 1
            except OSError as exc:
                logger.warning('No se pudo liberar caché del mapa %s: %s', path.name, exc)
    if count:
        logger.debug('Solicitada liberación de páginas de %d mapas (%.2f GB en disco); '
                     'archivos conservados. No equivale a RAM liberada.', count, total / 1e9)
    return count, total
