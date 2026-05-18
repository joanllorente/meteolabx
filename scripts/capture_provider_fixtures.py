#!/usr/bin/env python3
"""
Captura muestras reales de proveedores para fixtures de test.

Las pruebas unitarias no deben depender de internet. Este script hace las
llamadas reales bajo demanda y guarda una foto saneada de la salida canónica
que usa MeteoLabX. No guarda API keys ni tokens.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DEFAULT_STATIONS = {
    "WU": "",
    "AEMET": "3195",
    "METEOCAT": "Z6",
    "EUSKALMET": "C009",
    "FROST": "SN18700",
    "METEOFRANCE": "75110001",
    "METEOGALICIA": "10157",
    "NWS": "KSEA",
    "POEM": "1103",
}

CORE_KEYS = (
    "idema",
    "station_code",
    "station_name",
    "Tc",
    "RH",
    "p_hpa",
    "p_abs_hpa",
    "wind",
    "gust",
    "wind_dir_deg",
    "precip_total",
    "solar_radiation",
    "uv",
    "epoch",
    "lat",
    "lon",
    "elevation",
    "temp_max",
    "temp_min",
    "rh_max",
    "rh_min",
    "gust_max",
    "pressure_3h_ago",
    "epoch_3h_ago",
)


def _env(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name, "")).strip()
        if value:
            return value
    return ""


def _station_for(provider: str) -> str:
    provider = provider.upper()
    return _env(
        f"MLX_CAPTURE_{provider}_STATION_ID",
        f"MLX_{provider}_STATION_ID",
        f"{provider}_STATION_ID",
    ) or DEFAULT_STATIONS.get(provider, "")


def _state(provider: str, station_id: str) -> dict[str, Any]:
    provider = provider.upper()
    prefix = provider.lower()
    return {
        "connected": True,
        "connection_type": provider,
        "provider_station_id": station_id,
        f"{prefix}_station_id": station_id,
    }


def _finite_or_none(value: Any) -> Any:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _sanitize(v)
            for k, v in value.items()
            if str(k).lower() not in {"api_key", "apikey", "token", "bearer", "authorization", "cookie"}
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return _finite_or_none(value)


def _compact_series(series: Any, keep: int = 48) -> Any:
    if not isinstance(series, dict):
        return series
    epochs = series.get("epochs")
    if not isinstance(epochs, list) or len(epochs) <= keep:
        return series

    start = len(epochs) - keep
    compact = {}
    for key, value in series.items():
        if isinstance(value, list) and len(value) == len(epochs):
            compact[key] = value[start:]
        else:
            compact[key] = value
    compact["_compacted_from"] = len(epochs)
    return compact


def _canonical_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = {key: data.get(key) for key in CORE_KEYS if key in data}
    for series_key in ("_series", "_series_7d"):
        if series_key in data:
            payload[series_key] = _compact_series(data.get(series_key))
    return _sanitize(payload)


def _call_wu(station_id: str) -> dict[str, Any]:
    from api.weather_underground import fetch_wu_current

    api_key = _env("MLX_CAPTURE_WU_API_KEY", "MLX_WU_API_KEY", "WU_API_KEY", "WEATHER_UNDERGROUND_API_KEY")
    if not station_id or not api_key:
        raise RuntimeError("WU omitido: faltan MLX_CAPTURE_WU_STATION_ID y/o MLX_CAPTURE_WU_API_KEY en entorno.")
    return fetch_wu_current(station_id, api_key)


def _call_aemet(station_id: str) -> dict[str, Any]:
    from services.aemet import get_aemet_data

    return get_aemet_data(_state("AEMET", station_id)) or {}


def _call_meteocat(station_id: str) -> dict[str, Any]:
    from services.meteocat import get_meteocat_data

    return get_meteocat_data(state=_state("METEOCAT", station_id)) or {}


def _call_euskalmet(station_id: str) -> dict[str, Any]:
    from services.euskalmet import get_euskalmet_data

    return get_euskalmet_data(state=_state("EUSKALMET", station_id)) or {}


def _call_frost(station_id: str) -> dict[str, Any]:
    from services.frost import get_frost_data

    return get_frost_data(_state("FROST", station_id)) or {}


def _call_meteofrance(station_id: str) -> dict[str, Any]:
    from services.meteofrance import get_meteofrance_data

    return get_meteofrance_data(_state("METEOFRANCE", station_id)) or {}


def _call_meteogalicia(station_id: str) -> dict[str, Any]:
    from services.meteogalicia import get_meteogalicia_data

    return get_meteogalicia_data(_state("METEOGALICIA", station_id)) or {}


def _call_nws(station_id: str) -> dict[str, Any]:
    from services.nws import get_nws_data

    return get_nws_data(_state("NWS", station_id)) or {}


def _call_poem(station_id: str) -> dict[str, Any]:
    from services.poem import get_poem_data

    return get_poem_data(_state("POEM", station_id)) or {}


CALLERS: dict[str, Callable[[str], dict[str, Any]]] = {
    "WU": _call_wu,
    "AEMET": _call_aemet,
    "METEOCAT": _call_meteocat,
    "EUSKALMET": _call_euskalmet,
    "FROST": _call_frost,
    "METEOFRANCE": _call_meteofrance,
    "METEOGALICIA": _call_meteogalicia,
    "NWS": _call_nws,
    "POEM": _call_poem,
}


def capture_provider(provider: str, out_dir: Path) -> dict[str, Any]:
    provider = provider.upper()
    station_id = _station_for(provider)
    result: dict[str, Any] = {
        "provider": provider,
        "station_id": station_id,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "error",
    }
    if provider not in CALLERS:
        result["error"] = f"Proveedor no soportado: {provider}"
        return result
    if not station_id:
        result["status"] = "skipped"
        result["error"] = f"Sin station_id para {provider}."
        return result

    try:
        data = CALLERS[provider](station_id)
        if not isinstance(data, dict) or not data:
            raise RuntimeError("La llamada no devolvió datos canónicos.")
        result["status"] = "ok"
        result["payload"] = _canonical_payload(data)
    except Exception as exc:
        result["status"] = "skipped" if provider == "WU" else "error"
        result["error"] = str(exc)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{provider.lower()}.json"
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Captura fixtures reales saneadas de proveedores MeteoLabX.")
    parser.add_argument(
        "--out-dir",
        default=str(ROOT_DIR / "tests" / "fixtures" / "provider_live"),
        help="Directorio de salida para JSON fixtures.",
    )
    parser.add_argument(
        "--providers",
        nargs="+",
        default=list(CALLERS.keys()),
        help="Lista de proveedores a capturar.",
    )
    parser.add_argument("--fail-on-error", action="store_true", help="Devuelve código 1 si algún proveedor falla.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    results = [capture_provider(provider, out_dir) for provider in args.providers]
    manifest = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "results": [
            {k: v for k, v in item.items() if k != "payload"}
            for item in results
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    for item in results:
        message = f"{item['provider']}: {item['status']}"
        if item.get("station_id"):
            message += f" ({item['station_id']})"
        if item.get("error"):
            message += f" - {item['error']}"
        print(message)

    has_error = any(item.get("status") == "error" for item in results)
    return 1 if args.fail_on_error and has_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
