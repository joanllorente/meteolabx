"""Mantenimiento acotado de memoria del backend, sin vaciar datos vigentes."""
import asyncio
import ctypes
import gc
import logging
import sys
import time
from pathlib import Path

from server.services.cache import LIVE_CACHES

logger = logging.getLogger(__name__)


def anonymous_bytes():
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('RssAnon:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def collect_and_trim():
    collected = gc.collect()
    trimmed = None
    if sys.platform == 'linux':
        libc = ctypes.CDLL(None)
        trim = getattr(libc, 'malloc_trim', None)
        if trim is not None:
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            trimmed = trim(0)
    return collected, trimmed


async def maintain_once():
    started = time.monotonic()
    before = anonymous_bytes()
    purged = 0
    for cache in list(LIVE_CACHES):
        purged += await cache.purge_expired()
        await asyncio.sleep(0)
    # El trabajo nativo se ejecuta fuera del hilo del event loop. La recogida
    # de ciclos puede tomar el GIL brevemente; por eso se limita la frecuencia.
    collected, trimmed = await asyncio.to_thread(collect_and_trim)
    result = dict(purged=purged, collected=collected, trimmed=trimmed,
                  anon_before=before, anon_after=anonymous_bytes(),
                  seconds=round(time.monotonic() - started, 3))
    logger.info('Mantenimiento de memoria: %s', result)
    return result


async def maintenance_loop(interval_s=900):
    """Primera limpieza tras 15 minutos; se cancela con el lifespan."""
    while True:
        await asyncio.sleep(max(60, interval_s))
        try:
            await maintain_once()
        except Exception:
            logger.exception('Falló el mantenimiento de memoria; se reintentará')
