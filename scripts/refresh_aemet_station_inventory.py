#!/usr/bin/env python3
"""Refresh AEMET active stations and retain its climatological archive."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "data_estaciones_aemet.json"
BASE_URL = "https://opendata.aemet.es/opendata/api"
CURRENT_PATH = "/observacion/convencional/todas"
CLIMATE_PATH = "/valores/climatologicos/inventarioestaciones/todasestaciones"

SENSOR_FIELDS = {
    "thermometer": ("ta", "tamin", "tamax"),
    "hygrometer": ("hr",),
    "barometer": ("pres", "pres_nmar"),
    "anemometer": ("vv", "vmax", "vvu", "vmaxu"),
    "wind_vane": ("dv", "dmax", "dvu", "dmaxu"),
    "rain_gauge": ("prec",),
    "pyranometer": (),
    "uv": (),
}


def _load_local_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _decode_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        for encoding in ("utf-8", "latin-1"):
            try:
                return json.loads(response.content.decode(encoding))
            except Exception:
                continue
        raise


def _download_product(api_key: str, path: str) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            metadata_response = requests.get(
                f"{BASE_URL}{path}", headers={"api_key": api_key}, timeout=45,
            )
            metadata_response.raise_for_status()
            metadata = _decode_json(metadata_response)
            if not isinstance(metadata, dict) or metadata.get("estado") != 200 or not metadata.get("datos"):
                raise RuntimeError(f"AEMET metadata response failed for {path}: {metadata}")
            data_response = requests.get(str(metadata["datos"]), timeout=600)
            data_response.raise_for_status()
            return _decode_json(data_response)
        except (requests.RequestException, RuntimeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    assert last_error is not None
    raise last_error


def _dms_to_decimal(value: Any) -> float | None:
    raw = str(value or "").strip().upper()
    if len(raw) < 2 or raw[-1] not in "NSEW":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    digits, hemisphere = raw[:-1], raw[-1]
    # AEMET pads Spanish longitudes to two degree digits (e.g. 05°20′49″W
    # is ``052049W``), while accepting a variable-width degree component is
    # safer for any future catalogue expansion.
    degree_digits = len(digits) - 4
    if degree_digits < 1 or not digits.isdigit():
        return None
    degrees = int(digits[:degree_digits])
    minutes = int(digits[degree_digits:degree_digits + 2])
    seconds = int(digits[degree_digits + 2:degree_digits + 4])
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    return -decimal if hemisphere in "SW" else decimal


def _has_value(row: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return any(field in row and row.get(field) not in (None, "") for field in fields)


def build_inventory(
    current_records: list[dict[str, Any]],
    climate_records: list[dict[str, Any]],
    previous_stations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    previous = {str(row.get("idema") or "").strip(): row for row in previous_stations}
    climate = {
        str(row.get("indicativo") or "").strip(): row
        for row in climate_records
        if str(row.get("indicativo") or "").strip()
    }
    current_by_code: dict[str, list[dict[str, Any]]] = {}
    for row in current_records:
        code = str(row.get("idema") or "").strip()
        if code:
            current_by_code.setdefault(code, []).append(row)

    current_codes = set(current_by_code)
    climate_codes = set(climate)
    previous_codes = set(previous)
    previous_active_codes = {
        code for code, row in previous.items() if row.get("online") is not False
    }
    stations: list[dict[str, Any]] = []
    for code in sorted(current_codes | climate_codes):
        old = previous.get(code, {})
        climate_row = climate.get(code, {})
        observations = current_by_code.get(code, [])
        latest = max(observations, key=lambda row: str(row.get("fint") or ""), default={})
        active = bool(observations)

        station = dict(old)
        station.update({
            "idema": code,
            "nombre": str(
                latest.get("ubi") or climate_row.get("nombre") or old.get("nombre") or code
            ).strip(),
            "provincia": str(
                climate_row.get("provincia") or old.get("provincia") or ""
            ).strip(),
            "lat": latest.get("lat") if latest.get("lat") is not None else (
                _dms_to_decimal(climate_row.get("latitud"))
                if climate_row else old.get("lat")
            ),
            "lon": latest.get("lon") if latest.get("lon") is not None else (
                _dms_to_decimal(climate_row.get("longitud"))
                if climate_row else old.get("lon")
            ),
            "alt": latest.get("alt") if latest.get("alt") is not None else (
                climate_row.get("altitud") if climate_row else old.get("alt")
            ),
            "online": active,
            "has_historical": True,
            "status": "active" if active else "historical",
            "historical_only": not active,
        })
        if climate_row.get("indsinop"):
            station["indsinop"] = str(climate_row["indsinop"]).strip()
        if active:
            station.pop("status_reason", None)
            station.pop("replacement_station_id", None)
            station.pop("replacement_station_name", None)
            station["sensors"] = {
                sensor: any(_has_value(row, fields) for row in observations) if fields else False
                for sensor, fields in SENSOR_FIELDS.items()
            }
        elif not isinstance(station.get("sensors"), dict):
            station.pop("sensors", None)
        stations.append(station)

    report = {
        "added_active": sorted(current_codes - previous_codes),
        "added_historical": sorted((climate_codes - current_codes) - previous_codes),
        "newly_historical": sorted((previous_active_codes - current_codes) & climate_codes),
        "removed_without_archive": sorted(previous_codes - (current_codes | climate_codes)),
    }
    return stations, report


def main() -> int:
    _load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--api-key",
        default=os.getenv("METEOLABX_AEMET_API_KEY", "") or os.getenv("AEMET_API_KEY", ""),
    )
    args = parser.parse_args()
    api_key = str(args.api_key or "").strip()
    if not api_key:
        parser.error("missing METEOLABX_AEMET_API_KEY/AEMET_API_KEY")

    previous_payload = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
    previous = previous_payload.get("estaciones", []) if isinstance(previous_payload, dict) else []
    current = _download_product(api_key, CURRENT_PATH)
    climate = _download_product(api_key, CLIMATE_PATH)
    if not isinstance(current, list) or not isinstance(climate, list):
        raise RuntimeError("AEMET products did not return station lists")
    stations, report = build_inventory(current, climate, previous)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    output = {
        "version": "2.0",
        "fecha_generacion": generated_at,
        "total_estaciones": len(stations),
        "fuente": "AEMET OpenData: observación convencional + inventario climatológico",
        "estaciones": stations,
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    active = sum(bool(row.get("online")) for row in stations)
    print(f"Saved {len(stations)} AEMET stations: {active} active, {len(stations) - active} historical-only")
    for key, values in report.items():
        print(f"{key} ({len(values)}): {', '.join(values) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
