"""Mantenimiento acotado de memoria del backend, sin vaciar datos vigentes."""
import asyncio
import ctypes
import gc
import logging
import os
import sys
import time
from pathlib import Path

from server.services.cache import LIVE_CACHES
from server.services.memory_diagnostics import memory_sample

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


def _release_idle_frame_caches():
    """Las cachés de mapas de AROME guardan por número de entradas, no por
    tiempo: en cuanto nadie mira el visor son meseta pagada por minuto."""
    try:
        from server.services.arome_forecast import release_idle_frame_caches
    except Exception:
        logger.debug('No se pudieron vaciar las cachés de mapas', exc_info=True)
        return {}
    return release_idle_frame_caches()


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


_snapshot = None


def growth_by_source(limit=5):
    """Qué líneas de código han pedido más memoria desde la vuelta anterior.

    Solo con METEOLABX_MEMORY_TRACE=1, porque `tracemalloc` encarece cada
    asignación. Las cachés están limitadas a 500 entradas, así que cuando la
    API crece en gigas no es por guardar más cosas sino por guardarlas más
    grandes, y el número de entradas no dice cuáles. Esto sí.
    """
    global _snapshot
    if os.getenv('METEOLABX_MEMORY_TRACE', '').lower() not in ('1', 'true', 'yes'):
        return None
    import tracemalloc
    if not tracemalloc.is_tracing():
        tracemalloc.start(1)
        _snapshot = tracemalloc.take_snapshot()
        return 'trazando desde ahora'
    actual = tracemalloc.take_snapshot()
    previo, _snapshot = _snapshot, actual
    if previo is None:
        return 'trazando desde ahora'
    crecimiento = [linea for linea in actual.compare_to(previo, 'lineno')
                   if linea.size_diff > 0][:limit]
    if not crecimiento:
        return 'sin crecimiento medible'
    return ' · '.join(
        f'{linea.traceback[0].filename.split("/")[-1]}:{linea.traceback[0].lineno} '
        f'+{linea.size_diff / _MB:.0f} MB' for linea in crecimiento)


def untracked_memory(limit=3):
    """Cuánta memoria del proceso NO pasa por el asignador de Python, y dónde
    está la que sí.

    `growth_by_source` compara dos instantáneas y solo ve el crecimiento del
    último cuarto de hora: si la API sube 120 MB cada hora en trozos pequeños,
    esa lista sale plana. Esto mira el total acumulado, que es lo que se paga,
    y lo contrasta con la RSS anónima. La diferencia es orientativa: incluye
    asignaciones anteriores al inicio del trazado, el propio diagnóstico,
    memoria nativa y memoria retenida por los asignadores. No identifica por
    sí sola una fuga ni permite atribuir toda la diferencia a código C.
    """
    if os.getenv('METEOLABX_MEMORY_TRACE', '').lower() not in ('1', 'true', 'yes'):
        return None
    import tracemalloc
    if not tracemalloc.is_tracing():
        return None
    rastreada, pico = tracemalloc.get_traced_memory()
    partes = [f'rastreada {rastreada / _MB:.0f} MB (pico {pico / _MB:.0f})']
    # This is bookkeeping overhead, not part of get_traced_memory(). RSS minus
    # traced bytes also includes pre-tracing allocations, snapshots and allocator
    # retention: it is not evidence of a leak in a C extension.
    overhead = tracemalloc.get_tracemalloc_memory()
    partes.append(f'metadatos de trazado {overhead / _MB:.0f} MB')
    anon = anonymous_bytes()
    if anon is not None:
        partes.append(f'diferencia RSS anónima−rastreada {(anon - rastreada) / _MB:.0f} MB '
                      '(no atribuible directamente a memoria nativa)')
    # maintain_once just captured a snapshot. Reuse it instead of allocating
    # a second full snapshot while the first remains alive.
    snapshot = _snapshot if _snapshot is not None else tracemalloc.take_snapshot()
    mayores = snapshot.statistics('lineno')[:limit]
    if mayores:
        partes.append('las mayores: ' + ', '.join(
            f'{e.traceback[0].filename.split("/")[-1]}:{e.traceback[0].lineno} '
            f'{e.size / _MB:.0f} MB' for e in mayores))
    return ' · '.join(partes)


async def maintain_once():
    started = time.monotonic()
    before = anonymous_bytes()
    native_before = await asyncio.to_thread(memory_sample)
    purged = 0
    for cache in list(LIVE_CACHES):
        purged += await cache.purge_expired()
        await asyncio.sleep(0)
    result_mapas = await asyncio.to_thread(_release_idle_frame_caches)
    # El trabajo nativo se ejecuta fuera del hilo del event loop. La recogida
    # de ciclos puede tomar el GIL brevemente; por eso se limita la frecuencia.
    collected, trimmed = await asyncio.to_thread(collect_and_trim)
    result = dict(purged=purged, maps=result_mapas, collected=collected, trimmed=trimmed,
                  anon_before=before, anon_after=anonymous_bytes(),
                  seconds=round(time.monotonic() - started, 3))
    native_after_trim = await asyncio.to_thread(memory_sample)
    logger.info('Mantenimiento de memoria: %s', result)
    # Aparte, y después del recorte: lo que queda es la meseta de verdad.
    reparto = await asyncio.to_thread(container_memory)
    result['container'] = reparto
    logger.info('Memoria del contenedor: %s', describe_container_memory(reparto))
    crecimiento = await asyncio.to_thread(growth_by_source)
    if crecimiento:
        result['growth'] = crecimiento
        logger.info('Quién pidió la memoria: %s', crecimiento)
    fuera = await asyncio.to_thread(untracked_memory)
    if fuera:
        result['untracked'] = fuera
        logger.info('Memoria que Python no rastrea: %s', fuera)
    native_after_diagnostics = await asyncio.to_thread(memory_sample)
    result['allocator_samples'] = {'before_cleanup': native_before,
        'after_cleanup': native_after_trim, 'after_diagnostics': native_after_diagnostics}
    logger.info('Diagnóstico asignador (MiB; reservas no equivalen a RSS y no se suman '
                'a Python; diferencias incluyen actividad concurrente): %s', result['allocator_samples'])
    return result


async def maintenance_loop(interval_s=900):
    """Primera limpieza tras 15 minutos; se cancela con el lifespan."""
    while True:
        await asyncio.sleep(max(60, interval_s))
        try:
            await maintain_once()
        except Exception:
            logger.exception('Falló el mantenimiento de memoria; se reintentará')
