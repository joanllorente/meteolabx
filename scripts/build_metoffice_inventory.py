#!/usr/bin/env python3
"""
Builds a reconstructed Met Office Weather DataHub Land Observations inventory.

The Land Observations API does not expose a station catalog endpoint. It exposes
`/observation-land/1/nearest` and `/observation-land/1/{geohash}`. This script
therefore scans a UK bounding box, asks for the nearest station at each grid
point, deduplicates geohashes, and writes a reusable local inventory.

Usage:
  METOFFICE_API_KEY=... python3 scripts/build_metoffice_inventory.py \
      --output data/data_estaciones_metoffice.json

For a more exhaustive scan, reduce the step and increase passes:
  METOFFICE_API_KEY=... python3 scripts/build_metoffice_inventory.py \
      --step-deg 0.25 --passes 4
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_files import METOFFICE_STATIONS_PATH

DEFAULT_BASE_URL = os.getenv(
    "METOFFICE_BASE_URL",
    "https://data.hub.api.metoffice.gov.uk",
).rstrip("/")
DEFAULT_OUTPUT = str(METOFFICE_STATIONS_PATH)
DEFAULT_CACHE = str(ROOT_DIR / "data" / "metoffice_nearest_cache.json")
DEFAULT_BBOX = (49.0, 61.1, -9.0, 2.2)  # south, north, west, east
DEFAULT_NEAREST_PATH = "/observation-land/1/nearest"
DEFAULT_OBSERVATIONS_PATH_TEMPLATE = "/observation-land/1/{geohash}"
DEFAULT_STATION_NAMES_URL = (
    "https://www.metoffice.gov.uk/research/climate/maps-and-data/uk-synoptic-and-climate-stations"
)
DEFAULT_STEP_DEG = 0.35
DEFAULT_PASSES = 2
DEFAULT_MAX_REQUESTS = 320
DEFAULT_ALLOWED_COUNTRIES = (
    "England",
    "Scotland",
    "Wales",
    "Northern Ireland",
    "Channel Islands",
    "British Isles",
    "Isle of Man",
)
DEFAULT_NAME_MATCH_MAX_KM = 12.0
DEFAULT_OFFICIAL_PROBE_TYPES = ("auto", "wind")

GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
METOFFICE_COORD_DECIMALS = 2
OFFSET_PATTERNS: Tuple[Tuple[float, float], ...] = (
    (0.0, 0.0),
    (0.5, 0.5),
    (0.5, 0.0),
    (0.0, 0.5),
    (0.25, 0.25),
    (0.75, 0.75),
    (0.25, 0.75),
    (0.75, 0.25),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_api_key(args: argparse.Namespace) -> str:
    return str(
        os.getenv("METEOLABX_METOFFICE_API_KEY", "")
        or os.getenv("METOFFICE_API_KEY", "")
    ).strip()


def _coord_decimal(value: float) -> Decimal:
    quant = Decimal("1").scaleb(-METOFFICE_COORD_DECIMALS)
    rounded = Decimal(str(float(value))).quantize(quant, rounding=ROUND_HALF_UP)
    return Decimal("0.00") if rounded == Decimal("-0.00") else rounded


def _coord_text(value: float) -> str:
    return f"{_coord_decimal(value):.{METOFFICE_COORD_DECIMALS}f}"


def _coord_float(value: float) -> float:
    return float(_coord_decimal(value))


def _cache_key(lat: float, lon: float) -> str:
    return f"nearest:{_coord_text(lat)},{_coord_text(lon)}"


def _observation_cache_key(geohash: str) -> str:
    return f"observations:{str(geohash or '').strip().lower()}"


def _legacy_cache_key(lat: float, lon: float) -> str:
    return f"nearest:{lat:.5f},{lon:.5f}"


def _load_cache(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    cache_path = Path(path)
    if not cache_path.exists():
        return {}
    try:
        payload = _load_json_file(cache_path)
    except Exception:
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("responses"), dict):
        return dict(payload["responses"])
    return {}


def _save_cache(path: Optional[str], responses: Dict[str, Any]) -> None:
    if not path:
        return
    payload = {
        "version": 1,
        "generated_at": _now_iso(),
        "responses": responses,
    }
    _save_json_file(Path(path), payload)


def _request_json(
    url: str,
    *,
    api_key: str,
    timeout: int,
    retries: int,
    retry_sleep: float,
) -> Any:
    last_error: Optional[BaseException] = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "MeteoLabx/1.0 (+https://meteolabx.com)",
    }
    if api_key:
        headers["apikey"] = api_key

    for attempt in range(retries + 1):
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                details = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {exc.code} for {url}: {details[:500]}") from exc
        except URLError as exc:
            last_error = exc
            if attempt >= retries:
                raise RuntimeError(f"Network error for {url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON for {url}: {exc}") from exc

        time.sleep(max(0.0, retry_sleep) * (attempt + 1))

    raise RuntimeError(f"Request failed for {url}: {last_error}")


def _request_text(
    url: str,
    *,
    timeout: int,
    retries: int,
    retry_sleep: float,
) -> str:
    last_error: Optional[BaseException] = None
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": "MeteoLabx/1.0 (+https://meteolabx.com)",
    }
    for attempt in range(retries + 1):
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                details = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {exc.code} for {url}: {details[:500]}") from exc
        except URLError as exc:
            last_error = exc
            if attempt >= retries:
                raise RuntimeError(f"Network error for {url}: {exc}") from exc

        time.sleep(max(0.0, retry_sleep) * (attempt + 1))

    raise RuntimeError(f"Request failed for {url}: {last_error}")


def _frange(start: float, stop: float, step: float) -> Iterator[float]:
    index = 0
    while True:
        value = start + index * step
        if value > stop + 1e-9:
            break
        yield round(value, 5)
        index += 1


def _grid_points(
    bbox: Tuple[float, float, float, float],
    *,
    step_deg: float,
    passes: int,
) -> Iterator[Tuple[float, float]]:
    south, north, west, east = bbox
    selected_offsets = OFFSET_PATTERNS[: max(1, min(int(passes), len(OFFSET_PATTERNS)))]
    seen: set[Tuple[float, float]] = set()

    for lat_offset, lon_offset in selected_offsets:
        start_lat = south + (lat_offset * step_deg)
        start_lon = west + (lon_offset * step_deg)
        for lat in _frange(start_lat, north, step_deg):
            for lon in _frange(start_lon, east, step_deg):
                point = (round(lat, 5), round(lon, 5))
                if point in seen:
                    continue
                seen.add(point)
                yield point


def decode_geohash(geohash: str) -> Tuple[float, float, float, float]:
    """
    Returns center latitude, center longitude, lat half-error and lon half-error.
    """
    lat_interval = [-90.0, 90.0]
    lon_interval = [-180.0, 180.0]
    even_bit = True

    for char in geohash.strip().lower():
        if char not in GEOHASH_BASE32:
            raise ValueError(f"Invalid geohash character: {char!r}")
        code = GEOHASH_BASE32.index(char)
        for mask in (16, 8, 4, 2, 1):
            if even_bit:
                midpoint = (lon_interval[0] + lon_interval[1]) / 2.0
                if code & mask:
                    lon_interval[0] = midpoint
                else:
                    lon_interval[1] = midpoint
            else:
                midpoint = (lat_interval[0] + lat_interval[1]) / 2.0
                if code & mask:
                    lat_interval[0] = midpoint
                else:
                    lat_interval[1] = midpoint
            even_bit = not even_bit

    lat = (lat_interval[0] + lat_interval[1]) / 2.0
    lon = (lon_interval[0] + lon_interval[1]) / 2.0
    lat_error = (lat_interval[1] - lat_interval[0]) / 2.0
    lon_error = (lon_interval[1] - lon_interval[0]) / 2.0
    return lat, lon, lat_error, lon_error


def _first_location(payload: Any) -> Optional[Dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and str(item.get("geohash", "")).strip():
                return item
        return None
    if isinstance(payload, dict):
        if str(payload.get("geohash", "")).strip():
            return payload
        for key in ("data", "features", "locations", "results"):
            child = payload.get(key)
            location = _first_location(child)
            if location:
                return location
    return None


def _observations_from_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "observations", "results"):
            child = payload.get(key)
            if isinstance(child, list):
                return [item for item in child if isinstance(item, dict)]
    return []


def _extract_observation_summary(payload: Any) -> Dict[str, Any]:
    observations = _observations_from_payload(payload)
    datetimes = [
        str(item.get("datetime", "")).strip()
        for item in observations
        if str(item.get("datetime", "")).strip()
    ]
    fields = sorted(
        {
            key
            for item in observations
            for key, value in item.items()
            if key != "datetime" and value is not None
        }
    )
    return {
        "observation_count": len(observations),
        "first_observation": min(datetimes) if datetimes else None,
        "last_observation": max(datetimes) if datetimes else None,
        "observed_fields": fields,
    }


def _normalize_station(
    item: Dict[str, Any],
    *,
    probe_lat: float,
    probe_lon: float,
    hit_count: int = 1,
) -> Dict[str, Any]:
    geohash = str(item.get("geohash", "")).strip().lower()
    lat, lon, lat_error, lon_error = decode_geohash(geohash)
    area = str(item.get("area", "")).strip()
    country = str(item.get("country", "")).strip()
    region = str(item.get("region", "")).strip()
    tz_name = str(item.get("olson_time_zone", "")).strip()

    return {
        "id": geohash,
        "source_id": geohash,
        "geohash": geohash,
        "name": area or geohash,
        "area": area or None,
        "region": region or None,
        "country": country or None,
        "country_code": "GB" if country in {"England", "Scotland", "Wales", "Northern Ireland"} else None,
        "lat": lat,
        "lon": lon,
        "geohash_lat_error": lat_error,
        "geohash_lon_error": lon_error,
        "elev": None,
        "altitude": None,
        "tz": tz_name or None,
        "olson_time_zone": tz_name or None,
        "active_now": True,
        "provider": "METOFFICE",
        "source": "Met Office Weather DataHub Land Observations",
        "inventory_method": "nearest_grid_scan",
        "probe_lat": probe_lat,
        "probe_lon": probe_lon,
        "hit_count": hit_count,
        "raw_nearest": item,
    }


def _normalize_allowed_countries(values: Iterable[str]) -> Optional[set[str]]:
    countries = {
        str(value or "").strip()
        for value in values
        if str(value or "").strip()
    }
    if not countries or any(value.lower() in {"*", "all"} for value in countries):
        return None
    return countries


def _station_country_allowed(station: Dict[str, Any], allowed_countries: Optional[set[str]]) -> bool:
    if allowed_countries is None:
        return True
    return str(station.get("country") or "").strip() in allowed_countries


def _official_location_country_allowed(location: Dict[str, Any], allowed_countries: Optional[set[str]]) -> bool:
    if allowed_countries is None:
        return True
    countries = _country_name_candidates(str(location.get("country") or ""))
    return any(country in allowed_countries for country in countries)


def _station_geohash(station: Dict[str, Any]) -> str:
    return str(station.get("geohash") or station.get("id") or station.get("source_id") or "").strip().lower()


def _station_official_name(station: Dict[str, Any]) -> str:
    return str(station.get("station_name") or station.get("metoffice_station_name") or "").strip()


def _station_name_key(value: str) -> str:
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def _station_base_display_name(station: Dict[str, Any]) -> str:
    geohash = _station_geohash(station)
    station_name = _station_official_name(station)
    if station_name:
        return station_name
    area = str(station.get("area") or station.get("name") or geohash).strip()
    country = str(station.get("country") or "").strip()
    if country and area and country not in area:
        return f"{area}, {country}"
    return area or geohash


def _apply_display_names(stations: List[Dict[str, Any]]) -> None:
    base_names = [_station_base_display_name(station) for station in stations]
    name_counts = Counter(base_names)
    for station, base_name in zip(stations, base_names):
        geohash = _station_geohash(station)
        if name_counts[base_name] > 1 and geohash:
            station["display_name"] = f"{geohash} - {base_name}"
        else:
            station["display_name"] = base_name


def _drop_unmatched_station_names(stations: List[Dict[str, Any]]) -> int:
    matched = [
        station
        for station in stations
        if _station_official_name(station)
    ]
    removed = len(stations) - len(matched)
    stations[:] = matched
    return removed


def _sort_stations(stations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        stations,
        key=lambda station: (
            str(station.get("country") or ""),
            str(station.get("region") or ""),
            str(station.get("area") or ""),
            _station_geohash(station),
        ),
    )


def _normalize_probe_types(values: Iterable[str]) -> Optional[set[str]]:
    types = {
        str(value or "").strip().lower()
        for value in values
        if str(value or "").strip()
    }
    if not types or any(value in {"*", "all"} for value in types):
        return None
    return types


def _official_probe_candidates(
    named_locations: List[Dict[str, Any]],
    stations: List[Dict[str, Any]],
    *,
    allowed_types: Optional[set[str]],
    allowed_countries: Optional[set[str]],
) -> List[Dict[str, Any]]:
    existing_names = {
        _station_official_name(station)
        for station in stations
        if _station_official_name(station)
    }
    existing_name_keys = {
        _station_name_key(name)
        for name in existing_names
        if _station_name_key(name)
    }
    candidates: List[Dict[str, Any]] = []
    for location in named_locations:
        name = str(location.get("name") or "").strip()
        name_key = _station_name_key(name)
        station_type = str(location.get("station_type") or "").strip().lower()
        if not name or name in existing_names or name_key in existing_name_keys:
            continue
        if allowed_types is not None and station_type not in allowed_types:
            continue
        if not _official_location_country_allowed(location, allowed_countries):
            continue
        candidates.append(location)
    return sorted(
        candidates,
        key=lambda item: (
            str(item.get("station_type") or ""),
            str(item.get("country") or ""),
            str(item.get("name") or ""),
        ),
    )


def _apply_official_location_to_station(
    station: Dict[str, Any],
    location: Dict[str, Any],
    distance_km: float,
) -> None:
    station_name = str(location.get("name") or "").strip()
    station["station_name"] = station_name
    station["metoffice_station_name"] = station_name
    station["metoffice_station_type"] = str(location.get("station_type") or "").strip() or None
    station["metoffice_station_id"] = location.get("station_id")
    station["name_match_distance_km"] = round(float(distance_km), 3)
    if location.get("altitude_m") not in (None, ""):
        station["elev"] = location.get("altitude_m")
        station["altitude"] = location.get("altitude_m")


def _extract_balanced_json(text: str, object_start: int) -> str:
    in_string = False
    escape_next = False
    depth = 0
    for index, char in enumerate(text[object_start:], start=object_start):
        if in_string:
            if escape_next:
                escape_next = False
            elif char == "\\":
                escape_next = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[object_start : index + 1]
    raise RuntimeError("No se encontró el cierre del objeto JSON de estaciones Met Office.")


def _extract_synoptic_station_locations(page_html: str) -> List[Dict[str, Any]]:
    marker = "locations:"
    marker_index = str(page_html or "").find(marker)
    if marker_index < 0:
        return []
    object_start = page_html.find("{", marker_index)
    if object_start < 0:
        return []
    payload = json.loads(_extract_balanced_json(page_html, object_start))
    features = payload.get("features", [])
    locations: List[Dict[str, Any]] = []
    if not isinstance(features, list):
        return locations
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        coords = geometry.get("coordinates", {})
        if not isinstance(props, dict) or not isinstance(coords, dict):
            continue
        name = str(props.get("name") or "").strip()
        country = str(props.get("country") or "").strip()
        try:
            lat = float(coords.get("first"))
            lon = float(coords.get("second"))
        except (TypeError, ValueError):
            continue
        if not name or lat != lat or lon != lon:
            continue
        locations.append(
            {
                "name": name,
                "country": country,
                "lat": lat,
                "lon": lon,
                "station_type": str(props.get("type") or "").strip(),
                "station_id": props.get("id"),
                "altitude_m": props.get("alt"),
            }
        )
    return locations


def _fetch_synoptic_station_locations(args: argparse.Namespace) -> List[Dict[str, Any]]:
    url = str(args.station_names_url or "").strip()
    if not url:
        return []
    html_text = _request_text(
        url,
        timeout=args.timeout,
        retries=args.retries,
        retry_sleep=args.retry_sleep,
    )
    return _extract_synoptic_station_locations(html_text)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * radius_km * math.asin(min(1.0, math.sqrt(a)))


def _country_name_candidates(country: str) -> set[str]:
    country = str(country or "").strip()
    candidates = {country} if country else set()
    if country in {"Channel Islands", "Isle of Man"}:
        candidates.add("British Isles")
    if country == "British Isles":
        candidates.update({"Channel Islands", "Isle of Man"})
    return {value for value in candidates if value}


def _apply_named_station_matches(
    stations: List[Dict[str, Any]],
    named_locations: List[Dict[str, Any]],
    *,
    max_distance_km: float,
) -> int:
    if not named_locations:
        return 0
    matched = 0
    max_distance = max(0.0, float(max_distance_km))
    for station in stations:
        try:
            lat = float(station.get("lat"))
            lon = float(station.get("lon"))
        except (TypeError, ValueError):
            continue
        countries = _country_name_candidates(str(station.get("country") or ""))
        candidates = [
            item
            for item in named_locations
            if not countries or str(item.get("country") or "").strip() in countries
        ]
        if not candidates:
            candidates = named_locations
        best = min(
            candidates,
            key=lambda item: _haversine_km(lat, lon, float(item["lat"]), float(item["lon"])),
        )
        distance_km = _haversine_km(lat, lon, float(best["lat"]), float(best["lon"]))
        if distance_km > max_distance:
            continue
        station["station_name"] = str(best.get("name") or "").strip()
        station["metoffice_station_name"] = station["station_name"]
        station["metoffice_station_type"] = str(best.get("station_type") or "").strip() or None
        station["metoffice_station_id"] = best.get("station_id")
        station["name_match_distance_km"] = round(distance_km, 3)
        matched += 1
    return matched


def _merge_station(existing: Dict[str, Any], item: Dict[str, Any], *, probe_lat: float, probe_lon: float) -> None:
    existing["hit_count"] = int(existing.get("hit_count", 0) or 0) + 1
    for key in ("area", "region", "country", "olson_time_zone"):
        if not existing.get(key) and item.get(key):
            existing[key] = item.get(key)
    if not existing.get("tz") and item.get("olson_time_zone"):
        existing["tz"] = item.get("olson_time_zone")
    existing["last_probe_lat"] = probe_lat
    existing["last_probe_lon"] = probe_lon


def _fetch_nearest(
    lat: float,
    lon: float,
    *,
    base_url: str,
    nearest_path: str,
    api_key: str,
    lat_param: str,
    lon_param: str,
    timeout: int,
    retries: int,
    retry_sleep: float,
) -> Any:
    url = _join_url(base_url, nearest_path)
    url = f"{url}?{urlencode({lat_param: _coord_text(lat), lon_param: _coord_text(lon)})}"
    return _request_json(url, api_key=api_key, timeout=timeout, retries=retries, retry_sleep=retry_sleep)


def _fetch_observations(
    geohash: str,
    *,
    base_url: str,
    observations_path_template: str,
    api_key: str,
    timeout: int,
    retries: int,
    retry_sleep: float,
) -> Any:
    path = observations_path_template.format(geohash=geohash)
    return _request_json(
        _join_url(base_url, path),
        api_key=api_key,
        timeout=timeout,
        retries=retries,
        retry_sleep=retry_sleep,
    )


def _load_seed_inventory(path: str) -> List[Dict[str, Any]]:
    path_text = str(path or "").strip()
    if not path_text:
        return []
    try:
        payload = _load_json_file(Path(path_text))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [dict(item) for item in payload if isinstance(item, dict) and _station_geohash(item)]


def _remaining_request_budget(max_api_requests: Optional[int], requests_made: int) -> Optional[int]:
    if max_api_requests is None:
        return None
    return max(0, int(max_api_requests) - int(requests_made))


def _probe_official_missing_coordinates(
    stations_by_geohash: Dict[str, Dict[str, Any]],
    named_locations: List[Dict[str, Any]],
    *,
    cache: Dict[str, Any],
    args: argparse.Namespace,
    api_key: str,
    allowed_countries: Optional[set[str]],
    max_api_requests: Optional[int],
    requests_made: int,
) -> Tuple[int, Dict[str, int]]:
    stations = _sort_stations(stations_by_geohash.values())
    allowed_types = _normalize_probe_types(args.official_probe_types)
    candidates = _official_probe_candidates(
        named_locations,
        stations,
        allowed_types=allowed_types,
        allowed_countries=allowed_countries,
    )
    limit = max(0, int(args.official_probe_limit or 0))
    summary = {
        "candidates": len(candidates),
        "attempted": 0,
        "api_calls": 0,
        "cache_hits": 0,
        "verification_api_calls": 0,
        "verification_cache_hits": 0,
        "added": 0,
        "updated_existing": 0,
        "already_named": 0,
        "duplicate_geohash": 0,
        "no_nearest": 0,
        "too_far": 0,
        "too_far_existing_name": 0,
        "too_far_existing_geohash": 0,
        "too_far_new_geohash": 0,
        "country_filtered": 0,
        "no_observations": 0,
        "skipped_budget": 0,
    }
    if limit <= 0:
        return requests_made, summary
    known_name_keys = {
        _station_name_key(_station_official_name(station))
        for station in stations_by_geohash.values()
        if _station_name_key(_station_official_name(station))
    }

    for location in candidates[:limit]:
        official_name = str(location.get("name") or "").strip()
        official_name_key = _station_name_key(official_name)
        if official_name_key and official_name_key in known_name_keys:
            summary["already_named"] += 1
            continue

        budget = _remaining_request_budget(max_api_requests, requests_made)
        if budget is not None and budget <= 0:
            summary["skipped_budget"] += 1
            break

        query_lat = _coord_float(float(location["lat"]))
        query_lon = _coord_float(float(location["lon"]))
        key = _cache_key(query_lat, query_lon)
        if key in cache:
            payload = cache[key]
            summary["cache_hits"] += 1
        else:
            payload = _fetch_nearest(
                query_lat,
                query_lon,
                base_url=args.base_url,
                nearest_path=args.nearest_path,
                api_key=api_key,
                lat_param=args.lat_param,
                lon_param=args.lon_param,
                timeout=args.timeout,
                retries=args.retries,
                retry_sleep=args.retry_sleep,
            )
            cache[key] = payload
            requests_made += 1
            summary["api_calls"] += 1
            if args.sleep > 0:
                time.sleep(args.sleep)

        summary["attempted"] += 1
        item = _first_location(payload)
        if not item:
            summary["no_nearest"] += 1
            continue

        geohash = str(item.get("geohash", "")).strip().lower()
        if not geohash:
            summary["no_nearest"] += 1
            continue

        probe_station = _normalize_station(item, probe_lat=query_lat, probe_lon=query_lon)
        if not _station_country_allowed(probe_station, allowed_countries):
            summary["country_filtered"] += 1
            continue
        distance_km = _haversine_km(
            float(probe_station["lat"]),
            float(probe_station["lon"]),
            float(location["lat"]),
            float(location["lon"]),
        )
        existing = stations_by_geohash.get(geohash)
        existing_name = _station_official_name(existing) if isinstance(existing, dict) else ""
        if distance_km > max(0.0, float(args.name_match_max_km)):
            summary["too_far"] += 1
            if existing_name and _station_name_key(existing_name) == official_name_key:
                summary["too_far_existing_name"] += 1
            elif existing:
                summary["too_far_existing_geohash"] += 1
            else:
                summary["too_far_new_geohash"] += 1
            continue

        if existing_name and existing_name == official_name:
            summary["already_named"] += 1
            continue
        if existing_name and existing_name != official_name:
            summary["duplicate_geohash"] += 1
            continue

        target_station = existing if isinstance(existing, dict) else probe_station
        if args.official_probe_verify:
            obs_key = _observation_cache_key(geohash)
            if obs_key in cache:
                obs_payload = cache[obs_key]
                summary["verification_cache_hits"] += 1
            else:
                budget = _remaining_request_budget(max_api_requests, requests_made)
                if budget is not None and budget <= 0:
                    summary["skipped_budget"] += 1
                    continue
                obs_payload = _fetch_observations(
                    geohash,
                    base_url=args.base_url,
                    observations_path_template=args.observations_path_template,
                    api_key=api_key,
                    timeout=args.timeout,
                    retries=args.retries,
                    retry_sleep=args.retry_sleep,
                )
                cache[obs_key] = obs_payload
                requests_made += 1
                summary["verification_api_calls"] += 1
                if args.sleep > 0:
                    time.sleep(args.sleep)
            observation_summary = _extract_observation_summary(obs_payload)
            if int(observation_summary.get("observation_count") or 0) <= 0:
                summary["no_observations"] += 1
                continue
            target_station.update(observation_summary)

        _apply_official_location_to_station(target_station, location, distance_km)
        target_station["inventory_method"] = (
            "official_station_coordinate_probe"
            if not existing
            else str(target_station.get("inventory_method") or "nearest_grid_scan")
        )
        target_station["official_probe_lat"] = query_lat
        target_station["official_probe_lon"] = query_lon
        stations_by_geohash[geohash] = target_station
        if existing:
            summary["updated_existing"] += 1
        else:
            summary["added"] += 1
        if official_name_key:
            known_name_keys.add(official_name_key)

    return requests_made, summary


def build_inventory(args: argparse.Namespace) -> List[Dict[str, Any]]:
    api_key = _resolve_api_key(args)
    if not api_key:
        raise RuntimeError("Falta METEOLABX_METOFFICE_API_KEY en el entorno.")

    bbox = tuple(float(value) for value in args.bbox)
    if len(bbox) != 4:
        raise RuntimeError("--bbox necesita 4 valores: south north west east")

    cache = _load_cache(None if args.no_cache else args.cache_path)
    stations_by_geohash: Dict[str, Dict[str, Any]] = {}
    requests_made = 0
    cache_hits = 0
    seed_path = str(args.seed_inventory or "").strip()
    if not seed_path and args.skip_grid_scan:
        seed_path = str(args.output or "").strip()
    if seed_path:
        seed_stations = _load_seed_inventory(seed_path)
        for station in seed_stations:
            stations_by_geohash[_station_geohash(station)] = station
        print(f"Seed inventory loaded: {len(seed_stations)} stations from {seed_path}")

    points = [] if args.skip_grid_scan else list(_grid_points(bbox, step_deg=args.step_deg, passes=args.passes))
    total_points = len(points)

    print("Met Office Land Observations inventory scan")
    max_api_requests = None if args.max_requests is not None and args.max_requests <= 0 else args.max_requests

    if args.skip_grid_scan:
        print("Grid scan: skipped")
    else:
        print(f"Grid points: {total_points} | step: {args.step_deg} | passes: {args.passes}")
    if max_api_requests is None:
        print("API request cap: disabled")
    else:
        print(f"API request cap: {max_api_requests} calls")
    print(f"Bounding box: south={bbox[0]}, north={bbox[1]}, west={bbox[2]}, east={bbox[3]}")

    for index, (lat, lon) in enumerate(points, start=1):
        query_lat = _coord_float(lat)
        query_lon = _coord_float(lon)
        key = _cache_key(query_lat, query_lon)
        legacy_key = _legacy_cache_key(lat, lon)
        if key in cache:
            payload = cache[key]
            cache_hits += 1
        elif legacy_key in cache:
            payload = cache[legacy_key]
            cache[key] = payload
            cache_hits += 1
        else:
            if max_api_requests is not None and requests_made >= max_api_requests:
                print(f"Max requests reached: {max_api_requests}")
                break
            payload = _fetch_nearest(
                query_lat,
                query_lon,
                base_url=args.base_url,
                nearest_path=args.nearest_path,
                api_key=api_key,
                lat_param=args.lat_param,
                lon_param=args.lon_param,
                timeout=args.timeout,
                retries=args.retries,
                retry_sleep=args.retry_sleep,
            )
            cache[key] = payload
            requests_made += 1
            if args.sleep > 0:
                time.sleep(args.sleep)

        item = _first_location(payload)
        if item:
            geohash = str(item.get("geohash", "")).strip().lower()
            if geohash:
                if geohash in stations_by_geohash:
                    _merge_station(stations_by_geohash[geohash], item, probe_lat=query_lat, probe_lon=query_lon)
                else:
                    stations_by_geohash[geohash] = _normalize_station(
                        item,
                        probe_lat=query_lat,
                        probe_lon=query_lon,
                    )

        if index % args.progress_every == 0:
            print(
                f"{index}/{total_points} points | stations={len(stations_by_geohash)} "
                f"| api_calls={requests_made} | cache_hits={cache_hits}",
                flush=True,
            )
        if not args.no_cache and requests_made and requests_made % args.save_every == 0:
            _save_cache(args.cache_path, cache)

    stations = _sort_stations(stations_by_geohash.values())
    allowed_countries = _normalize_allowed_countries(args.allowed_countries)
    stations = [
        station
        for station in stations
        if _station_country_allowed(station, allowed_countries)
    ]
    named_locations: List[Dict[str, Any]] = []
    if not args.no_name_enrichment:
        try:
            named_locations = _fetch_synoptic_station_locations(args)
            matched_names = _apply_named_station_matches(
                stations,
                named_locations,
                max_distance_km=args.name_match_max_km,
            )
            print(f"Station name enrichment: {matched_names}/{len(stations)} matched")
            if not args.keep_unmatched_names:
                removed_names = _drop_unmatched_station_names(stations)
                print(f"Unmatched station-name fallbacks removed: {removed_names}")
        except Exception as exc:
            print(f"Station name enrichment skipped: {exc}")
    stations_by_geohash = {
        _station_geohash(station): station
        for station in stations
        if _station_geohash(station)
    }
    if args.probe_official_missing:
        if args.no_name_enrichment:
            print("Official coordinate probe skipped: --no-name-enrichment disables official station list.")
        elif not named_locations:
            print("Official coordinate probe skipped: no official station locations available.")
        else:
            requests_made, probe_summary = _probe_official_missing_coordinates(
                stations_by_geohash,
                named_locations,
                cache=cache,
                args=args,
                api_key=api_key,
                allowed_countries=allowed_countries,
                max_api_requests=max_api_requests,
                requests_made=requests_made,
            )
            print(
                "Official coordinate probe: "
                f"candidates={probe_summary['candidates']} "
                f"attempted={probe_summary['attempted']} "
                f"added={probe_summary['added']} "
                f"updated={probe_summary['updated_existing']} "
                f"nearest_calls={probe_summary['api_calls']} "
                f"nearest_cache_hits={probe_summary['cache_hits']} "
                f"verify_calls={probe_summary['verification_api_calls']} "
                f"verify_cache_hits={probe_summary['verification_cache_hits']} "
                f"already_named={probe_summary['already_named']} "
                f"duplicate_geohash={probe_summary['duplicate_geohash']} "
                f"too_far={probe_summary['too_far']} "
                f"too_far_existing_name={probe_summary['too_far_existing_name']} "
                f"too_far_existing_geohash={probe_summary['too_far_existing_geohash']} "
                f"too_far_new_geohash={probe_summary['too_far_new_geohash']} "
                f"country_filtered={probe_summary['country_filtered']} "
                f"no_observations={probe_summary['no_observations']} "
                f"skipped_budget={probe_summary['skipped_budget']}"
            )
            stations = _sort_stations(stations_by_geohash.values())
    _apply_display_names(stations)

    if args.verify:
        print(f"Verifying observations for {len(stations)} geohashes...")
        for index, station in enumerate(stations, start=1):
            if max_api_requests is not None and requests_made >= max_api_requests:
                print(f"Max requests reached before verification completed: {max_api_requests}")
                break
            geohash = str(station.get("geohash", "")).strip()
            payload = _fetch_observations(
                geohash,
                base_url=args.base_url,
                observations_path_template=args.observations_path_template,
                api_key=api_key,
                timeout=args.timeout,
                retries=args.retries,
                retry_sleep=args.retry_sleep,
            )
            requests_made += 1
            station.update(_extract_observation_summary(payload))
            if args.sleep > 0:
                time.sleep(args.sleep)
            if index % args.progress_every == 0:
                print(f"Verified {index}/{len(stations)}", flush=True)

    if not args.no_cache:
        _save_cache(args.cache_path, cache)
    return stations


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a reconstructed Met Office Land Observations station inventory."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"API base URL. Default: {DEFAULT_BASE_URL}")
    parser.add_argument("--nearest-path", default=DEFAULT_NEAREST_PATH, help="Nearest endpoint path.")
    parser.add_argument(
        "--observations-path-template",
        default=DEFAULT_OBSERVATIONS_PATH_TEMPLATE,
        help="Observation endpoint path template, using {geohash}.",
    )
    parser.add_argument("--lat-param", default="lat", help="Latitude query parameter name.")
    parser.add_argument("--lon-param", default="lon", help="Longitude query parameter name.")
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        default=DEFAULT_BBOX,
        metavar=("SOUTH", "NORTH", "WEST", "EAST"),
        help="Scan bounding box. Defaults to UK plus islands.",
    )
    parser.add_argument(
        "--allowed-countries",
        nargs="*",
        default=list(DEFAULT_ALLOWED_COUNTRIES),
        help=(
            "Countries kept in the reconstructed inventory. Defaults to UK nations "
            "plus Channel Islands. Use 'all' to keep spillover nearest results."
        ),
    )
    parser.add_argument(
        "--station-names-url",
        default=DEFAULT_STATION_NAMES_URL,
        help="Official Met Office station list page used to enrich geohashes with human station names.",
    )
    parser.add_argument(
        "--no-name-enrichment",
        action="store_true",
        help="Do not fetch/match the official Met Office station names page.",
    )
    parser.add_argument(
        "--keep-unmatched-names",
        action="store_true",
        help=(
            "Keep geohashes that return data but could not be matched to the "
            "official named station list."
        ),
    )
    parser.add_argument(
        "--name-match-max-km",
        type=float,
        default=DEFAULT_NAME_MATCH_MAX_KM,
        help=f"Maximum geohash-to-station-name match distance. Default: {DEFAULT_NAME_MATCH_MAX_KM} km.",
    )
    parser.add_argument("--step-deg", type=float, default=DEFAULT_STEP_DEG, help="Grid spacing in degrees.")
    parser.add_argument("--passes", type=int, default=DEFAULT_PASSES, help="Number of offset grid passes.")
    parser.add_argument(
        "--skip-grid-scan",
        action="store_true",
        help="Skip the UK grid scan. Useful with --seed-inventory and --probe-official-missing.",
    )
    parser.add_argument(
        "--seed-inventory",
        default="",
        help=(
            "Existing inventory JSON used as the starting point before scanning/probing. "
            "When --skip-grid-scan is set and this is omitted, --output is used."
        ),
    )
    parser.add_argument(
        "--probe-official-missing",
        action="store_true",
        help=(
            "Try missing official Met Office station coordinates with /nearest. "
            "Requires official name enrichment and obeys --official-probe-limit and --max-requests."
        ),
    )
    parser.add_argument(
        "--official-probe-limit",
        type=int,
        default=0,
        help=(
            "Maximum missing official locations to attempt in this run. Default 0 prints the "
            "candidate count without spending Met Office API calls."
        ),
    )
    parser.add_argument(
        "--official-probe-types",
        nargs="*",
        default=list(DEFAULT_OFFICIAL_PROBE_TYPES),
        help=(
            "Official station types to probe. Default: auto wind. "
            "Use 'all' to include manual climate stations too."
        ),
    )
    parser.add_argument(
        "--official-probe-verify",
        action="store_true",
        help=(
            "Fetch observations for newly found geohashes before adding them. "
            "This can add one extra API call per new geohash."
        ),
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=DEFAULT_MAX_REQUESTS,
        help=(
            "Stop after this many API calls. Nearest cache hits do not count; verification calls do. "
            f"Default: {DEFAULT_MAX_REQUESTS}, under the 360/day free tier. Use 0 to disable."
        ),
    )
    parser.add_argument("--sleep", type=float, default=0.05, help="Sleep between uncached requests.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Retries for 429/5xx/network errors.")
    parser.add_argument("--retry-sleep", type=float, default=2.0, help="Base retry sleep in seconds.")
    parser.add_argument("--cache-path", default=DEFAULT_CACHE, help="Nearest response cache path.")
    parser.add_argument("--no-cache", action="store_true", help="Disable cache reads and writes.")
    parser.add_argument("--save-every", type=int, default=50, help="Save cache every N new requests.")
    parser.add_argument("--progress-every", type=int, default=100, help="Print progress every N points.")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Fetch observations for each discovered geohash. These calls count against --max-requests.",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"Output JSON path. Default: {DEFAULT_OUTPUT}")
    parser.add_argument("--check-config", action="store_true", help="Validate local config without API calls.")
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    if args.check_config:
        api_key = _resolve_api_key(args)
        bbox = tuple(float(value) for value in args.bbox)
        points = [] if args.skip_grid_scan else list(_grid_points(bbox, step_deg=args.step_deg, passes=args.passes))
        print("Met Office config check")
        print(f"API key: {'found' if api_key else 'missing'}")
        print(f"Base URL: {args.base_url}")
        print(f"Nearest URL: {_join_url(args.base_url, args.nearest_path)}")
        sample_params = urlencode({args.lat_param: _coord_text(bbox[0]), args.lon_param: _coord_text(bbox[2])})
        print(f"Sample nearest request: {_join_url(args.base_url, args.nearest_path)}?{sample_params}")
        print(f"Grid points: {len(points)}")
        print(f"Max API calls per run: {args.max_requests}")
        print(f"Skip grid scan: {bool(args.skip_grid_scan)}")
        print(f"Seed inventory: {args.seed_inventory or (args.output if args.skip_grid_scan else '')}")
        print(f"Probe official missing: {bool(args.probe_official_missing)}")
        print(f"Official probe limit: {args.official_probe_limit}")
        print(f"Official probe types: {', '.join(args.official_probe_types)}")
        print(f"Official probe verifies observations: {bool(args.official_probe_verify)}")
        print(f"Cache path: {args.cache_path}")
        print(f"Output path: {args.output}")
        return 0 if api_key else 2

    stations = build_inventory(args)
    output_path = Path(args.output).resolve()
    _save_json_file(output_path, stations)

    print(f"Inventory saved: {output_path}")
    print(f"Total reconstructed stations: {len(stations)}")
    print("Note: this is a reconstructed inventory from /nearest, not an official catalog endpoint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
