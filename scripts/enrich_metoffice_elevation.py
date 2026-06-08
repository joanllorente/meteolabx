#!/usr/bin/env python3
"""
Añade elevación Open-Meteo al inventario existente de estaciones Met Office.

Ejemplo:
  python3 scripts/enrich_metoffice_elevation.py --overwrite
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT_DIR / "data" / "data_estaciones_metoffice.json"
DEFAULT_ENDPOINT = "https://api.open-meteo.com/v1/elevation"

Station = Dict[str, Any]
Coordinate = Tuple[float, float]
ElevationFetcher = Callable[[Sequence[Coordinate]], Sequence[float]]


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return float("nan")
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _is_finite(value: Any) -> bool:
    number = _safe_float(value)
    return math.isfinite(number)


def _station_coordinate(station: Station) -> Optional[Coordinate]:
    lat = _safe_float(station.get("lat"))
    lon = _safe_float(station.get("lon"))
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    return lat, lon


def _station_has_elevation(station: Station) -> bool:
    return _is_finite(station.get("elev")) or _is_finite(station.get("altitude"))


def _normalize_elevation(value: Any) -> Optional[Union[float, int]]:
    elevation = _safe_float(value)
    if not math.isfinite(elevation):
        return None
    rounded = round(elevation, 1)
    return int(rounded) if float(rounded).is_integer() else rounded


def _request_json(url: str, *, timeout: int, retries: int, retry_sleep: float) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": "MeteoLabx/1.0 (+https://meteolabx.com)",
    }
    last_error: Optional[BaseException] = None
    for attempt in range(int(retries) + 1):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= int(retries):
                details = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {exc.code} for {url}: {details[:500]}") from exc
        except URLError as exc:
            last_error = exc
            if attempt >= int(retries):
                raise RuntimeError(f"Network error for {url}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON for {url}: {exc}") from exc
        time.sleep(max(0.0, float(retry_sleep)) * (attempt + 1))
    raise RuntimeError(f"Request failed for {url}: {last_error}")


def _fetch_open_meteo_elevation_batch(
    coords: Sequence[Coordinate],
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    timeout: int = 30,
    retries: int = 2,
    retry_sleep: float = 1.0,
) -> List[float]:
    if not coords:
        return []
    params = urlencode(
        {
            "latitude": ",".join(f"{lat:.5f}" for lat, _lon in coords),
            "longitude": ",".join(f"{lon:.5f}" for _lat, lon in coords),
        }
    )
    payload = _request_json(
        f"{str(endpoint).rstrip('?')}" + f"?{params}",
        timeout=timeout,
        retries=retries,
        retry_sleep=retry_sleep,
    )
    values = payload.get("elevation") if isinstance(payload, dict) else None
    if isinstance(values, (int, float)):
        values = [values]
    if not isinstance(values, list):
        raise RuntimeError("Open-Meteo elevation response does not contain an elevation list.")
    if len(values) < len(coords):
        raise RuntimeError(
            f"Open-Meteo returned {len(values)} elevations for {len(coords)} coordinates."
        )
    return [_safe_float(value) for value in values[: len(coords)]]


def _fetch_open_meteo_elevation(
    coords: Sequence[Coordinate],
    *,
    endpoint: str,
    timeout: int,
    retries: int,
    retry_sleep: float,
) -> List[float]:
    try:
        return _fetch_open_meteo_elevation_batch(
            coords,
            endpoint=endpoint,
            timeout=timeout,
            retries=retries,
            retry_sleep=retry_sleep,
        )
    except Exception:
        if len(coords) <= 1:
            raise
    elevations: List[float] = []
    for coord in coords:
        elevations.extend(
            _fetch_open_meteo_elevation_batch(
                [coord],
                endpoint=endpoint,
                timeout=timeout,
                retries=retries,
                retry_sleep=retry_sleep,
            )
        )
    return elevations


def _chunks(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    chunk_size = max(1, int(size))
    for start in range(0, len(values), chunk_size):
        yield values[start : start + chunk_size]


def enrich_stations_with_elevation(
    stations: List[Station],
    *,
    fetcher: ElevationFetcher,
    batch_size: int = 50,
    overwrite: bool = False,
    sleep_seconds: float = 0.0,
) -> Dict[str, int]:
    stats = {
        "total": len(stations),
        "targets": 0,
        "updated": 0,
        "skipped_existing": 0,
        "skipped_coordinates": 0,
        "missing_elevation": 0,
    }
    targets: List[Tuple[Station, Coordinate]] = []
    for station in stations:
        if _station_has_elevation(station) and not overwrite:
            stats["skipped_existing"] += 1
            continue
        coord = _station_coordinate(station)
        if coord is None:
            stats["skipped_coordinates"] += 1
            continue
        targets.append((station, coord))

    stats["targets"] = len(targets)
    for batch in _chunks(targets, batch_size):
        coords = [coord for _station, coord in batch]
        elevations = list(fetcher(coords))
        for (station, _coord), elevation in zip(batch, elevations):
            normalized = _normalize_elevation(elevation)
            if normalized is None:
                stats["missing_elevation"] += 1
                continue
            station["elev"] = normalized
            station["altitude"] = normalized
            station["elevation_source"] = "open-meteo"
            station.pop("elevation_updated_at", None)
            stats["updated"] += 1
        if sleep_seconds > 0:
            time.sleep(float(sleep_seconds))
    return stats


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Añade elevación Open-Meteo al inventario Met Office existente.")
    parser.add_argument("--input", default=str(DEFAULT_INVENTORY), help="Inventario Met Office de entrada.")
    parser.add_argument("--output", default="", help="Inventario de salida. Si se omite, sobreescribe --input.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Endpoint de elevación Open-Meteo.")
    parser.add_argument("--batch-size", type=int, default=50, help="Coordenadas por llamada a Open-Meteo.")
    parser.add_argument("--overwrite", action="store_true", help="Recalcular también estaciones que ya tienen elevación.")
    parser.add_argument("--dry-run", action="store_true", help="Consulta y resume, pero no escribe el JSON.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Pausa entre lotes.")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout HTTP en segundos.")
    parser.add_argument("--retries", type=int, default=2, help="Reintentos para errores temporales.")
    parser.add_argument("--retry-sleep", type=float, default=1.0, help="Pausa base entre reintentos.")
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve() if args.output else input_path
    stations = _load_json(input_path)
    if not isinstance(stations, list):
        raise RuntimeError(f"El inventario no es una lista JSON: {input_path}")

    def fetcher(coords: Sequence[Coordinate]) -> Sequence[float]:
        return _fetch_open_meteo_elevation(
            coords,
            endpoint=args.endpoint,
            timeout=args.timeout,
            retries=args.retries,
            retry_sleep=args.retry_sleep,
        )

    stats = enrich_stations_with_elevation(
        stations,
        fetcher=fetcher,
        batch_size=args.batch_size,
        overwrite=bool(args.overwrite),
        sleep_seconds=float(args.sleep),
    )
    print(
        "Open-Meteo elevation enrichment: "
        f"total={stats['total']} "
        f"targets={stats['targets']} "
        f"updated={stats['updated']} "
        f"skipped_existing={stats['skipped_existing']} "
        f"skipped_coordinates={stats['skipped_coordinates']} "
        f"missing_elevation={stats['missing_elevation']}"
    )

    if args.dry_run:
        print("Dry run: inventory not written.")
        return 0

    _save_json(output_path, stations)
    print(f"Inventory saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
