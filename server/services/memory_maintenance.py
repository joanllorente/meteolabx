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


_MB = 1024 * 1024


def _process_label(cmdline: str) -> str:
    """Nombre corto de un proceso del contenedor, a partir de su línea de órdenes.

    Los trabajos aislados del worker arrancan con «spawn», así que su línea es
    la de multiprocessing y no dice de quién son: se reconocen por eso.
    """
    if 'multiprocessing' in cmdline and 'spawn_main' in cmdline:
        return 'trabajo'
    if 'forecast_worker' in cmdline:
        return 'worker'
    if 'uvicorn' in cmdline:
        return 'api'
    return 'otros'


def container_memory(proc_root=Path('/proc'), cgroup_root=Path('/sys/fs/cgroup')):
    """Reparto de la memoria de todo el contenedor, no solo de este proceso.

    El mantenimiento corre dentro de la API y hasta ahora solo medía la API.
    La meseta entre pasadas puede estar en el proceso padre del worker, que
    nunca recortaba su montón, o en la caché de páginas de los ficheros, que no
    es de ningún proceso pero cuenta en el total del contenedor. Sin separarlo
    no se sabe qué conviene recortar.
    """
    procesos = {}
    try:
        entradas = [p for p in proc_root.iterdir() if p.name.isdigit()]
    except OSError:
        entradas = []
    for entrada in entradas:
        try:
            cmdline = (entrada / 'cmdline').read_bytes().replace(b'\0', b' ').decode(
                'utf-8', 'replace')
            anon = None
            for line in (entrada / 'status').read_text().splitlines():
                if line.startswith('RssAnon:'):
                    anon = int(line.split()[1]) * 1024
                    break
        except (OSError, ValueError):
            continue
        # Los hilos del núcleo no tienen línea de órdenes ni memoria propia.
        if not cmdline.strip() or anon is None:
            continue
        grupo = procesos.setdefault(_process_label(cmdline), {'count': 0, 'anon_mb': 0.0})
        grupo['count'] += 1
        grupo['anon_mb'] = round(grupo['anon_mb'] + anon / _MB, 1)

    cgroup = {}
    try:
        cgroup['current_mb'] = round(int((cgroup_root / 'memory.current').read_text()) / _MB, 1)
    except (OSError, ValueError):
        pass
    try:
        for line in (cgroup_root / 'memory.stat').read_text().splitlines():
            campo, _, valor = line.partition(' ')
            if campo in ('anon', 'file', 'shmem', 'active_file', 'inactive_file'):
                cgroup[f'{campo}_mb'] = round(int(valor) / _MB, 1)
    except (OSError, ValueError):
        pass
    if not procesos and not cgroup:
        return None
    return {'processes': procesos, 'cgroup': cgroup}


def describe_container_memory(reparto):
    """Una línea legible del reparto, para el log de producción."""
    if not reparto:
        return 'sin datos del contenedor'
    partes = []
    cg = reparto.get('cgroup') or {}
    if 'current_mb' in cg:
        partes.append(f"total {cg['current_mb'] / 1024:.2f} GB")
    if 'anon_mb' in cg:
        partes.append(f"anónima {cg['anon_mb'] / 1024:.2f} GB")
    if 'file_mb' in cg:
        activa = (f" (activa {cg['active_file_mb'] / 1024:.2f})"
                  if 'active_file_mb' in cg else '')
        partes.append(f"caché de ficheros {cg['file_mb'] / 1024:.2f} GB{activa}")
    if cg.get('shmem_mb'):
        partes.append(f"shmem {cg['shmem_mb'] / 1024:.2f} GB")
    for nombre, grupo in sorted((reparto.get('processes') or {}).items(),
                                key=lambda item: -item[1]['anon_mb']):
        veces = f"×{grupo['count']}" if grupo['count'] > 1 else ''
        partes.append(f"{nombre}{veces} {grupo['anon_mb'] / 1024:.2f} GB")
    return ' · '.join(partes)


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
    # Aparte, y después del recorte: lo que queda es la meseta de verdad.
    reparto = await asyncio.to_thread(container_memory)
    result['container'] = reparto
    logger.info('Memoria del contenedor: %s', describe_container_memory(reparto))
    return result


async def maintenance_loop(interval_s=900):
    """Primera limpieza tras 15 minutos; se cancela con el lifespan."""
    while True:
        await asyncio.sleep(max(60, interval_s))
        try:
            await maintain_once()
        except Exception:
            logger.exception('Falló el mantenimiento de memoria; se reintentará')
