"""
Estadísticas internas de uso: visitas (conexiones) por estación, aperturas de
las fichas SEO, aperturas del panel completo desde esas fichas, errores de
conexión y entradas a pestañas/mapas.

Cada vez que un usuario se conecta a una estación (selector, mapa, ranking,
deep link o autoconexión) el frontend registra una visita vía
``POST /v1/stats/visit``. Si la conexión falla, registra el error vía
``POST /v1/stats/error`` con la categoría (timeout, unauthorized, network…).
Las aperturas del HTML de las fichas SEO se registran mediante ``POST
/v1/stats/seo-view`` y sus aperturas del panel mediante ``POST
/v1/stats/panel-click``. Las entradas a secciones se registran mediante ``POST
/v1/stats/section``.
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
    source TEXT NOT NULL DEFAULT 'app',
    -- Idioma en el que se leyó la ficha, añadido en v1.3.5. Las visitas
    -- anteriores lo llevan vacío: no se puede reconstruir.
    language TEXT NOT NULL DEFAULT '',
    browser_languages TEXT NOT NULL DEFAULT '',
    page_request_languages TEXT NOT NULL DEFAULT '',
    language_reason TEXT NOT NULL DEFAULT '',
    request_client TEXT NOT NULL DEFAULT '',
    request_languages TEXT NOT NULL DEFAULT '',
    saved_language TEXT NOT NULL DEFAULT '',
    url_language TEXT NOT NULL DEFAULT '',
    -- Por dónde entró: buscador, enlace externo, navegación interna o
    -- directa. `referrer_domain` guarda solo el dominio que enlazó
    -- (`google.es`), nunca la URL: interesa quién enlaza, no qué se lee.
    entry TEXT NOT NULL DEFAULT '',
    referrer_domain TEXT NOT NULL DEFAULT '',
    -- Móvil, tableta o escritorio. Lo decide el navegador por el tipo de
    -- puntero; aquí no se guarda el «user agent».
    device TEXT NOT NULL DEFAULT '',
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
-- Eventos anónimos de navegación añadidos en v1.3.3. No guardan estación,
-- usuario, IP ni identificador de sesión.
CREATE TABLE IF NOT EXISTS section_visits (
    section_visit_pk INTEGER PRIMARY KEY,
    section TEXT NOT NULL,
    epoch INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_section_visits_section ON section_visits(section);
CREATE INDEX IF NOT EXISTS idx_section_visits_epoch ON section_visits(epoch);
CREATE TABLE IF NOT EXISTS seo_page_views (
    view_pk INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    station_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    epoch INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seo_views_station ON seo_page_views(provider, station_id);
CREATE INDEX IF NOT EXISTS idx_seo_views_epoch ON seo_page_views(epoch);
CREATE TABLE IF NOT EXISTS seo_panel_clicks (
    click_pk INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    station_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    epoch INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seo_clicks_station ON seo_panel_clicks(provider, station_id);
CREATE INDEX IF NOT EXISTS idx_seo_clicks_epoch ON seo_panel_clicks(epoch);
"""

TRACKED_SECTIONS = (
    "observation",
    "trends",
    "historical",
    "map.stations",
    "map.temperature",
    "map.wind",
    "map.precipitation",
    # Predicción, según de dónde se llegue. ``forecast.streamlit`` ya no se
    # emite —la app de Streamlit está fuera de uso—, pero se conserva para que
    # el panel siga sumando lo registrado en su día.
    "forecast.app",
    "forecast.streamlit",
    "forecast.direct",
    "ranking",
)
_TRACKED_SECTION_SET = frozenset(TRACKED_SECTIONS)

# De dónde llegó una visita. Se decide en el navegador, que es el único que
# ve el referente; aquí solo se valida.
ENTRY_POINTS = ("search", "external", "internal", "direct")

# Con qué se mira. Igual que la entrada, lo decide el navegador.
DEVICES = ("mobile", "tablet", "desktop")

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
    visit_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(station_visits)")
    }
    if "source" not in visit_columns:
        connection.execute(
            "ALTER TABLE station_visits ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy'"
        )
    for columna in ("language", "entry", "referrer_domain", "device",
                    "browser_languages", "request_languages", "saved_language", "url_language",
                    "page_request_languages", "language_reason", "request_client"):
        if columna not in visit_columns:
            connection.execute(
                f"ALTER TABLE station_visits ADD COLUMN {columna} TEXT NOT NULL DEFAULT ''"
            )
    return connection


