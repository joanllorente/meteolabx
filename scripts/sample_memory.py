#!/usr/bin/env python3
"""Muestreo Linux de solo lectura; JSONL persistente, sin tocar páginas GRIB.

python scripts/sample_memory.py --output /data/memory-diagnostics/samples.jsonl
Se detiene tras 24 h por defecto. No purga cachés ni importa la aplicación.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone


def counters(path: Path) -> dict[str, int]:
    result = {}
    for line in path.read_text().splitlines():
        parts = line.replace(':', '').split()
        if len(parts) >= 2:
            try:
                result[parts[0]] = int(parts[1]) * (1024 if parts[-1] == 'kB' else 1)
            except ValueError:
                pass
    return result


def resident_file(path: Path) -> dict:
    """mincore cuenta páginas residentes; mmap no lee ni precarga el archivo."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, ctypes.c_longlong]
    libc.mmap.restype = ctypes.c_void_p
    libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
    libc.mincore.restype = ctypes.c_int
    libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    libc.munmap.restype = ctypes.c_int
    with path.open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        result = {'name': path.name, 'bytes': size, 'resident_bytes': 0}
        if not size:
            return result
        page = os.sysconf('SC_PAGE_SIZE')
        count = (size + page - 1) // page
        address = libc.mmap(None, size, 1, 1, stream.fileno(), 0)
        if address == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_errno(), 'mmap failed')
        try:
            vector = (ctypes.c_ubyte * count)()
            if libc.mincore(address, size, vector) != 0:
                raise OSError(ctypes.get_errno(), 'mincore failed')
            result['resident_bytes'] = sum(
                min(page, size - i * page) for i, flag in enumerate(vector) if flag & 1
            )
        finally:
            libc.munmap(address, size)
        return result


def snapshot(manifest: Path, packages: Path) -> dict:
    result = {'time': datetime.now(timezone.utc).isoformat(), 'sampler_pid': os.getpid(),
              'deployment': os.getenv('RAILWAY_DEPLOYMENT_ID'), 'errors': []}
    for name in ('memory.stat', 'memory.events', 'memory.pressure'):
        try:
            p = Path('/sys/fs/cgroup') / name
            result[name] = counters(p) if name != 'memory.pressure' else p.read_text()
        except OSError as exc:
            result['errors'].append(f'{name}: {exc}')
    for name in ('memory.current', 'memory.max'):
        try:
            result[name] = (Path('/sys/fs/cgroup') / name).read_text().strip()
        except OSError as exc:
            result['errors'].append(f'{name}: {exc}')
    try:
        data = json.loads(manifest.read_text())
        result['forecast'] = {k: data.get(k) for k in ('run', 'status', 'progress')}
    except (OSError, ValueError) as exc:
        result['errors'].append(f'manifest: {exc}')
    result['processes'] = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            args = (p / 'cmdline').read_bytes().split(bytes([0]))
            role = ('backend' if (b'uvicorn' in args or b'scripts.run_uvicorn_dual_stack' in args) else
                    'worker' if b'scripts.forecast_worker' in args else
                    'sampler' if int(p.name) == os.getpid() else 'other')
            result['processes'].append({'pid': int(p.name), 'role': role,
                                       'memory': counters(p / 'smaps_rollup')})
        except OSError:
            pass  # Los hijos pueden terminar durante el muestreo.
    result['grib'] = []
    for p in sorted(packages.glob('*.grib2')):
        try:
            result['grib'].append(resident_file(p))
        except OSError as exc:
            result['errors'].append(f'{p.name}: {exc}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=Path('/data/forecast/forecast/manifests/latest.json'))
    parser.add_argument('--packages', type=Path, default=Path('/data/arome-packages'))
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--hours', type=float, default=24)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.interval < 10 or args.hours <= 0:
        parser.error('interval debe ser >=10 y hours >0')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.hours * 3600
    while time.monotonic() < deadline:
        started = time.monotonic()
        sample = snapshot(args.manifest, args.packages)
        sample['sample_seconds'] = time.monotonic() - started
        with args.output.open('a') as stream:
            stream.write(json.dumps(sample) + '\n')
            stream.flush()
        if args.once:
            break
        time.sleep(min(max(0, deadline - time.monotonic()),
                       max(0, args.interval - (time.monotonic() - started))))


if __name__ == '__main__':
    main()
