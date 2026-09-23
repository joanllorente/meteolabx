"""Small allocator/OS samples; no heap walks, object contents or new snapshots.

Allocator totals describe reserved address space, not resident RAM. They overlap
with Python allocations and cannot be added to RSS or tracemalloc totals.
"""
from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

MiB = 1024 ** 2


def parse_malloc_info(xml: bytes) -> dict:
    root = ET.fromstring(xml)
    # Root totals already aggregate heaps. Never sum them with per-heap totals.
    totals = {e.attrib['type']: int(e.attrib['size']) for e in root.findall('total')}
    system = root.find("system[@type='current']")
    if system is None:
        raise ValueError('malloc_info has no current system total')
    arena = int(system.attrib['size'])
    free = totals.get('fast', 0) + totals.get('rest', 0)
    return {
        'arenas': len(root.findall('heap')),
        'arena_reserved_mib': round(arena / MiB, 2),
        'arena_free_bins_mib': round(free / MiB, 2),
        # Includes metadata, allocations serving pymalloc, and thread caches.
        # It is NOT the sum of live application objects.
        'arena_not_in_free_bins_mib': round((arena - free) / MiB, 2),
        'malloc_mmap_mib': round(totals.get('mmap', 0) / MiB, 2),
    }


def allocator_memory() -> dict:
    if sys.platform != 'linux':
        return {'unavailable': 'not_linux'}
    libc = ctypes.CDLL(None)
    if not all(hasattr(libc, name) for name in ('gnu_get_libc_version', 'malloc_info', 'open_memstream')):
        return {'unavailable': 'glibc_malloc_info_not_available'}
    libc.open_memstream.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_size_t)]
    libc.open_memstream.restype = ctypes.c_void_p
    libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
    libc.malloc_info.restype = ctypes.c_int
    libc.fclose.argtypes = [ctypes.c_void_p]
    libc.fclose.restype = ctypes.c_int
    libc.free.argtypes = [ctypes.c_void_p]
    libc.free.restype = None
    buffer, size = ctypes.c_void_p(), ctypes.c_size_t()
    stream = libc.open_memstream(ctypes.byref(buffer), ctypes.byref(size))
    if not stream:
        return {'unavailable': 'open_memstream_failed'}
    try:
        try:
            result = libc.malloc_info(0, stream)
        finally:
            closed = libc.fclose(stream)  # Flushes and publishes buffer/size.
        if result or closed or not buffer.value:
            return {'unavailable': 'malloc_info_failed'}
        if size.value > 8 * MiB:
            return {'unavailable': 'malloc_info_too_large'}
        return parse_malloc_info(ctypes.string_at(buffer, size.value))
    finally:
        if buffer.value:
            libc.free(buffer)


def process_memory(proc=Path('/proc/self')) -> dict:
    result = {}
    try:
        for line in (proc / 'smaps_rollup').read_text().splitlines():
            key, _, value = line.partition(':')
            if key in ('Rss', 'Pss', 'Anonymous', 'Private_Dirty', 'Private_Clean', 'AnonHugePages', 'Swap'):
                result[f'{key}_mib'] = round(int(value.split()[0]) / 1024, 2)
    except (OSError, ValueError):
        result['smaps_unavailable'] = True
    try:
        for line in (proc / 'status').read_text().splitlines():
            if line.startswith('Threads:'):
                result['threads'] = int(line.split()[1])
    except (OSError, ValueError):
        pass
    return result


def memory_sample() -> dict:
    """Read cheap counters even when tracemalloc is disabled."""
    import tracemalloc
    result = {'process': process_memory()}
    try:
        result['glibc'] = allocator_memory()
    except (OSError, ValueError, ET.ParseError):
        result['glibc'] = {'unavailable': 'read_failed'}
    if tracemalloc.is_tracing():
        current, peak = tracemalloc.get_traced_memory()
        result['trace'] = {'current_mib': round(current / MiB, 2),
                           'peak_mib': round(peak / MiB, 2),
                           'metadata_mib': round(tracemalloc.get_tracemalloc_memory() / MiB, 2)}
    return result