def record_visit(
    provider: str,
    station_id: str,
    name: str = "",
    *,
    source: str = "app",
    language: str = "",
    entry: str = "",
    referrer_domain: str = "",
    device: str = "",
    browser_languages: str = "",
    page_request_languages: str = "",
    language_reason: str = "",
    request_client: str = "",
    request_languages: str = "",
    saved_language: str = "",
    url_language: str = "",
    settings=None,
) -> None:
    provider = str(provider or "").strip().upper()
    station_id = str(station_id or "").strip()
    if not provider or not station_id:
        return
    source = str(source or "").strip().lower()
    source = source if source in {"app", "seo"} else "app"
    entry = str(entry or "").strip().lower()
    entry = entry if entry in ENTRY_POINTS else ""
    # El dominio solo tiene sentido cuando alguien nos enlazó de verdad.
    dominio = str(referrer_domain or "").strip().lower()[:120]
    if entry not in {"search", "external"}:
        dominio = ""
    device = str(device or "").strip().lower()
    device = device if device in DEVICES else ""
    with _connect(settings) as connection:
        connection.execute(
            "INSERT INTO station_visits"
            "(provider, station_id, name, source, language, entry, referrer_domain,"
            " device, browser_languages, request_languages, saved_language, url_language, page_request_languages, language_reason, request_client, epoch)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                provider,
                station_id,
                str(name or "").strip()[:120],
                source,
                str(language or "").strip().lower()[:8],
                entry,
                dominio,
                device,
                language_tags(browser_languages),
                language_tags(request_languages),
                saved_language if saved_language in SUPPORTED_LANGUAGES else "",
                url_language if url_language in SUPPORTED_LANGUAGES else "",
                language_tags(page_request_languages),
                language_reason if language_reason in {"saved", "browser", "url", "fallback"} else "",
                request_client if request_client in {"googlebot", "bingbot", "other_bot", "unidentified"} else "",
                int(time.time()),
            ),
        )


def record_seo_panel_click(
    provider: str,
    station_id: str,
    name: str = "",
    *,
    language: str = "",
    settings=None,
) -> None:
    provider = str(provider or "").strip().upper()
    station_id = str(station_id or "").strip()
    if not provider or not station_id:
        return
    with _connect(settings) as connection:
        connection.execute(
            "INSERT INTO seo_panel_clicks(provider, station_id, name, language, epoch)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                provider,
                station_id,
                str(name or "").strip()[:120],
                str(language or "").strip().lower()[:8],
                int(time.time()),
            ),
        )


def record_seo_page_view(
    provider: str,
    station_id: str,
    name: str = "",
    *,
    language: str = "",
    settings=None,
) -> None:
    """Registra la apertura del HTML de una ficha SEO.

    Es independiente de que el iframe de observaciones consiga conectarse a
    la fuente oficial. No se almacenan IP, sesión ni identificadores de
    usuario.
    """
    provider = str(provider or "").strip().upper()
    station_id = str(station_id or "").strip()
    if not provider or not station_id:
        return
    with _connect(settings) as connection:
        connection.execute(
            "INSERT INTO seo_page_views(provider, station_id, name, language, epoch)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                provider,
                station_id,
                str(name or "").strip()[:120],
                str(language or "").strip().lower()[:8],
                int(time.time()),
            ),
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


def record_section_visit(section: str, *, settings=None) -> None:
    """Registra una transición a una sección conocida, sin datos personales."""
    section = str(section or "").strip().lower()
    if section not in _TRACKED_SECTION_SET:
        return
    with _connect(settings) as connection:
        connection.execute(
            "INSERT INTO section_visits(section, epoch) VALUES (?, ?)",
            (section, int(time.time())),
        )


