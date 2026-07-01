#!/usr/bin/env python3
"""Compare the IEM inventory with MeteoLabX provider inventories."""

from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_IEM = DATA / "data_estaciones_iem.json"
DEFAULT_OUTPUT = DATA / "iem_inventory_overlap_report.json"
MAX_DISTANCE_M = 150.0
GRID_DEGREES = 0.002


@dataclass(frozen=True)
class Station:
    provider: str
    station_id: str
    name: str
    lat: float
    lon: float
    elevation: float | None
    identifiers: frozenset[str]
    raw: dict[str, Any]


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("stations", "estaciones", "listaEstacionsMeteo"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _coordinates(row: dict[str, Any]) -> tuple[float, float] | None:
    coordinates = row.get("coordenades")
    if isinstance(coordinates, dict):
        lat = _float(coordinates.get("latitud"))
        lon = _float(coordinates.get("longitud"))
    else:
        lat = _float(_first(row, "lat", "latitude"))
        lon = _float(_first(row, "lon", "longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _identifier(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text and text not in {"NONE", "NULL", "9999", "99999"} else ""


def _identifiers(row: dict[str, Any], station_id: str) -> frozenset[str]:
    values = {station_id}
    for key in (
        "source_id", "id_station", "id_omm", "idema", "stationId",
        "codi", "idEstacion", "codigo", "geohash", "synop", "climate_site",
    ):
        value = _identifier(row.get(key))
        if value:
            values.add(value)
    attributes = row.get("attributes")
    if isinstance(attributes, dict):
        for key in ("GHCNH_ID", "NCEI_ID", "WMO_ID", "ICAO"):
            value = _identifier(attributes.get(key))
            if value:
                values.add(value)
    return frozenset(value for value in values if value)


def _station(row: dict[str, Any], provider: str) -> Station | None:
    coordinates = _coordinates(row)
    if coordinates is None:
        return None
    station_id = _identifier(_first(
        row, "id", "source_id", "idema", "stationId", "codi",
        "id_station", "idEstacion", "codigo", "geohash",
    ))
    if not station_id:
        return None
    name = str(_first(
        row, "name", "nombre", "nom", "displayName", "estacion", "nom_usuel",
    ) or station_id).strip()
    elevation = _float(_first(row, "elev", "elevation", "altitude", "altitude_m", "altitud", "alt"))
    return Station(
        provider=provider,
        station_id=station_id,
        name=name,
        lat=coordinates[0],
        lon=coordinates[1],
        elevation=elevation,
        identifiers=_identifiers(row, station_id),
        raw=row,
    )


def _normal_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b(station|weather|airport|aeroport|aeropuerto|meteo|automatic|automatica)\b", " ", text)
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _name_similarity(left: str, right: str) -> float:
    a, b = _normal_name(left), _normal_name(right)
    if not a or not b:
        return 0.0
    tokens_a, tokens_b = set(a.split()), set(b.split())
    jaccard = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    sequence = SequenceMatcher(None, a, b).ratio()
    return max(jaccard, sequence)


def _distance_m(a: Station, b: Station) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 12_742_000.0 * math.asin(min(1.0, math.sqrt(value)))


def _classification(source: Station, iem: Station, distance: float, name_score: float) -> str:
    shared_identifier = bool(source.identifiers & iem.identifiers)
    elevation_delta = (
        abs(source.elevation - iem.elevation)
        if source.elevation is not None and iem.elevation is not None
        else None
    )
    if shared_identifier and distance <= 1_000:
        return "secure"
    if distance <= 50 and name_score >= 0.55 and (elevation_delta is None or elevation_delta <= 50):
        return "secure"
    if distance <= 150 and (name_score >= 0.45 or (elevation_delta is not None and elevation_delta <= 30)):
        return "probable"
    return "ambiguous"


def _provider_inventories() -> Iterable[tuple[str, Path]]:
    for path in sorted(DATA.glob("data_estaciones_*.json")):
        if path.name == DEFAULT_IEM.name:
            continue
        yield path.stem.removeprefix("data_estaciones_"), path


def compare(iem_path: Path) -> dict[str, Any]:
    iem_payload = json.loads(iem_path.read_text(encoding="utf-8"))
    iem_stations = [
        station for row in _rows(iem_payload)
        if (station := _station(row, "iem")) is not None
    ]
    grid: dict[tuple[int, int], list[Station]] = defaultdict(list)
    for station in iem_stations:
        grid[(math.floor(station.lat / GRID_DEGREES), math.floor(station.lon / GRID_DEGREES))].append(station)

    summaries = {}
    all_matched_iem: set[tuple[str, str]] = set()
    for provider, path in _provider_inventories():
        rows = _rows(json.loads(path.read_text(encoding="utf-8")))
        source_stations = [
            station for row in rows
            if (station := _station(row, provider)) is not None
        ]
        counts = {"secure": 0, "probable": 0, "ambiguous": 0}
        different_names = 0
        examples = []
        matched_iem: set[tuple[str, str]] = set()
        multi_network_matches = 0

        for source in source_stations:
            cell_y = math.floor(source.lat / GRID_DEGREES)
            cell_x = math.floor(source.lon / GRID_DEGREES)
            candidates = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    for candidate in grid.get((cell_y + dy, cell_x + dx), []):
                        distance = _distance_m(source, candidate)
                        if distance <= MAX_DISTANCE_M:
                            name_score = _name_similarity(source.name, candidate.name)
                            classification = _classification(source, candidate, distance, name_score)
                            candidates.append((classification, distance, -name_score, candidate, name_score))
            if not candidates:
                continue
            priority = {"secure": 0, "probable": 1, "ambiguous": 2}
            candidates.sort(key=lambda item: (priority[item[0]], item[1], item[2]))
            classification, distance, _, best, name_score = candidates[0]
            counts[classification] += 1
            network_keys = {(item[3].raw.get("network"), item[3].station_id) for item in candidates}
            matched_iem.update(network_keys)
            all_matched_iem.update(network_keys)
            if len({key[0] for key in network_keys}) > 1:
                multi_network_matches += 1
            if _normal_name(source.name) != _normal_name(best.name):
                different_names += 1
            if len(examples) < 12 and (
                classification != "secure" or _normal_name(source.name) != _normal_name(best.name)
            ):
                examples.append({
                    "classification": classification,
                    "distance_m": round(distance, 1),
                    "name_similarity": round(name_score, 3),
                    "source": {"id": source.station_id, "name": source.name},
                    "iem": {
                        "id": best.station_id,
                        "name": best.name,
                        "network": best.raw.get("network"),
                    },
                })

        summaries[provider] = {
            "inventory_rows": len(rows),
            "stations_with_coordinates": len(source_stations),
            "matched_source_stations": sum(counts.values()),
            **counts,
            "matched_iem_records": len(matched_iem),
            "matches_with_different_normalized_names": different_names,
            "source_stations_matching_multiple_iem_networks": multi_network_matches,
            "examples": examples,
        }

    return {
        "method": {
            "maximum_distance_m": MAX_DISTANCE_M,
            "secure": "shared identifier within 1 km, or <=50 m with compatible name/elevation",
            "probable": "<=150 m with compatible name or elevation",
            "ambiguous": "<=150 m without sufficient supporting metadata",
            "warning": "No records are deleted automatically.",
        },
        "iem_records_with_coordinates": len(iem_stations),
        "matched_iem_records_any_provider": len(all_matched_iem),
        "providers": summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iem", type=Path, default=DEFAULT_IEM)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = compare(args.iem)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved overlap report to {args.output}")
    for provider, summary in report["providers"].items():
        print(
            f"{provider:14} matched={summary['matched_source_stations']:6} "
            f"secure={summary['secure']:6} probable={summary['probable']:6} "
            f"ambiguous={summary['ambiguous']:6}"
        )


if __name__ == "__main__":
    main()
