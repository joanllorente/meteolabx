"""
Estadísticas internas de uso: visitas (conexiones) por estación.

Cada vez que un usuario se conecta a una estación (selector, mapa, ranking,
deep link o autoconexión) el frontend registra una visita vía
``POST /v1/stats/visit``. El panel interno (credenciales especiales en el
formulario WU) las consulta agregadas por ventanas temporales.

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


def visit_summary(*, settings=None, limit: int = 500) -> Dict[str, Any]:
    """Visitas agregadas por estación en cada ventana + totales globales."""
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

    stations: List[Dict[str, Any]] = [
        {
            "provider": row["provider"],
            "station_id": row["station_id"],
            "name": row["name"] or row["station_id"],
            "d1": int(row["d1"] or 0),
            "d7": int(row["d7"] or 0),
            "d30": int(row["d30"] or 0),
            "total": int(row["total"] or 0),
            "last_epoch": int(row["last_epoch"] or 0),
        }
        for row in rows
    ]
    return {
        "stations": stations,
        "totals": {
            "d1": int(totals_row["d1"] or 0),
            "d7": int(totals_row["d7"] or 0),
            "d30": int(totals_row["d30"] or 0),
            "total": int(totals_row["total"] or 0),
            "stations": int(totals_row["stations"] or 0),
        },
    }
