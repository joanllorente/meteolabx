"""
Parsing puro de los feeds POEM (Puertos del Estado).

Contiene las heurísticas de extracción de filas y series consumidas por
``server/services/poem.py`` sin importar ``streamlit``.

Contenido: normalizadores de texto/clave/valor, detección de filas en
payloads arbitrarios (``_extract_rows``), mapeo de columnas a
magnitudes (``_kind_from_hint``), escalas raras de los feeds TR
(décimas de m/s, centésimas de %, Kelvin defensivo…) y el ensamblado
``_rows_to_series``.
"""

from __future__ import annotations

import os
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from domain.parsing.common import parse_epoch as _parse_epoch

POEM_SERIES_KEEP_WINDOW_SECONDS = int(os.getenv("POEM_SERIES_KEEP_WINDOW_SECONDS", str(90 * 86400)))


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


def _normalize_text(value: Any) -> str:
    txt = str(value or "").strip().lower()
    txt = "".join(c for c in unicodedata.normalize("NFD", txt) if unicodedata.category(c) != "Mn")
    return txt


def _normalize_key(value: Any) -> str:
    txt = _normalize_text(value)
    return "".join(ch for ch in txt if ch.isalnum() or ch == "_")


def _normalize_station_token(value: Any) -> str:
    raw = str(value or "").strip().upper()
    token = "".join(ch for ch in raw if ch.isalnum())
    if token.isdigit():
        try:
            return str(int(token))
        except Exception:
            return token
    return token


