#!/usr/bin/env python3
"""Build a separate SQLite inventory of public Netatmo weather stations."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

API_URL = "https://api.netatmo.com/api/getpublicdata"
DEFAULT_OUTPUT = ROOT / "data" / "netatmo_pws_stations.sqlite"
SPAIN_BBOX = (35.5, -10.0, 44.5, 4.5)  # lat_sw, lon_sw, lat_ne, lon_ne
WORLD_BBOX = (-60.0, -180.0, 85.0, 180.0)
DEFAULT_CACHE_DIR = ROOT / "data" / "netatmo_tile_cache"
SCHEMA_VERSION = "1"


SCHEMA = """
CREATE TABLE catalog_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE netatmo_stations (
    station_pk INTEGER PRIMARY KEY,
    station_id TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL DEFAULT 'NETATMO',
    name TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    country TEXT,
    city TEXT,
    timezone TEXT,
    elevation_m REAL,
    observed_at INTEGER,
    temperature_c REAL,
    humidity_pct REAL,
    pressure_hpa REAL,
    wind_kmh REAL,
    gust_kmh REAL,
    wind_direction_deg REAL,
    rain_rate_mm_h REAL,
    rain_1h_mm REAL,
    rain_24h_mm REAL,
    thermometer INTEGER NOT NULL DEFAULT 0,
    hygrometer INTEGER NOT NULL DEFAULT 0,
    barometer INTEGER NOT NULL DEFAULT 0,
    anemometer INTEGER NOT NULL DEFAULT 0,
    wind_vane INTEGER NOT NULL DEFAULT 0,
    rain_gauge INTEGER NOT NULL DEFAULT 0,
    pyranometer INTEGER NOT NULL DEFAULT 0,
    uv INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL CHECK (json_valid(raw_json))
);

CREATE INDEX idx_netatmo_country ON netatmo_stations(country);
CREATE INDEX idx_netatmo_observed_at ON netatmo_stations(observed_at);
CREATE INDEX idx_netatmo_lat_lon ON netatmo_stations(latitude, longitude);

CREATE VIRTUAL TABLE netatmo_station_rtree USING rtree(
    station_pk,
    min_latitude, max_latitude,
    min_longitude, max_longitude
);

