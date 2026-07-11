"""Read-only metadata helpers for local station catalogs."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from data_files import METEOCAT_STATIONS_PATH, METEOFRANCE_STATIONS_PATH, STATIONS_DB_PATH


@lru_cache(maxsize=4)
def _catalog(path: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _station(path: Path, field: str, station_id: str) -> dict[str, Any]:
    target = str(station_id or "").strip()
    return next((row for row in _catalog(str(path)) if str(row.get(field, "")).strip() == target), {})


def meteofrance_series_start(station_id: str) -> str | None:
    raw = str(_station(METEOFRANCE_STATIONS_PATH, "id_station", station_id).get("date_ouverture", "") or "").strip()
    return raw or None


def meteocat_series_start(station_id: str) -> str | None:
    station = _station(METEOCAT_STATIONS_PATH, "codi", station_id)
    candidates: list[str] = []
    for status in station.get("estats", []) if isinstance(station.get("estats"), list) else []:
        if not isinstance(status, dict):
            continue
        raw = str(status.get("dataInici", "") or "").strip()
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            candidates.append(parsed.astimezone(timezone.utc).strftime("%Y-%m-%d"))
        except ValueError:
            candidates.append(raw.split("T", 1)[0])
    return min(candidates) if candidates else None


def _sqlite_raw_station_payload(provider: str, station_id: str, network_code: str = "") -> dict[str, Any]:
    station = str(station_id or "").strip()
    if not station:
        return {}
    provider_id = str(provider or "").strip().upper()
    network = str(network_code or "").strip()
    try:
        connection = sqlite3.connect(f"file:{Path(STATIONS_DB_PATH).resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT r.raw_json
            FROM stations s
            JOIN station_inventory_records r ON r.record_pk = s.source_record_pk
            WHERE s.provider = ? COLLATE NOCASE
              AND s.network_code = ? COLLATE NOCASE
              AND s.station_id = ? COLLATE NOCASE
            LIMIT 1
            """,
            (provider_id, network, station),
        ).fetchone()
        connection.close()
    except sqlite3.Error:
        return {}
    if not row:
        return {}
    try:
        payload = json.loads(str(row["raw_json"] or "{}"))
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def aemet_series_start(station_id: str) -> str | None:
    payload = _sqlite_raw_station_payload("AEMET", station_id)
    start = str(payload.get("archive_begin") or payload.get("series_start") or "").strip()
    return start or None


def iem_series_start(station_id: str) -> str | None:
    raw = str(station_id or "").strip()
    if "|" not in raw:
        return None
    network, station = (part.strip() for part in raw.split("|", 1))
    if not network or not station:
        return None
    payload = _sqlite_raw_station_payload("IEM", station, network)
    start = str(payload.get("archive_begin") or "").strip()
    return start or None