def purge_crawler_visits(*, settings=None) -> int:
    """Borra las visitas que ya se guardaron identificadas como rastreador.

    Desde que los endpoints las descartan al entrar, esta tabla solo recibe
    visitas sin identificar; lo anterior sigue ahí, engordando los recuentos
    por idioma y por estación. Devuelve cuántas filas se han eliminado.

    Solo se puede limpiar ``station_visits``: es la única tabla que guardó de
    qué cliente venía cada fila. Los errores, las secciones y las aperturas de
    ficha no lo anotaron, así que su histórico se queda con el ruido dentro.
    """
    with _connect(settings) as connection:
        cursor = connection.execute(
            "DELETE FROM station_visits WHERE request_client != '' AND request_client != 'unidentified'"
        )
        return int(cursor.rowcount or 0)


def visit_summary(*, settings=None, limit: int = 500) -> Dict[str, Any]:
    """Conexiones, errores y secciones agregados por ventanas temporales."""
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
                   SUM(CASE WHEN source = 'app' THEN 1 ELSE 0 END) AS app_total,
                   SUM(CASE WHEN source = 'app' AND epoch >= ? THEN 1 ELSE 0 END) AS app_d30,
                   SUM(CASE WHEN source = 'seo' THEN 1 ELSE 0 END) AS seo_total,
                   SUM(CASE WHEN source = 'seo' AND epoch >= ? THEN 1 ELSE 0 END) AS seo_d30,
                   SUM(CASE WHEN source = 'legacy' THEN 1 ELSE 0 END) AS legacy_total,
                   SUM(CASE WHEN source = 'legacy' AND epoch >= ? THEN 1 ELSE 0 END) AS legacy_d30,
                   MAX(epoch) AS last_epoch
            FROM station_visits v
            GROUP BY provider, station_id
            ORDER BY total DESC, last_epoch DESC
            LIMIT {int(limit)}
            """,
            (
                now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"],
                now - WINDOWS["d30"], now - WINDOWS["d30"], now - WINDOWS["d30"],
            ),
        ).fetchall()
        totals_row = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d1,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30,
                   SUM(CASE WHEN source = 'app' THEN 1 ELSE 0 END) AS app_total,
                   SUM(CASE WHEN source = 'app' AND epoch >= ? THEN 1 ELSE 0 END) AS app_d30,
                   SUM(CASE WHEN source = 'seo' THEN 1 ELSE 0 END) AS seo_total,
                   SUM(CASE WHEN source = 'seo' AND epoch >= ? THEN 1 ELSE 0 END) AS seo_d30,
                   SUM(CASE WHEN source = 'legacy' THEN 1 ELSE 0 END) AS legacy_total,
                   SUM(CASE WHEN source = 'legacy' AND epoch >= ? THEN 1 ELSE 0 END) AS legacy_d30,
                   COUNT(DISTINCT provider || '|' || station_id) AS stations
            FROM station_visits
            """,
            (
                now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"],
                now - WINDOWS["d30"], now - WINDOWS["d30"], now - WINDOWS["d30"],
            ),
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
        section_rows = connection.execute(
            """
            SELECT section,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d1,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30,
                   MAX(epoch) AS last_epoch
            FROM section_visits
            GROUP BY section
            """,
            (now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"]),
        ).fetchall()
        seo_view_rows = connection.execute(
            """
            SELECT provider, station_id,
                   (SELECT s2.name FROM seo_page_views s2
                    WHERE s2.provider = s.provider AND s2.station_id = s.station_id
                      AND s2.name <> '' ORDER BY s2.epoch DESC LIMIT 1) AS name,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d1,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30,
                   MAX(epoch) AS last_epoch
            FROM seo_page_views s
            GROUP BY provider, station_id
            """,
            (now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"]),
        ).fetchall()
        seo_view_totals_row = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d1,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30
            FROM seo_page_views
            """,
            (now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"]),
        ).fetchone()
        panel_click_rows = connection.execute(
            """
            SELECT provider, station_id,
                   (SELECT c2.name FROM seo_panel_clicks c2
                    WHERE c2.provider = c.provider AND c2.station_id = c.station_id
                      AND c2.name <> '' ORDER BY c2.epoch DESC LIMIT 1) AS name,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d1,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30,
                   MAX(epoch) AS last_epoch
            FROM seo_panel_clicks c
            GROUP BY provider, station_id
            """,
            (now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"]),
        ).fetchall()
        panel_click_totals_row = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d1,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30
            FROM seo_panel_clicks
            """,
            (now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"]),
        ).fetchone()

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
            "app_total": int(row["app_total"] or 0),
            "app_d30": int(row["app_d30"] or 0),
            "seo_total": int(row["seo_total"] or 0),
            "seo_d30": int(row["seo_d30"] or 0),
            "legacy_total": int(row["legacy_total"] or 0),
            "legacy_d30": int(row["legacy_d30"] or 0),
            "last_epoch": int(row["last_epoch"] or 0),
            "errors": dict(empty_errors),
            "panel_clicks": {"d1": 0, "d7": 0, "d30": 0, "total": 0, "last_epoch": 0},
        }
    for row in seo_view_rows:
        key = (row["provider"], row["station_id"])
        station = stations_by_key.get(key)
        if station is None:
            station = stations_by_key[key] = {
                "provider": row["provider"],
                "station_id": row["station_id"],
                "name": row["name"] or row["station_id"],
                "d1": 0, "d7": 0, "d30": 0, "total": 0,
                "app_total": 0, "app_d30": 0,
                "seo_total": 0, "seo_d30": 0,
                "legacy_total": 0, "legacy_d30": 0,
                "last_epoch": 0,
                "errors": dict(empty_errors),
                "panel_clicks": {"d1": 0, "d7": 0, "d30": 0, "total": 0, "last_epoch": 0},
            }
        station["seo_total"] = int(row["total"] or 0)
        station["seo_d30"] = int(row["d30"] or 0)
        if int(row["last_epoch"] or 0) > int(station["last_epoch"] or 0):
            station["last_epoch"] = int(row["last_epoch"] or 0)
            if row["name"]:
                station["name"] = row["name"]
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
                "app_total": 0,
                "app_d30": 0,
                "seo_total": 0,
                "seo_d30": 0,
                "legacy_total": 0,
                "legacy_d30": 0,
                "last_epoch": 0,
                "errors": dict(empty_errors),
                "panel_clicks": {"d1": 0, "d7": 0, "d30": 0, "total": 0, "last_epoch": 0},
            }
        station["errors"] = {
            "d1": int(row["d1"] or 0),
            "d7": int(row["d7"] or 0),
            "d30": int(row["d30"] or 0),
            "total": int(row["total"] or 0),
            "last_epoch": int(row["last_epoch"] or 0),
            "last_kind": str(row["last_kind"] or ""),
        }
    for row in panel_click_rows:
        key = (row["provider"], row["station_id"])
        station = stations_by_key.get(key)
        if station is None:
            station = stations_by_key[key] = {
                "provider": row["provider"],
                "station_id": row["station_id"],
                "name": row["name"] or row["station_id"],
                "d1": 0, "d7": 0, "d30": 0, "total": 0,
                "app_total": 0, "app_d30": 0, "seo_total": 0, "seo_d30": 0,
                "legacy_total": 0, "legacy_d30": 0,
                "last_epoch": 0,
                "errors": dict(empty_errors),
                "panel_clicks": {},
            }
        station["panel_clicks"] = {
            "d1": int(row["d1"] or 0),
            "d7": int(row["d7"] or 0),
            "d30": int(row["d30"] or 0),
            "total": int(row["total"] or 0),
            "last_epoch": int(row["last_epoch"] or 0),
        }

    stations: List[Dict[str, Any]] = sorted(
        stations_by_key.values(),
        key=lambda s: (s["total"], s["last_epoch"]),
        reverse=True,
    )[: int(limit)]
    sections_by_name = {
        str(row["section"]): {
            "section": str(row["section"]),
            "d1": int(row["d1"] or 0),
            "d7": int(row["d7"] or 0),
            "d30": int(row["d30"] or 0),
            "total": int(row["total"] or 0),
            "last_epoch": int(row["last_epoch"] or 0),
        }
        for row in section_rows
        if str(row["section"]) in _TRACKED_SECTION_SET
    }
    sections = [
        sections_by_name.get(
            section,
            {
                "section": section,
                "d1": 0,
                "d7": 0,
                "d30": 0,
                "total": 0,
                "last_epoch": 0,
            },
        )
        for section in TRACKED_SECTIONS
    ]
    sections.sort(key=lambda row: (row["total"], row["last_epoch"]), reverse=True)

    # País de cada estación visitada, para saber a dónde se conecta la gente.
    # Sale del catálogo, no de la base de uso: allí no se guarda, y resolverlo
    # aquí evita duplicar el dato en cada visita registrada.
    countries: Dict[str, int] = {}
    try:
        from server.services import stations as stations_svc

        for fila in stations:
            registro = stations_svc.get_station(fila["provider"], fila["station_id"])
            code = str((registro or {}).get("country") or "").strip().upper()
            fila["country"] = code or None
            if code:
                countries[code] = countries.get(code, 0) + int(fila.get("total") or 0)
    except Exception:  # noqa: BLE001 — el panel no puede caerse por el catálogo
        logger.warning("uso: no se pudo resolver el país de las estaciones", exc_info=True)

    by_country = sorted(
        ({"country": code, "visits": total} for code, total in countries.items()),
        key=lambda fila: -fila["visits"],
    )

    return {
        "stations": stations,
        "by_country": by_country,
        "totals": {
            "d1": int(totals_row["d1"] or 0),
            "d7": int(totals_row["d7"] or 0),
            "d30": int(totals_row["d30"] or 0),
            "total": int(totals_row["total"] or 0),
            "stations": int(totals_row["stations"] or 0),
            "sources": {
                "app": {
                    "d30": int(totals_row["app_d30"] or 0),
                    "total": int(totals_row["app_total"] or 0),
                },
                "seo": {
                    "d30": int(seo_view_totals_row["d30"] or 0),
                    "total": int(seo_view_totals_row["total"] or 0),
                },
                "legacy": {
                    "d30": int(totals_row["legacy_d30"] or 0),
                    "total": int(totals_row["legacy_total"] or 0),
                },
            },
            "panel_clicks": {
                "d1": int(panel_click_totals_row["d1"] or 0),
                "d7": int(panel_click_totals_row["d7"] or 0),
                "d30": int(panel_click_totals_row["d30"] or 0),
                "total": int(panel_click_totals_row["total"] or 0),
            },
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
        "sections": sections,
    }


def station_detail(
    provider: str,
    station_id: str,
    *,
    settings=None,
    limit: int = 60,
) -> Optional[Dict[str, Any]]:
    """Detalle de una estación: agregados, últimas visitas y últimos errores.

    El resumen del panel dice cuántos errores ha habido, pero no cuándo ni de
    qué tipo. Esto abre esa caja: los eventos recientes con su fecha, más el
    reparto de errores por tipo.

    Devuelve ``None`` si la estación no tiene ni una visita ni un error
    registrados.
    """
    provider = str(provider or "").strip().upper()
    station_id = str(station_id or "").strip()
    if not provider or not station_id:
        return None
    limit = max(1, min(int(limit), 200))
    now = int(time.time())
    clave = (provider, station_id)
    ventanas = (now - WINDOWS["d1"], now - WINDOWS["d7"], now - WINDOWS["d30"])

    def _ventanas(tabla: str, connection) -> Dict[str, int]:
        row = connection.execute(
            f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d1,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d7,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30,
                   MAX(epoch) AS last_epoch
            FROM {tabla}
            WHERE provider = ? AND station_id = ?
            """,
            (*ventanas, *clave),
        ).fetchone()
        return {
            "d1": int(row["d1"] or 0),
            "d7": int(row["d7"] or 0),
            "d30": int(row["d30"] or 0),
            "total": int(row["total"] or 0),
            "last_epoch": int(row["last_epoch"] or 0),
        }

    with _connect(settings) as connection:
        connection.row_factory = sqlite3.Row
        visits = _ventanas("station_visits", connection)
        errors = _ventanas("station_errors", connection)
        seo_views = _ventanas("seo_page_views", connection)
        panel_clicks = _ventanas("seo_panel_clicks", connection)
        if not (visits["total"] or errors["total"] or seo_views["total"] or panel_clicks["total"]):
            return None
        name_row = connection.execute(
            """
            SELECT name FROM (
                SELECT name, epoch FROM station_visits
                 WHERE provider = ? AND station_id = ? AND name <> ''
                UNION ALL
                SELECT name, epoch FROM station_errors
                 WHERE provider = ? AND station_id = ? AND name <> ''
                UNION ALL
                SELECT name, epoch FROM seo_page_views
                 WHERE provider = ? AND station_id = ? AND name <> ''
            ) ORDER BY epoch DESC LIMIT 1
            """,
            (*clave, *clave, *clave),
        ).fetchone()
        by_source_rows = connection.execute(
            """
            SELECT source,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30
            FROM station_visits
            WHERE provider = ? AND station_id = ?
            GROUP BY source
            """,
            (now - WINDOWS["d30"], *clave),
        ).fetchall()
        error_kind_rows = connection.execute(
            """
            SELECT error_kind,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30,
                   MAX(epoch) AS last_epoch
            FROM station_errors
            WHERE provider = ? AND station_id = ?
            GROUP BY error_kind
            ORDER BY total DESC
            """,
            (now - WINDOWS["d30"], *clave),
        ).fetchall()
        by_language_rows = connection.execute(
            """
            SELECT language,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30
            FROM station_visits
            WHERE provider = ? AND station_id = ?
            GROUP BY language
            ORDER BY total DESC
            """,
            (now - WINDOWS["d30"], *clave),
        ).fetchall()
        by_entry_rows = connection.execute(
            """
            SELECT entry,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30
            FROM station_visits
            WHERE provider = ? AND station_id = ?
            GROUP BY entry
            ORDER BY total DESC
            """,
            (now - WINDOWS["d30"], *clave),
        ).fetchall()
        by_device_rows = connection.execute(
            """
            SELECT device,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30
            FROM station_visits
            WHERE provider = ? AND station_id = ?
            GROUP BY device
            ORDER BY total DESC
            """,
            (now - WINDOWS["d30"], *clave),
        ).fetchall()
        referrer_rows = connection.execute(
            """
            SELECT referrer_domain,
                   COUNT(*) AS total,
                   SUM(CASE WHEN epoch >= ? THEN 1 ELSE 0 END) AS d30,
                   MAX(epoch) AS last_epoch
            FROM station_visits
            WHERE provider = ? AND station_id = ? AND referrer_domain <> ''
            GROUP BY referrer_domain
            ORDER BY total DESC
            LIMIT 20
            """,
            (now - WINDOWS["d30"], *clave),
        ).fetchall()
        recent_visit_rows = connection.execute(
            f"""
            SELECT epoch, source, language, entry, referrer_domain, device,
                   browser_languages, request_languages, saved_language, url_language,
                   page_request_languages, language_reason, request_client
            FROM station_visits
            WHERE provider = ? AND station_id = ?
            ORDER BY epoch DESC LIMIT {limit}
            """,
            clave,
        ).fetchall()
        recent_error_rows = connection.execute(
            f"""
            SELECT epoch, error_kind, status_code FROM station_errors
            WHERE provider = ? AND station_id = ?
            ORDER BY epoch DESC LIMIT {limit}
            """,
            clave,
        ).fetchall()
        recent_seo_rows = connection.execute(
            f"""
            SELECT epoch, language FROM seo_page_views
            WHERE provider = ? AND station_id = ?
            ORDER BY epoch DESC LIMIT {limit}
            """,
            clave,
        ).fetchall()

    by_source = {source: {"d30": 0, "total": 0} for source in ("app", "seo", "legacy")}
    for row in by_source_rows:
        by_source[str(row["source"] or "app")] = {
            "d30": int(row["d30"] or 0),
            "total": int(row["total"] or 0),
        }

    return {
        "provider": provider,
        "station_id": station_id,
        "name": (name_row["name"] if name_row else "") or station_id,
        "visits": visits,
        "visits_by_source": by_source,
        "errors": errors,
        "seo_views": seo_views,
        "panel_clicks": panel_clicks,
        "error_kinds": [
            {
                "kind": str(row["error_kind"]),
                "d30": int(row["d30"] or 0),
                "total": int(row["total"] or 0),
                "last_epoch": int(row["last_epoch"] or 0),
            }
            for row in error_kind_rows
        ],
        "visits_by_language": [
            {
                "language": str(row["language"] or ""),
                "d30": int(row["d30"] or 0),
                "total": int(row["total"] or 0),
            }
            for row in by_language_rows
        ],
        "visits_by_entry": [
            {
                "entry": str(row["entry"] or ""),
                "d30": int(row["d30"] or 0),
                "total": int(row["total"] or 0),
            }
            for row in by_entry_rows
        ],
        "visits_by_device": [
            {
                "device": str(row["device"] or ""),
                "d30": int(row["d30"] or 0),
                "total": int(row["total"] or 0),
            }
            for row in by_device_rows
        ],
        "referrers": [
            {
                "domain": str(row["referrer_domain"] or ""),
                "d30": int(row["d30"] or 0),
                "total": int(row["total"] or 0),
                "last_epoch": int(row["last_epoch"] or 0),
            }
            for row in referrer_rows
        ],
        "recent_visits": [
            {
                "epoch": int(row["epoch"] or 0),
                "source": str(row["source"] or "app"),
                "language": str(row["language"] or ""),
                "entry": str(row["entry"] or ""),
                "referrer_domain": str(row["referrer_domain"] or ""),
                "device": str(row["device"] or ""),
                **{key: str(row[key] or "") for key in (
                    "browser_languages", "request_languages", "saved_language", "url_language",
                    "page_request_languages", "language_reason", "request_client")},
            }
            for row in recent_visit_rows
        ],
        "recent_errors": [
            {
                "epoch": int(row["epoch"] or 0),
                "kind": str(row["error_kind"] or ""),
                "status_code": (int(row["status_code"]) if row["status_code"] is not None else None),
            }
            for row in recent_error_rows
        ],
        "recent_seo_views": [
            {"epoch": int(row["epoch"] or 0), "language": str(row["language"] or "")}
            for row in recent_seo_rows
        ],
    }


SUPPORTED_LANGUAGES = {"es", "ca", "en", "fr", "it", "pt"}


def language_tags(value: str) -> str:
    """Lista acotada de etiquetas, ordenada por prioridad HTTP; sin datos libres."""
    import re
    tags = []
    for chunk in str(value or "")[:512].split(","):
        tag, *params = chunk.strip().split(";")
        if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*", tag):
            continue
        quality = 1.0
        for param in params:
            if param.strip().startswith("q="):
                try:
                    quality = float(param.strip()[2:])
                except ValueError:
                    quality = 0
        if 0 < quality <= 1:
            tags.append((quality, tag.lower()))
    ordered = dict.fromkeys(tag for _, tag in sorted(tags, key=lambda pair: -pair[0]))
    return ",".join(list(ordered)[:6])[:200]


def request_client(user_agent: str) -> str:
    """Declaración del cliente, no verificación de identidad ni prueba de humanidad.

    Solo se conserva la categoría, nunca el user-agent completo ni la IP.
    """
    import re
    agent = str(user_agent or "")[:1024].lower()
    if "googlebot" in agent or "google-inspectiontool" in agent:
        return "googlebot"
    if "bingbot" in agent or "bingpreview" in agent:
        return "bingbot"
    if re.search(r"bot\b|crawler|spider|headlesschrome", agent):
        return "other_bot"
    return "unidentified"


def is_crawler(user_agent: str) -> bool:
    """¿Se declara rastreador? Entonces su paso no cuenta como uso.

    Googlebot ejecuta el JavaScript de la ficha, así que llegaba a los mismos
    endpoints que una persona y contaminaba justo lo que se mira para decidir:
    de qué idioma se sirve cada visita y qué estaciones fallan. Una estación
    rota que el rastreador visita mil veces parecía mil errores.

    Descartar por user-agent es asimétrico a propósito: quien se declara bot
    lo hace por convención y no gana nada mintiendo hacia ese lado. Lo que no
    se declara sigue contando, aunque no haya forma de confirmar que es una
    persona —de ahí que su categoría se llame `unidentified` y no `human`.
    """
    return request_client(user_agent) != "unidentified"
