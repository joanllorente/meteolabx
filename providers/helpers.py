"""
Helpers compartidos para catálogos locales de estaciones.
"""

from __future__ import annotations

import heapq
import math
from typing import Any, Callable, Iterable, List, Sequence, Tuple, TypeVar

from utils.geo import haversine_distance


RecordT = TypeVar("RecordT")

_SEARCH_RADII_KM: Sequence[float | None] = (40.0, 80.0, 160.0, 320.0, 640.0, 1200.0, None)


def is_valid_coordinate(lat: float, lon: float) -> bool:
    return -90.0 <= float(lat) <= 90.0 and -180.0 <= float(lon) <= 180.0


def maybe_swap_coordinates(lat: float, lon: float) -> tuple[float, float] | None:
    lat = float(lat)
    lon = float(lon)
    if is_valid_coordinate(lat, lon):
        return lat, lon
    if is_valid_coordinate(lon, lat):
        return lon, lat
    return None


def _radius_bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    lat_delta = float(radius_km) / 111.0
    cos_lat = math.cos(math.radians(float(lat)))
    lon_delta = 180.0 if abs(cos_lat) < 1e-6 else float(radius_km) / (111.320 * abs(cos_lat))
    return lat - lat_delta, lat + lat_delta, lon - lon_delta, lon + lon_delta


def _within_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lat, max_lat, min_lon, max_lon = bbox
    if not (min_lat <= lat <= max_lat):
        return False
    if min_lon <= max_lon:
        return min_lon <= lon <= max_lon
    return lon >= min_lon or lon <= max_lon


def nearest_records(
    lat: float,
    lon: float,
    records: Iterable[RecordT],
    *,
    get_coords: Callable[[RecordT], tuple[float, float] | None],
    max_results: int = 5,
) -> List[Tuple[RecordT, float]]:
    """
    Devuelve los registros mas cercanos, aplicando un prefiltrado espacial
    progresivo antes de caer al escaneo completo.
    """
    lat = float(lat)
    lon = float(lon)
    limit = max(1, int(max_results))
    all_records = records if isinstance(records, list) else list(records)
    if not all_records:
        return []

    best: list[tuple[float, RecordT]] = []
    seen_ids: set[int] = set()
    search_radii = (None,) if limit >= len(all_records) else _SEARCH_RADII_KM

    for radius_km in search_radii:
        bbox = _radius_bbox(lat, lon, radius_km) if radius_km is not None else None
        candidates: list[tuple[float, RecordT]] = []

        for record in all_records:
            try:
                coords = get_coords(record)
            except Exception:
                continue
            if coords is None:
                continue
            cand_lat, cand_lon = coords
            if bbox is not None and not _within_bbox(cand_lat, cand_lon, bbox):
                continue
            try:
                distance = float(haversine_distance(lat, lon, cand_lat, cand_lon))
            except Exception:
                continue
            candidates.append((distance, record))

        if candidates:
            best = (
                sorted(candidates, key=lambda item: item[0])
                if len(candidates) <= limit
                else heapq.nsmallest(limit, candidates, key=lambda item: item[0])
            )
            seen_ids = {id(record) for _, record in best}
            if len(best) >= limit or radius_km is None:
                break

    if len(best) < limit:
        fallback: list[tuple[float, RecordT]] = list(best)
        for record in all_records:
            if id(record) in seen_ids:
                continue
            try:
                coords = get_coords(record)
            except Exception:
                continue
            if coords is None:
                continue
            cand_lat, cand_lon = coords
            try:
                distance = float(haversine_distance(lat, lon, cand_lat, cand_lon))
            except Exception:
                continue
            fallback.append((distance, record))
        best = heapq.nsmallest(limit, fallback, key=lambda item: item[0])

    return [(record, float(distance)) for distance, record in best]