CREATE TABLE windy_duplicate_candidates (
    netatmo_station_id TEXT NOT NULL,
    windy_station_id TEXT NOT NULL,
    distance_m REAL NOT NULL,
    confidence TEXT NOT NULL,
    netatmo_resolution_s INTEGER,
    windy_resolution_s INTEGER,
    netatmo_sensor_count INTEGER,
    windy_sensor_count INTEGER,
    preferred_provider TEXT,
    reason TEXT,
    checked_at TEXT,
    PRIMARY KEY(netatmo_station_id, windy_station_id)
);
"""


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _latest_res(measure: Any) -> tuple[int | None, list[Any], list[str]]:
    if not isinstance(measure, dict):
        return None, [], []
    types = [str(value).strip().lower() for value in (measure.get("type") or [])]
    rows = measure.get("res")
    if not isinstance(rows, dict) or not rows:
        return None, [], types
    candidates = []
    for raw_epoch, values in rows.items():
        try:
            epoch = int(raw_epoch)
        except (TypeError, ValueError):
            continue
        candidates.append((epoch, values if isinstance(values, list) else []))
    if not candidates:
        return None, [], types
    epoch, values = max(candidates, key=lambda item: item[0])
    return epoch, values, types


def normalize_station(row: dict[str, Any]) -> dict[str, Any] | None:
    station_id = str(row.get("_id") or "").strip()
    place = row.get("place") if isinstance(row.get("place"), dict) else {}
    location = place.get("location") if isinstance(place.get("location"), list) else []
    if not station_id or len(location) < 2:
        return None
    longitude = _number(location[0])
    latitude = _number(location[1])
    if latitude is None or longitude is None:
        return None

    values: dict[str, float | None] = {
        "temperature_c": None, "humidity_pct": None, "pressure_hpa": None,
        "wind_kmh": None, "gust_kmh": None, "wind_direction_deg": None,
        "rain_rate_mm_h": None, "rain_1h_mm": None, "rain_24h_mm": None,
    }
    sensors = {
        "thermometer": False, "hygrometer": False, "barometer": False,
        "anemometer": False, "wind_vane": False, "rain_gauge": False,
        "pyranometer": False, "uv": False,
    }
    observed_epochs: list[int] = []
    measures = row.get("measures") if isinstance(row.get("measures"), dict) else {}
    for measure in measures.values():
        if not isinstance(measure, dict):
            continue
        epoch, result_values, result_types = _latest_res(measure)
        if epoch is not None:
            observed_epochs.append(epoch)
        for index, measure_type in enumerate(result_types):
            value = _number(result_values[index] if index < len(result_values) else None)
            if measure_type == "temperature":
                sensors["thermometer"] = True
                values["temperature_c"] = value
            elif measure_type == "humidity":
                sensors["hygrometer"] = True
                values["humidity_pct"] = value
            elif measure_type == "pressure":
                sensors["barometer"] = True
                values["pressure_hpa"] = value

        if "wind_strength" in measure or "gust_strength" in measure:
            sensors["anemometer"] = True
            sensors["wind_vane"] = True
            values["wind_kmh"] = _number(measure.get("wind_strength"))
            values["gust_kmh"] = _number(measure.get("gust_strength"))
            values["wind_direction_deg"] = _number(measure.get("wind_angle"))
            wind_epoch = measure.get("wind_timeutc")
            if isinstance(wind_epoch, (int, float)):
                observed_epochs.append(int(wind_epoch))

        if any(key in measure for key in ("rain_live", "rain_60min", "rain_24h")):
            sensors["rain_gauge"] = True
            values["rain_rate_mm_h"] = _number(measure.get("rain_live"))
            values["rain_1h_mm"] = _number(measure.get("rain_60min"))
            values["rain_24h_mm"] = _number(measure.get("rain_24h"))
            rain_epoch = measure.get("rain_timeutc")
            if isinstance(rain_epoch, (int, float)):
                observed_epochs.append(int(rain_epoch))

    city = str(place.get("city") or "").strip()
    street = str(place.get("street") or "").strip()
    # "Ciudad · Calle" para desambiguar: todas las estaciones de una ciudad
    # compartían nombre (y colisionaban los slugs de los deep links).
    if city and street:
        name = f"{city} · {street}"
    else:
        name = city or street or f"Netatmo {station_id[-5:]}"
    return {
        "station_id": station_id,
        "name": name,
        "latitude": latitude,
        "longitude": longitude,
        "country": str(place.get("country") or "").strip().upper() or None,
        "city": city or None,
        "timezone": str(place.get("timezone") or "").strip() or None,
        "elevation_m": _number(place.get("altitude")),
        "observed_at": max(observed_epochs) if observed_epochs else None,
        **values,
        **sensors,
        "raw_json": json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }


def _request_bbox(
    access_token: str,
    bbox: tuple[float, float, float, float],
    *,
    opener: Callable[..., Any],
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    lat_sw, lon_sw, lat_ne, lon_ne = bbox
    query = urllib.parse.urlencode({
        "lat_sw": lat_sw, "lon_sw": lon_sw,
        "lat_ne": lat_ne, "lon_ne": lon_ne,
        "filter": "false",
    })
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    with opener(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise RuntimeError(f"Unexpected Netatmo response: {payload.get('error') if isinstance(payload, dict) else 'invalid JSON'}")
    body = payload.get("body")
    if not isinstance(body, list):
        raise RuntimeError("Netatmo response does not contain a station list")
    return [row for row in body if isinstance(row, dict)]


def _tiles(bbox: tuple[float, float, float, float], tile_size: float) -> Iterable[tuple[float, float, float, float]]:
    lat_sw, lon_sw, lat_ne, lon_ne = bbox
    lat = lat_sw
    while lat < lat_ne:
        lon = lon_sw
        while lon < lon_ne:
            yield lat, lon, min(lat_ne, lat + tile_size), min(lon_ne, lon + tile_size)
            lon += tile_size
        lat += tile_size


def _land_geometry():
    from shapely.geometry import shape
    from shapely.ops import unary_union

    borders = json.loads((ROOT / "data" / "ne_50m_admin_0_countries.geojson").read_text())
    return unary_union([
        shape(feature["geometry"])
        for feature in borders.get("features", [])
        if isinstance(feature, dict) and feature.get("geometry")
    ])


def _land_tiles(
    bbox: tuple[float, float, float, float],
    tile_size: float,
) -> list[tuple[float, float, float, float]]:
    from shapely.geometry import box

    land = _land_geometry()
    return [
        tile for tile in _tiles(bbox, tile_size)
        if land.intersects(box(tile[1], tile[0], tile[3], tile[2]))
    ]


def _tile_cache_path(cache_dir: Path, tile: tuple[float, float, float, float]) -> Path:
    key = "_".join(f"{value:.4f}".replace("-", "m").replace(".", "p") for value in tile)
    return cache_dir / f"{key}.json"


def _request_bbox_cached(
    access_token: str,
    bbox: tuple[float, float, float, float],
    *,
    opener: Callable[..., Any],
    cache_dir: Path | None,
) -> tuple[list[dict[str, Any]], bool]:
    cache_path = _tile_cache_path(cache_dir, bbox) if cache_dir else None
    if cache_path and cache_path.exists():
        payload = json.loads(cache_path.read_text())
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)], True
    rows = _request_bbox(access_token, bbox, opener=opener)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
        os.replace(temporary, cache_path)
    return rows, False


def _split_tile(tile: tuple[float, float, float, float]) -> list[tuple[float, float, float, float]]:
    lat_sw, lon_sw, lat_ne, lon_ne = tile
    lat_mid = (lat_sw + lat_ne) / 2.0
    lon_mid = (lon_sw + lon_ne) / 2.0
    return [
        (lat_sw, lon_sw, lat_mid, lon_mid),
        (lat_sw, lon_mid, lat_mid, lon_ne),
        (lat_mid, lon_sw, lat_ne, lon_mid),
        (lat_mid, lon_mid, lat_ne, lon_ne),
    ]


def fetch_inventory(
    access_token: str,
    *,
    bbox: tuple[float, float, float, float] = SPAIN_BBOX,
    tile_size: float = 1.0,
    country: str = "ES",
    opener: Callable[..., Any] = urllib.request.urlopen,
    pause_s: float = 0.05,
    land_only: bool = False,
    adaptive: bool = False,
    split_threshold: int = 1000,
    coarse_split_threshold: int | None = None,
    min_tile_size: float = 1.0,
    cache_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not access_token.strip():
        raise ValueError("A Netatmo access token is required")
    stations: dict[str, dict[str, Any]] = {}
    requests = 0
    cached_requests = 0
    initial_tiles = _land_tiles(bbox, tile_size) if land_only else list(_tiles(bbox, tile_size))
    pending = list(reversed(initial_tiles))
    processed = 0
    while pending:
        tile = pending.pop()
        attempts = 0
        while True:
            try:
                rows, cached = _request_bbox_cached(
                    access_token, tile, opener=opener, cache_dir=cache_dir,
                )
                break
            except urllib.error.HTTPError as exc:
                attempts += 1
                if exc.code < 500 or attempts > 3:
                    raise RuntimeError(f"Netatmo returned HTTP {exc.code} for tile {tile}") from exc
                backoff = 5.0 * (3 ** (attempts - 1))
                print(f"HTTP {exc.code} for tile {tile}; retry {attempts}/3 in {backoff:.0f}s", flush=True)
                time.sleep(backoff)
            except (
                urllib.error.URLError, http.client.HTTPException,
                ConnectionError, TimeoutError, socket.timeout,
            ) as exc:
                # Cortes transitorios de red (RemoteDisconnected, resets…):
                # mismo backoff que los 5xx en vez de abortar todo el intento.
                attempts += 1
                if attempts > 3:
                    raise RuntimeError(f"Network error for tile {tile}: {exc}") from exc
                backoff = 5.0 * (3 ** (attempts - 1))
                print(f"Network error for tile {tile} ({exc}); retry {attempts}/3 in {backoff:.0f}s", flush=True)
                time.sleep(backoff)
        requests += int(not cached)
        cached_requests += int(cached)
        processed += 1
        lat_span = tile[2] - tile[0]
        lon_span = tile[3] - tile[1]
        span = max(lat_span, lon_span)
        # getpublicdata devuelve una MUESTRA no proporcional a la densidad en
        # bboxes grandes (Barcelona 1° -> 212 filas con miles debajo), así que
        # los tiles gruesos se parten con un umbral más bajo; el umbral alto
        # solo aplica a tiles ya pequeños, donde la muestra sí satura.
        effective_threshold = (
            coarse_split_threshold
            if coarse_split_threshold is not None and span > 0.5
            else split_threshold
        )
        if adaptive and len(rows) >= effective_threshold and span > min_tile_size:
            pending.extend(reversed(_split_tile(tile)))
            continue
        for raw in rows:
            normalized = normalize_station(raw)
            if not normalized:
                continue
            inside_tile = (
                tile[0] <= normalized["latitude"] <= tile[2]
                and tile[1] <= normalized["longitude"] <= tile[3]
            )
            if inside_tile and (not country or normalized["country"] == country.upper()):
                stations[normalized["station_id"]] = normalized
        if processed % 10 == 0:
            print(
                f"tiles={processed} pending={len(pending)} stations={len(stations)} "
                f"requests={requests} cached={cached_requests}",
                flush=True,
            )
        if pause_s > 0 and not cached:
            time.sleep(pause_s)
    return list(stations.values()), {
        "requests": requests,
        "cached_requests": cached_requests,
        "tiles": processed,
    }


def build_database(
    stations: list[dict[str, Any]],
    output_path: Path,
    *,
    requests: int,
    downloaded_at: str | None = None,
) -> dict[str, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent,
    )
    os.close(fd)
    temporary_path = Path(temporary_name)
    columns = [
        "station_id", "name", "latitude", "longitude", "country", "city",
        "timezone", "elevation_m", "observed_at", "temperature_c", "humidity_pct",
        "pressure_hpa", "wind_kmh", "gust_kmh", "wind_direction_deg",
        "rain_rate_mm_h", "rain_1h_mm", "rain_24h_mm", "thermometer", "hygrometer",
        "barometer", "anemometer", "wind_vane", "rain_gauge", "pyranometer", "uv",
        "raw_json",
    ]
    try:
        connection = sqlite3.connect(temporary_path)
        try:
            connection.executescript(SCHEMA)
            placeholders = ",".join("?" for _ in columns)
            connection.executemany(
                f"INSERT INTO netatmo_stations ({','.join(columns)}) VALUES ({placeholders})",
                [tuple(int(row[column]) if column in {
                    "thermometer", "hygrometer", "barometer", "anemometer",
                    "wind_vane", "rain_gauge", "pyranometer", "uv",
                } else row[column] for column in columns) for row in stations],
            )
            connection.execute(
                """
                INSERT INTO netatmo_station_rtree(
                    station_pk, min_latitude, max_latitude, min_longitude, max_longitude
                )
                SELECT station_pk, latitude, latitude, longitude, longitude
                FROM netatmo_stations
                """
            )
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "provider": "NETATMO",
                "source_url": API_URL,
                "downloaded_at": downloaded_at or datetime.now(timezone.utc).isoformat(),
                "request_count": str(requests),
            }
            connection.executemany(
                "INSERT INTO catalog_metadata(key, value) VALUES (?, ?)", metadata.items()
            )
            connection.commit()
            connection.execute("ANALYZE")
            connection.execute("PRAGMA optimize")
            counts = {
                "stations": connection.execute("SELECT COUNT(*) FROM netatmo_stations").fetchone()[0],
                "spatial": connection.execute("SELECT COUNT(*) FROM netatmo_station_rtree").fetchone()[0],
            }
        finally:
            connection.close()
        temporary_path.chmod(0o644)
        os.replace(temporary_path, output_path)
        return counts
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--access-token", default=os.environ.get("METEOLABX_NETATMO_ACCESS_TOKEN", ""))
    parser.add_argument("--tile-size", type=float, default=1.0)
    parser.add_argument("--country", default="ES")
    parser.add_argument("--world", action="store_true", help="Build a global land-only adaptive inventory")
    parser.add_argument(
        "--adaptive", action="store_true",
        help="Subdivide dense tiles (getpublicdata trunca la respuesta) también fuera de --world",
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--split-threshold", type=int, default=1000)
    parser.add_argument(
        "--coarse-split-threshold", type=int, default=100,
        help="Umbral de subdivisión para tiles > 0.5 grados (la muestra de la API no refleja la densidad real)",
    )
    parser.add_argument("--min-tile-size", type=float, default=1.0)
    args = parser.parse_args()
    world = bool(args.world)
    adaptive = world or bool(args.adaptive)
    stations, metadata = fetch_inventory(
        args.access_token,
        bbox=WORLD_BBOX if world else SPAIN_BBOX,
        tile_size=10.0 if world and args.tile_size == 1.0 else max(0.1, float(args.tile_size)),
        country="" if world else args.country,
        land_only=world,
        adaptive=adaptive,
        split_threshold=max(100, int(args.split_threshold)),
        coarse_split_threshold=max(50, int(args.coarse_split_threshold)),
        min_tile_size=max(0.1, float(args.min_tile_size)),
        cache_dir=args.cache_dir if adaptive else None,
    )
    counts = build_database(stations, args.output, requests=metadata["requests"])
    print(
        f"Saved {counts['stations']} Netatmo PWS stations "
        f"({counts['spatial']} spatially indexed) from {metadata['requests']} requests "
        f"to {args.output}"
    )


if __name__ == "__main__":
    main()
