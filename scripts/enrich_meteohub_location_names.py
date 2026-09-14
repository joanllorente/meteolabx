#!/usr/bin/env python3
"""Replace opaque MeteoNetwork codes with unique coordinate-derived names."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "data" / "data_estaciones_meteohub_it.json"
DEFAULT_REPORT = ROOT / "data" / "meteohub_location_names.json"
CODE_NAME = re.compile(r"^[a-z]{2,5}\d{2,5}$", re.IGNORECASE)
PLACE_CODES = {"PPL", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLC", "PPLG", "PPLL"}
EARTH_KM = 6371.0088


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_places(path: Path) -> list[dict[str, Any]]:
    places = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 15 or fields[6] != "P" or fields[7] not in PLACE_CODES:
                continue
            try:
                lat, lon = float(fields[4]), float(fields[5])
            except ValueError:
                continue
            places.append({
                "geoname_id": fields[0], "name": fields[1], "lat": lat, "lon": lon,
                "feature_code": fields[7], "population": int(fields[14] or 0),
            })
    if not places:
        raise ValueError(f"No populated places found in {path}")
    return places


def xyz(lat: float, lon: float) -> tuple[float, float, float]:
    phi, lam = math.radians(lat), math.radians(lon)
    return math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi)


def distance_and_bearing(lat: float, lon: float, place: dict[str, Any]) -> tuple[float, str]:
    p1, p2 = math.radians(lat), math.radians(float(place["lat"]))
    dl = math.radians(float(place["lon"]) - lon)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    distance = 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    directions = ("N", "NE", "E", "SE", "S", "SO", "O", "NO")
    return distance, directions[int((bearing + 22.5) // 45) % 8]


def enrich(rows: list[dict[str, Any]], places: list[dict[str, Any]]) -> dict[str, Any]:
    targets = [
        row for row in rows
        if str(row.get("network") or "").lower() == "mnw"
        and CODE_NAME.fullmatch(str(row.get("name") or "").strip())
        and row.get("lat") is not None and row.get("lon") is not None
    ]
    tree = cKDTree(np.asarray([xyz(place["lat"], place["lon"]) for place in places]))
    candidates: list[tuple[dict[str, Any], dict[str, Any], float, str]] = []
    for row in targets:
        _, index = tree.query(xyz(float(row["lat"]), float(row["lon"])), k=1)
        place = places[int(index)]
        distance, direction = distance_and_bearing(float(row["lat"]), float(row["lon"]), place)
        candidates.append((row, place, distance, direction))

    protected = {
        str(row.get("name") or "").strip().casefold()
        for row in rows if row not in targets and str(row.get("name") or "").strip()
    }
    base_counts = Counter(str(place["name"]).strip().casefold() for _, place, _, _ in candidates)
    used = set(protected)
    changes = []
    for row, place, distance, direction in candidates:
        code = str(row.get("name") or "").strip()
        base = str(place["name"]).strip()
        collision = base.casefold() in protected or base_counts[base.casefold()] > 1
        proposed = base if not collision else f"{base} · {distance:.1f} km {direction}"
        if proposed.casefold() in used:
            proposed = f"{base} · {float(row['lat']):.4f}, {float(row['lon']):.4f}"
        suffix = 2
        unique = proposed
        while unique.casefold() in used:
            unique = f"{proposed} · {suffix}"
            suffix += 1
        used.add(unique.casefold())
        aliases = [str(value) for value in row.get("aliases", []) if str(value).strip()]
        if code not in aliases:
            aliases.append(code)
        row["station_code"] = code
        row["aliases"] = aliases
        row["name"] = unique
        row["display_name"] = unique
        row["locality"] = base
        row["name_source"] = "GeoNames nearest populated place"
        row["name_distance_km"] = round(distance, 3)
        row["geoname_id"] = place["geoname_id"]
        changes.append({"id": row.get("id"), "code": code, "name": unique, "distance_km": round(distance, 3)})
    return {
        "updated": len(changes),
        "collisions_disambiguated": sum(" · " in change["name"] for change in changes),
        "changes": changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("geonames", type=Path)
    parser.add_argument("--input", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    report = enrich(rows, load_places(args.geonames))
    atomic_json(args.output, rows)
    atomic_json(args.report, report)
    print({key: value for key, value in report.items() if key != "changes"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