def _iter_scalar_items(obj: Any, prefix: str = "") -> Iterable[Tuple[str, Any]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = _normalize_key(f"{prefix}_{k}" if prefix else k)
            if isinstance(v, dict):
                if "value" in v or "valor" in v:
                    if "value" in v:
                        yield key, v.get("value")
                    if "valor" in v:
                        yield key, v.get("valor")
                for kk, vv in _iter_scalar_items(v, key):
                    yield kk, vv
            elif isinstance(v, list):
                continue
            else:
                yield key, v


def _looks_like_row(item: Dict[str, Any]) -> bool:
    if not isinstance(item, dict) or not item:
        return False
    scalar_count = 0
    for v in item.values():
        if isinstance(v, (str, int, float, bool)) or v is None:
            scalar_count += 1
    if scalar_count >= 2:
        return True
    keys_norm = {_normalize_key(k) for k in item.keys()}
    return bool(keys_norm.intersection({"valor", "value", "timestamp", "fecha", "instante"}))


def _extract_rows(payload: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def scalar_part(node: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in node.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                out[k] = v
        return out

    def walk(node: Any, context: Optional[Dict[str, Any]] = None) -> None:
        if context is None:
            context = {}

        if isinstance(node, list):
            for child in node:
                walk(child, context)
            return

        if isinstance(node, dict):
            merged = dict(context)
            merged.update(scalar_part(node))
            if _looks_like_row(merged):
                rows.append(merged)
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child, merged)

    walk(payload, {})
    return rows


def _extract_station_tokens(items: Sequence[Tuple[str, Any]]) -> List[str]:
    out: List[str] = []
    for key, value in items:
        k = _normalize_key(key)
        if k in {"codigo", "cod", "idestacion", "idest", "stationid", "codigoestacion"}:
            token = _normalize_station_token(value)
            if token:
                out.append(token)
            continue
        if k == "id":
            token = _normalize_station_token(value)
            if token and token.isdigit():
                out.append(token)
            continue
        if (
            ("estacion" in k or "boya" in k or "mareografo" in k)
            and ("id" in k or "cod" in k or "codigo" in k)
        ):
            token = _normalize_station_token(value)
            if token:
                out.append(token)
    return out


def _row_matches_station(items: Sequence[Tuple[str, Any]], station_code: str) -> bool:
    target = _normalize_station_token(station_code)
    if not target:
        return True
    station_tokens = _extract_station_tokens(items)
    if not station_tokens:
        return True
    return target in station_tokens


def _key_implies_meters_per_second(key_norm: str) -> bool:
    k = _normalize_key(key_norm)
    return (
        "m_s" in k
        or "m/s" in key_norm
        or k.endswith("mps")
        or "metrossegundo" in k
    )


def _normalize_pressure(value: float, key_norm: str) -> float:
    if _is_nan(value):
        return float("nan")
    if "pa" in key_norm and value > 2000:
        return value / 100.0
    return value


def _normalize_temp(value: float) -> float:
    if _is_nan(value):
        return value
    # Kelvin defensivo.
    if 170.0 < value < 360.0:
        return value - 273.15
    return value


def _normalize_rh(value: float) -> float:
    if _is_nan(value):
        return value
    # POEM redext_tr entrega HR como centesimas de porcentaje: 9340 -> 93.40%.
    if abs(value) > 1000.0:
        value = value / 100.0
    elif abs(value) > 100.0:
        value = value / 10.0
    if value < 0.0 or value > 100.0:
        return float("nan")
    return value


def _kind_from_hint(text_norm: str, key_norm: str = "") -> str:
    txt = text_norm
    key = key_norm
    full = f"{txt}_{key}"
    compact_txt = "".join(ch for ch in txt if ch.isalnum())
    compact_key = "".join(ch for ch in key if ch.isalnum())
    compact = compact_key or compact_txt
    compact_full = "".join(ch for ch in full if ch.isalnum())

    if compact.startswith(("dmd", "dv", "wd", "dirv", "dirmed", "direccionviento")):
        return "wind_dir"
    if compact.startswith(("vvmax", "vvmx", "gust", "racha", "vmax", "vx", "rv")):
        return "gust"
    if compact.startswith(("ds", "vv", "ws", "windspeed", "vmed", "velmed", "viento")):
        return "wind"
    if compact in {"ps", "pa"} or compact_full.startswith("press") or (
        compact.startswith("pa")
        and not compact.startswith(("par", "param"))
    ):
        return "pressure_abs"
    if compact.startswith(("hr", "rh", "hum")):
        return "rh"
    if compact in {"ts", "ta"} or compact_full.startswith(("temp", "tair", "temperaturaaire")):
        return "temp"

    if any(token in full for token in ("direccion", "dir")) and any(token in full for token in ("viento", "wind", "dv")):
        return "wind_dir"
    if any(token in full for token in ("racha", "gust", "max")) and any(token in full for token in ("viento", "wind", "vv")):
        return "gust"
    if "msl" in full or "nivelmar" in full or "sealevel" in full:
        return "pressure_msl"
    if any(token in full for token in ("presion", "pressure", "baromet")):
        return "pressure_abs"
    if any(token in full for token in ("humedad", "humidity", "rh", "hr")):
        return "rh"
    if any(token in full for token in ("precip", "rain", "lluvia")):
        return "precip"
    if any(token in full for token in ("radiacion", "solar", "irradian", "rs")):
        return "solar"
    if any(token in full for token in ("viento", "wind", "vv")):
        return "wind"
    if any(token in full for token in ("temperatura", "temp", "tair")):
        if any(token in full for token in ("agua", "mar", "sea", "water", "sst")):
            return ""
        return "temp"
    return ""


def _is_auxiliary_metric_key(key_norm: str) -> bool:
    key = _normalize_key(key_norm)
    if not key:
        return False
    if key.startswith(("qc_", "q_", "std", "stdev", "stddev", "sigma")):
        return True
    if any(token in key for token in ("desv", "desviacion", "desviaciontipica")):
        return True
    return False


def _unit_is_mps(unit_hint: str) -> bool:
    unit = _normalize_text(unit_hint)
    return ("m/s" in unit) or ("m s-1" in unit) or ("m.s-1" in unit)


def _allowed_metric_key(key_norm: str, allowed_metric_keys: Optional[set[str]]) -> bool:
    if not allowed_metric_keys:
        return True
    key = _normalize_key(key_norm)
    if key in allowed_metric_keys:
        return True
    return any(key.endswith(f"_{allowed}") for allowed in allowed_metric_keys)


def _poem_wind_scale(endpoint: str) -> str:
    endpoint_norm = str(endpoint or "").strip().lower()
    if endpoint_norm.endswith("/mareas/redmar_mir_tr") or "/mareas/redmar_mir_tr" in endpoint_norm:
        return "tenths_mps"
    return ""


def _normalize_poem_wind_value(value: float, key_norm: str, wind_scale: str = "") -> float:
    if _is_nan(value):
        return value
    key = _normalize_key(key_norm)
    if str(wind_scale or "").strip().lower() == "tenths_mps":
        return (value / 10.0) * 3.6

    if key.startswith("vv_") or key in {"vvmd", "vvmx"}:
        if abs(value) >= 100.0:
            value = value / 100.0
        elif abs(value) > 25.0:
            value = value / 10.0
        return value * 3.6

    return value


def _extract_epoch(items: Sequence[Tuple[str, Any]]) -> Optional[int]:
    time_keys = (
        "timestamp",
        "fecha",
        "fechahora",
        "instante",
        "hora",
        "fhora",
        "fhora",
        "fmedida",
        "fregistro",
        "datetime",
        "date",
        "time",
        "utc",
    )
    for key, value in items:
        key_norm = _normalize_key(key)
        if any(token in key_norm for token in time_keys):
            epoch = _parse_epoch(value)
            if epoch is not None:
                return epoch
    return None


def _extract_lat_lon(items: Sequence[Tuple[str, Any]]) -> Tuple[float, float]:
    lat = float("nan")
    lon = float("nan")
    for key, value in items:
        key_norm = _normalize_key(key)
        if key_norm in ("lat", "latitud", "latitude"):
            lat = _safe_float(value)
        elif key_norm in ("lon", "longitud", "longitude", "lng"):
            lon = _safe_float(value)
    return lat, lon


def _extract_metric_updates(
    items: Sequence[Tuple[str, Any]],
    *,
    allowed_metric_keys: Optional[set[str]] = None,
    wind_scale: str = "",
) -> Dict[str, float]:
    updates: Dict[str, float] = {}
    norm_keys = [_normalize_key(key) for key, _ in items]
    has_dv = any(k.startswith("dv_") or k == "dv" for k in norm_keys)
    has_vv = any(k.startswith("vv_") or k == "vv" for k in norm_keys)
    has_dv_md = any(k == "dv_md" or k.endswith("_dv_md") for k in norm_keys)

    # Formato "largo": parametro + valor.
    param_hint = ""
    unit_hint = ""
    raw_value = float("nan")
    for key, value in items:
        key_norm = _normalize_key(key)
        if key_norm in {"parametro", "nombreparametro", "codigoparametro", "variable", "magnitud"}:
            if value is not None:
                param_hint = _normalize_text(value)
        if key_norm in {"unidad", "unit", "unidade", "units", "unitcode"}:
            unit_hint = str(value or "")
        if key_norm in {"valor", "value", "dato", "medida"}:
            raw_value = _safe_float(value)

    if param_hint and not _is_nan(raw_value):
        kind = _kind_from_hint(param_hint)
        if kind:
            if kind in ("wind", "gust") and _unit_is_mps(unit_hint):
                raw_value = raw_value * 3.6
            updates[kind] = raw_value

    # Formato "ancho": cada columna una variable.
    for key, value in items:
        key_norm = _normalize_key(key)
        if _is_auxiliary_metric_key(key_norm):
            continue
        if not _allowed_metric_key(key_norm, allowed_metric_keys):
            continue
        val = _safe_float(value)
        if _is_nan(val):
            continue
        kind = _kind_from_hint(_normalize_text(key_norm), key_norm=key_norm)
        if not kind:
            continue
        if kind == "wind_dir" and key_norm.startswith("dmd") and has_dv:
            continue
        if kind == "wind_dir" and (key_norm == "dv_mx" or key_norm.endswith("_dv_mx")) and has_dv_md:
            continue
        if kind == "wind" and key_norm == "ds" and has_vv:
            continue

        # Escalas compactas observadas en feeds TR de POEM (ejemplo redcos_tr).
        if key_norm in {"ds"}:
            if abs(val) > 25.0:
                val = val / 10.0
            val = val * 3.6  # m/s -> km/h
        elif key_norm == "ts":
            if abs(val) > 60.0:
                val = val / 10.0
        elif key_norm == "ta":
            if abs(val) >= 1000.0:
                val = val / 100.0
            elif abs(val) > 60.0:
                val = val / 10.0
        elif key_norm in {"hr"}:
            val = _normalize_rh(val)
        elif key_norm in {"pa", "ps", "pres", "presion"}:
            if abs(val) > 2000.0:
                val = val / 10.0
            if abs(val) > 2000.0:
                val = val / 100.0
        elif key_norm.startswith("vv_") or key_norm in {"vvmd", "vvmx"}:
            val = _normalize_poem_wind_value(val, key_norm, wind_scale)

        if kind in ("wind", "gust") and _key_implies_meters_per_second(key_norm):
            val = val * 3.6
        updates[kind] = val

    # Normalizaciones por variable.
    if "temp" in updates:
        updates["temp"] = _normalize_temp(updates["temp"])
    if "rh" in updates:
        updates["rh"] = _normalize_rh(updates["rh"])
    if "pressure_abs" in updates:
        updates["pressure_abs"] = _normalize_pressure(updates["pressure_abs"], "pa")
    if "pressure_msl" in updates:
        updates["pressure_msl"] = _normalize_pressure(updates["pressure_msl"], "pa")

    return updates


def _rows_to_series(
    rows: List[Dict[str, Any]],
    station_code: str,
    allowed_metric_keys: Optional[set[str]] = None,
    wind_scale: str = "",
) -> Dict[str, Any]:
    by_epoch: Dict[int, Dict[str, float]] = {}
    now_epoch = int(datetime.now(timezone.utc).timestamp())

    for row in rows:
        items = list(_iter_scalar_items(row))
        if not items:
            continue
        if not _row_matches_station(items, station_code):
            continue

        updates = _extract_metric_updates(
            items,
            allowed_metric_keys=allowed_metric_keys,
            wind_scale=wind_scale,
        )
        if not updates:
            continue

        epoch = _extract_epoch(items)
        if epoch is None:
            if len(rows) == 1:
                epoch = now_epoch
            else:
                continue

        lat, lon = _extract_lat_lon(items)
        target = by_epoch.get(int(epoch))
        if target is None:
            target = {
                "temp": float("nan"),
                "rh": float("nan"),
                "pressure_abs": float("nan"),
                "pressure_msl": float("nan"),
                "wind": float("nan"),
                "gust": float("nan"),
                "wind_dir": float("nan"),
                "precip": float("nan"),
                "solar": float("nan"),
                "lat": float("nan"),
                "lon": float("nan"),
            }
        for k, v in updates.items():
            target[k] = float(v)
        if not _is_nan(lat):
            target["lat"] = float(lat)
        if not _is_nan(lon):
            target["lon"] = float(lon)
        by_epoch[int(epoch)] = target

    epochs = sorted(by_epoch.keys())
    temps: List[float] = []
    rhs: List[float] = []
    p_abs: List[float] = []
    p_msl: List[float] = []
    winds: List[float] = []
    gusts: List[float] = []
    dirs: List[float] = []
    precs: List[float] = []
    solars: List[float] = []
    lats: List[float] = []
    lons: List[float] = []

    for ep in epochs:
        row = by_epoch[ep]
        temps.append(float(row["temp"]))
        rhs.append(float(row["rh"]))
        p_abs.append(float(row["pressure_abs"]))
        p_msl.append(float(row["pressure_msl"]))
        winds.append(float(row["wind"]))
        gusts.append(float(row["gust"]))
        dirs.append(float(row["wind_dir"]))
        precs.append(float(row["precip"]))
        solars.append(float(row["solar"]))
        lats.append(float(row["lat"]))
        lons.append(float(row["lon"]))

    has_data = len(epochs) > 0
    return {
        "epochs": [int(ep) for ep in epochs],
        "temps": temps,
        "humidities": rhs,
        "pressures_abs": p_abs,
        "pressures_msl": p_msl,
        "winds": winds,
        "gusts": gusts,
        "wind_dirs": dirs,
        "precips": precs,
        "solar_radiations": solars,
        "lats": lats,
        "lons": lons,
        "has_data": has_data,
    }


def _trim_series_window(series: Dict[str, Any], window_seconds: int = POEM_SERIES_KEEP_WINDOW_SECONDS) -> Dict[str, Any]:
    epochs = [int(ep) for ep in series.get("epochs", [])]
    if not epochs:
        return dict(series)
    min_epoch = int(epochs[-1] - max(0, int(window_seconds)))
    keep_idx = [idx for idx, ep in enumerate(epochs) if int(ep) >= min_epoch]
    if not keep_idx:
        return dict(series)

    out: Dict[str, Any] = {}
    for key, values in series.items():
        if key == "has_data":
            continue
        if isinstance(values, list) and len(values) == len(epochs):
            out[key] = [values[idx] for idx in keep_idx]
        else:
            out[key] = values
    out["has_data"] = bool(out.get("epochs"))
    return out


def _latest_epoch(series: Dict[str, Any]) -> Optional[int]:
    epochs = series.get("epochs", [])
    if not epochs:
        return None
    try:
        return int(epochs[-1])
    except Exception:
        return None
