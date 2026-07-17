"""
Estadísticas internas de uso: visitas (conexiones) por estación y errores
de conexión.

Cada vez que un usuario se conecta a una estación (selector, mapa, ranking,
deep link o autoconexión) el frontend registra una visita vía
``POST /v1/stats/visit``. Si la conexión falla, registra el error vía
``POST /v1/stats/error`` con la categoría (timeout, unauthorized, network…).
El panel interno (credenciales especiales en el formulario WU) las consulta
agregadas por ventanas temporales.

Persistencia: sqlite propio, separado del catálogo. Ruta:
``METEOLABX_USAGE_STATS_PATH`` > ``$RAILWAY_VOLUME_MOUNT_PATH/
usage_stats.sqlite`` (sobrevive redeploys) > ``data/usage_stats.sqlite``
(dev local, gitignored).
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS station_visits (
    visit_pk INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    station_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    epoch INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visits_station ON station_visits(provider, station_id);
CREATE INDEX IF NOT EXISTS idx_visits_epoch ON station_visits(epoch);
-- Tabla añadida en v1.2.8. Solo CREATE IF NOT EXISTS: al desplegar sobre una
-- base existente se crea la tabla nueva sin tocar station_visits.
CREATE TABLE IF NOT EXISTS station_errors (
    error_pk INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    station_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    error_kind TEXT NOT NULL,
    status_code INTEGER,
    epoch INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_errors_station ON station_errors(provider, station_id);
CREATE INDEX IF NOT EXISTS idx_errors_epoch ON station_errors(epoch);
"""

# Ventanas del panel (etiqueta → segundos). "total" va aparte.
WINDOWS = {
    "d1": 24 * 3600,
    "d7": 7 * 24 * 3600,
    "d30": 30 * 24 * 3600,
}


def db_path(settings=None) -> Path:
    configured = str(getattr(settings, "usage_stats_path", "") or "").strip()
    if configured:
        return Path(configured)
    volume = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if volume:
        return Path(volume) / "usage_stats.sqlite"
    return _ROOT / "data" / "usage_stats.sqlite"


def _connect(settings=None) -> sqlite3.Connection:
    path = db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(_SCHEMA)
    return connection


def record_visit(provider: str, station_id: str, name: str = "", *, settings=None) -> None:
    provider = str(provider or "").strip().upper()
    station_id = str(station_id or "").strip()
    if not provider or not station_id:
        return
    with _connect(settings) as connection:
        connection.execute(
            "INSERT INTO station_visits(provider, station_id, name, epoch) VALUES (?, ?, ?, ?)",
            (provider, station_id, str(name or "").strip()[:120], int(time.time())),
        )


def record_error(
    provider: str,
    station_id: str,
    name: str = "",
    *,
    error_kind: str,
    status_code: Optional[int] = None,
    settings=None,
) -> None:
    provider = str(provider or "").strip().upper()
    station_id = str(station_id or "").strip()
    error_kind = str(error_kind or "").strip().lower()[:40]
    if not provider or not station_id or not error_kind:
        return
    try:
        status = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status = None
    with _connect(settings) as connection:
        connection.execute(
            "INSERT INTO station_errors(provider, station_id, name, error_kind, status_code, epoch)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (provider, station_id, str(name or "").strip()[:120], error_kind, status, int(time.time())),
        )


