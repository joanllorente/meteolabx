"""
Métricas en memoria del backend.

Registro ligero por proveedor: nº de llamadas reales al upstream
(misses de caché que fetchean), nº de errores y último OK/error con
timestamp. Lo alimentan dos puntos centrales:

- ``AsyncTTLCache.get_or_fetch`` (vía los hooks de este módulo) para
  llamadas y éxitos — solo cuenta fetches reales, no hits de caché.
- El exception handler de ``ProviderError`` en ``server/main.py`` para
  los errores.

Igual que el caché, vive en el proceso: se resetea al reiniciar y no se
comparte entre réplicas. Suficiente para el endpoint de diagnóstico;
si algún día hace falta persistencia → Prometheus/Redis.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional


_lock = threading.Lock()
_providers: Dict[str, Dict[str, Any]] = {}


def _bucket(provider: str) -> Dict[str, Any]:
    key = str(provider or "?").strip().upper() or "?"
    bucket = _providers.get(key)
    if bucket is None:
        bucket = {
            "calls": 0,
            "errors": 0,
            "last_ok_epoch": None,
            "last_error": None,
        }
        _providers[key] = bucket
    return bucket


def record_call(provider: str) -> None:
    """Una llamada real al upstream (miss de caché que fetchea)."""
    with _lock:
        _bucket(provider)["calls"] += 1


def record_success(provider: str) -> None:
    with _lock:
        _bucket(provider)["last_ok_epoch"] = int(time.time())


def record_error(provider: Optional[str], error_code: str, detail: Optional[str]) -> None:
    with _lock:
        bucket = _bucket(provider or "?")
        bucket["errors"] += 1
        bucket["last_error"] = {
            "error_code": str(error_code),
            "detail": str(detail or "")[:200],
            "epoch": int(time.time()),
        }


def snapshot() -> Dict[str, Dict[str, Any]]:
    """Copia del estado por proveedor (para /diagnostics y /health)."""
    with _lock:
        return {
            provider: dict(bucket, last_error=dict(bucket["last_error"]) if bucket["last_error"] else None)
            for provider, bucket in _providers.items()
        }


def reset() -> None:
    """Para tests."""
    with _lock:
        _providers.clear()
