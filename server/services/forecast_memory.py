"""Lectura común de memoria para la admisión de AROME y ECMWF."""
from pathlib import Path


def cgroup_memory(declared_limit=0, *, path_factory=Path, anonymous_reader=None):
    """(Memoria no recuperable, límite), o None si no se puede medir.

    En v2 se excluye la caché recuperable de ficheros. En v1, sin ese
    desglose, se usa conservadoramente el consumo total.
    """
    for current_name, limit_name in (
        ('memory.current', 'memory.max'),
        ('memory/memory.usage_in_bytes', 'memory/memory.limit_in_bytes'),
    ):
        try:
            current = int(path_factory('/sys/fs/cgroup/' + current_name).read_text().strip())
            raw = path_factory('/sys/fs/cgroup/' + limit_name).read_text().strip()
            limit = declared_limit if raw == 'max' else int(raw)
            if limit <= 0:
                continue
            if anonymous_reader is not None:
                anonymous = anonymous_reader()
            else:
                anonymous = None
                try:
                    for line in path_factory('/sys/fs/cgroup/memory.stat').read_text().splitlines():
                        key, _, value = line.partition(' ')
                        if key == 'anon':
                            anonymous = int(value)
                            break
                except (OSError, ValueError):
                    pass
            return (current if anonymous is None else anonymous), limit
        except (OSError, ValueError):
            continue
    return None