def visit_summary(*, settings=None, limit: int = 500) -> Dict[str, Any]:
    """Visitas y errores agregados por estación en cada ventana + totales."""
    now = int(time.time())
    with _connect(settings) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT provider, station_id,
                   -- nombre más reciente registrado (las estaciones se renombran)
                   (SELECT v2.name FROM station_visits v2
                    WHERE v2.provider = v.provider AND v2.station_id = v.station_id
                      AND v2.name <> '' ORDER BY v2.epoch DESC LIMIT 1) AS name,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d1,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30,
                   MAX(epoch) AS last_epoch
            FROM station_visits v
            GROUP BY provider, station_id
            ORDER BY total DESC, last_epoch DESC
            LIMIT {int(limit)}
            """,
            (now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"]),
        ).fetchall()
        totals_row = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d1,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30,
                   COUNT(DISTINCT provider || '|' || station_id) AS stations
            FROM station_visits
            """,
            (now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"]),
        ).fetchone()
        error_rows = connection.execute(
            """
            SELECT provider, station_id,
                   (SELECT e2.name FROM station_errors e2
                    WHERE e2.provider = e.provider AND e2.station_id = e.station_id
                      AND e2.name <> '' ORDER BY e2.epoch DESC LIMIT 1) AS name,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d1,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30,
                   MAX(epoch) AS last_epoch,
                   (SELECT e3.error_kind FROM station_errors e3
                    WHERE e3.provider = e.provider AND e3.station_id = e.station_id
                    ORDER BY e3.epoch DESC LIMIT 1) AS last_kind
            FROM station_errors e
            GROUP BY provider, station_id
            """,
            (now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"]),
        ).fetchall()
        error_totals_row = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d1,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30
            FROM station_errors
            """,
            (now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"]),
        ).fetchone()
        error_kind_rows = connection.execute(
            """
            SELECT error_kind,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30
            FROM station_errors
            GROUP BY error_kind
            ORDER BY total DESC
            """,
            (now - WINDOWS["d30"],),
        ).fetchall()

    empty_errors = {"d1": 0, "d7": 0, "d30": 0, "total": 0, "last_epoch": 0, "last_kind": ""}
    stations_by_key: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        stations_by_key[(row["provider"], row["station_id"])] = {
            "provider": row["provider"],
            "station_id": row["station_id"],
            "name": row["name"] or row["station_id"],
            "d1": int(row["d1"] or 0),
            "d7": int(row["d7"] or 0),
            "d30": int(row["d30"] or 0),
            "total": int(row["total"] or 0),
            "last_epoch": int(row["last_epoch"] or 0),
            "errors": dict(empty_errors),
        }
    for row in error_rows:
        key = (row["provider"], row["station_id"])
        station = stations_by_key.get(key)
        if station is None:
            # Estación que solo tiene errores (nunca conectó con éxito):
            # también interesa verla en el panel.
            station = stations_by_key[key] = {
                "provider": row["provider"],
                "station_id": row["station_id"],
                "name": row["name"] or row["station_id"],
                "d1": 0,
                "d7": 0,
                "d30": 0,
                "total": 0,
                "last_epoch": 0,
                "errors": dict(empty_errors),
            }
        station["errors"] = {
            "d1": int(row["d1"] or 0),
            "d7": int(row["d7"] or 0),
            "d30": int(row["d30"] or 0),
            "total": int(row["total"] or 0),
            "last_epoch": int(row["last_epoch"] or 0),
            "last_kind": str(row["last_kind"] or ""),
        }

    stations: List[Dict[str, Any]] = sorted(
        stations_by_key.values(),
        key=lambda s: (s["total"], s["last_epoch"]),
        reverse=True,
    )[: int(limit)]
    return {
        "stations": stations,
        "totals": {
            "d1": int(totals_row["d1"] or 0),
            "d7": int(totals_row["d7"] or 0),
            "d30": int(totals_row["d30"] or 0),
            "total": int(totals_row["total"] or 0),
            "stations": int(totals_row["stations"] or 0),
            "errors": {
                "d1": int(error_totals_row["d1"] or 0),
                "d7": int(error_totals_row["d7"] or 0),
                "d30": int(error_totals_row["d30"] or 0),
                "total": int(error_totals_row["total"] or 0),
            },
        },
        "error_kinds": [
            {
                "kind": row["error_kind"],
                "d30": int(row["d30"] or 0),
                "total": int(row["total"] or 0),
            }
            for row in error_kind_rows
        ],
    }
