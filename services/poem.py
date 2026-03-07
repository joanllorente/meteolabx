"""
Servicio para integrar observaciones de POEM (Puertos del Estado).
"""

import json
import math
import os
import hashlib
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
import streamlit as st
from requests.auth import HTTPBasicAuth


POEM_BASE_URL = os.getenv("POEM_BASE_URL", "https://poem.puertos.es").rstrip("/")
POEM_TIMEOUT_SECONDS = int(os.getenv("POEM_TIMEOUT_SECONDS", "16"))
POEM_TR_MAX_AGE_SECONDS = int(os.getenv("POEM_TR_MAX_AGE_SECONDS", str(45 * 86400)))
POEM_SERIES_KEEP_WINDOW_SECONDS = int(os.getenv("POEM_SERIES_KEEP_WINDOW_SECONDS", str(90 * 86400)))


def _get_setting(session_key: str, env_key: str, default: str = "") -> str:
    try:
        raw = st.session_state.get(session_key, "")
        if raw not in (None, ""):
            return str(raw).strip()
    except Exception:
        pass
    try:
        secret_val = st.secrets.get(env_key, "")
        if secret_val not in (None, ""):
            return str(secret_val).strip()
    except Exception:
        pass
    return str(os.getenv(env_key, default)).strip()


def _poem_auth_config() -> Tuple[Dict[str, str], Dict[str, Any], Optional[HTTPBasicAuth], bool, str]:
    headers: Dict[str, str] = {}
    params: Dict[str, Any] = {}
    auth: Optional[HTTPBasicAuth] = None
    configured = False

    bearer_token = _get_setting("poem_bearer_token", "POEM_BEARER_TOKEN")
    api_key = _get_setting("poem_api_key", "POEM_API_KEY")
    api_key_header = _get_setting("poem_api_key_header", "POEM_API_KEY_HEADER", "X-API-Key")
    basic_user = _get_setting("poem_basic_user", "POEM_BASIC_USER")
    basic_password = _get_setting("poem_basic_password", "POEM_BASIC_PASSWORD")
    generic_header = _get_setting("poem_auth_header", "POEM_AUTH_HEADER")
    generic_value = _get_setting("poem_auth_value", "POEM_AUTH_VALUE")
    cookie_value = _get_setting("poem_cookie", "POEM_COOKIE")
    token_param = _get_setting("poem_token_param", "POEM_TOKEN_PARAM")
    token_value = _get_setting("poem_token_value", "POEM_TOKEN_VALUE")

    if generic_header and generic_value:
        headers[generic_header] = generic_value
        configured = True

    if bearer_token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {bearer_token}"
        configured = True

    if api_key:
        headers[api_key_header or "X-API-Key"] = api_key
        configured = True

    if basic_user:
        auth = HTTPBasicAuth(basic_user, basic_password)
        configured = True

    if cookie_value:
        headers["Cookie"] = cookie_value
        configured = True

    if token_param and token_value:
        params[token_param] = token_value
        configured = True

    fingerprint_src = "|".join(
        [
            headers.get("Authorization", ""),
            headers.get(api_key_header or "X-API-Key", ""),
            headers.get("Cookie", ""),
            basic_user,
            token_param,
            token_value,
            generic_header,
            generic_value,
        ]
    )
    fingerprint = hashlib.sha1(fingerprint_src.encode("utf-8")).hexdigest()[:12]
    return headers, params, auth, configured, fingerprint


def _poem_auth_help() -> str:
    return (
        "Configura auth POEM en env/session: "
        "POEM_BEARER_TOKEN o POEM_API_KEY (+POEM_API_KEY_HEADER), "
        "o POEM_BASIC_USER/POEM_BASIC_PASSWORD."
    )


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


def _parse_epoch(value: Any) -> Optional[int]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        try:
            iv = int(value)
        except Exception:
            return None
        if iv <= 0:
            return None
        if iv > 10**12:  # milisegundos
            iv = int(iv / 1000)
        return iv

    raw = str(value).strip()
    if not raw:
        return None

    if raw.isdigit():
        iv = int(raw)
        if iv > 10**12:
            iv = int(iv / 1000)
        return iv if iv > 0 else None

    iso_raw = raw.replace(" ", "T")
    if iso_raw.endswith("Z"):
        iso_raw = iso_raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        pass

    fmts = [
        "%Y%m%d@%H%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except Exception:
            continue
    return None


def _request_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": "MeteoLabX/1.0",
    }
    auth_headers, auth_params, auth_basic, _configured, _fingerprint = _poem_auth_config()
    headers.update(auth_headers)

    query: Dict[str, Any] = {}
    if isinstance(params, dict):
        query.update(params)
    for k, v in auth_params.items():
        if k not in query:
            query[k] = v

    response = requests.get(
        url,
        params=query,
        headers=headers,
        auth=auth_basic,
        timeout=POEM_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        auth_hint = str(response.headers.get("WWW-Authenticate", "")).strip()
        body_snippet = str(response.text or "").strip().replace("\n", " ")
        if len(body_snippet) > 180:
            body_snippet = body_snippet[:180] + "..."
        detail = f"{response.status_code} {response.reason}"
        if auth_hint:
            detail += f" | WWW-Authenticate: {auth_hint}"
        if body_snippet:
            detail += f" | body: {body_snippet}"
        raise requests.HTTPError(detail, response=response)
    response.raise_for_status()
    content_type = str(response.headers.get("Content-Type", "")).lower()
    if "application/json" in content_type:
        return response.json()
    try:
        return response.json()
    except Exception:
        return json.loads(response.text)


@lru_cache(maxsize=2)
def _load_stations(path: str = "data_estaciones_poem.json") -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _find_station(station_code: str) -> Dict[str, Any]:
    target = _normalize_station_token(station_code)
    if not target:
        return {}
    for station in _load_stations():
        token = _normalize_station_token(station.get("codigo"))
        if token == target:
            return station
    return {}


def _station_meta(station_code: str) -> Dict[str, Any]:
    return dict(_find_station(station_code))


def _resolve_station_endpoints(station_meta: Dict[str, Any]) -> Tuple[List[str], List[str], str]:
    explicit_tr = str(station_meta.get("tr_endpoint") or "").strip()
    explicit_hourly = str(station_meta.get("hourly_endpoint") or "").strip()
    explicit_source = str(station_meta.get("endpoint_source") or "").strip()
    explicit_reason = str(station_meta.get("endpoint_reason") or "").strip()
    if explicit_tr or explicit_hourly:
        return (
            [explicit_tr] if explicit_tr else [],
            [explicit_hourly] if explicit_hourly else [],
            explicit_reason or explicit_source or "catalogo-local",
        )
    return [], [], "sin-endpoint-en-catalogo"


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


def _unit_is_mps(unit_hint: str) -> bool:
    unit = _normalize_text(unit_hint)
    return ("m/s" in unit) or ("m s-1" in unit) or ("m.s-1" in unit)


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


def _extract_metric_updates(items: Sequence[Tuple[str, Any]]) -> Dict[str, float]:
    updates: Dict[str, float] = {}
    norm_keys = [_normalize_key(key) for key, _ in items]
    has_dv = any(k.startswith("dv_") or k == "dv" for k in norm_keys)
    has_vv = any(k.startswith("vv_") or k == "vv" for k in norm_keys)

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
        if key_norm.startswith("qc_"):
            continue
        val = _safe_float(value)
        if _is_nan(val):
            continue
        kind = _kind_from_hint(_normalize_text(key_norm), key_norm=key_norm)
        if not kind:
            continue
        if kind == "wind_dir" and key_norm.startswith("dmd") and has_dv:
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
            if abs(val) > 100.0:
                val = val / 10.0
        elif key_norm in {"pa", "ps", "pres", "presion"}:
            if abs(val) > 2000.0:
                val = val / 10.0
            if abs(val) > 2000.0:
                val = val / 100.0
        elif key_norm.startswith("vv_") or key_norm in {"vvmd", "vvmx"}:
            if abs(val) >= 100.0:
                val = val / 100.0
            elif abs(val) > 25.0:
                val = val / 10.0
            val = val * 3.6

        if kind in ("wind", "gust") and _key_implies_meters_per_second(key_norm):
            val = val * 3.6
        updates[kind] = val

    # Normalizaciones por variable.
    if "temp" in updates:
        updates["temp"] = _normalize_temp(updates["temp"])
    if "pressure_abs" in updates:
        updates["pressure_abs"] = _normalize_pressure(updates["pressure_abs"], "pa")
    if "pressure_msl" in updates:
        updates["pressure_msl"] = _normalize_pressure(updates["pressure_msl"], "pa")

    return updates


def _rows_to_series(
    rows: List[Dict[str, Any]],
    station_code: str,
) -> Dict[str, Any]:
    by_epoch: Dict[int, Dict[str, float]] = {}
    now_epoch = int(datetime.now(timezone.utc).timestamp())

    for row in rows:
        items = list(_iter_scalar_items(row))
        if not items:
            continue
        if not _row_matches_station(items, station_code):
            continue

        updates = _extract_metric_updates(items)
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


@st.cache_data(ttl=300)
def fetch_poem_endpoint_series(endpoint: str, station_code: str, auth_cache_key: str = "") -> Dict[str, Any]:
    _ = auth_cache_key  # participa en key de caché para invalidar al cambiar auth
    sid = str(station_code).strip()
    url = f"{POEM_BASE_URL}{endpoint}"
    sid_param: Any = sid
    if sid.isdigit():
        try:
            sid_param = int(sid)
        except Exception:
            sid_param = sid
    try:
        payload = _request_json(
            url,
            params={
                "codigo": sid_param,
                "OrderBy": "fecha.desc",
                "Limit": 1000,
            },
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "series": {}, "endpoint": endpoint}

    rows = _extract_rows(payload)
    series = _rows_to_series(rows, sid)
    series = _trim_series_window(series)
    latest_epoch = _latest_epoch(series)
    if latest_epoch is not None:
        age_seconds = int(datetime.now(timezone.utc).timestamp()) - int(latest_epoch)
        if age_seconds > POEM_TR_MAX_AGE_SECONDS:
            latest_iso = datetime.fromtimestamp(latest_epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            series = dict(series)
            series["has_data"] = False
            return {
                "ok": False,
                "error": f"Serie obsoleta (ultima={latest_iso})",
                "series": series,
                "endpoint": endpoint,
            }

    if series.get("has_data"):
        return {"ok": True, "error": "", "series": series, "endpoint": endpoint}
    return {"ok": False, "error": f"Serie vacia (rows={len(rows)})", "series": series, "endpoint": endpoint}


def _valid_count(values: Sequence[float]) -> int:
    return sum(1 for v in values if not _is_nan(_safe_float(v)))


def _median_step_seconds(epochs: Sequence[int]) -> Optional[float]:
    if len(epochs) < 2:
        return None
    diffs = []
    for i in range(1, len(epochs)):
        d = int(epochs[i]) - int(epochs[i - 1])
        if d > 0:
            diffs.append(float(d))
    if not diffs:
        return None
    return float(median(diffs))


def _series_score(series: Dict[str, Any]) -> Tuple[int, int, float]:
    epochs = series.get("epochs", [])
    coverage = 0
    for k in ("temps", "humidities", "pressures_abs", "winds", "gusts", "wind_dirs"):
        if _valid_count(series.get(k, [])) > 0:
            coverage += 1
    points = len(epochs)
    step_s = _median_step_seconds(epochs)
    resolution_score = 0.0 if step_s is None or step_s <= 0 else (1.0 / step_s)
    return coverage, points, resolution_score


def _series_span_hours(series: Dict[str, Any]) -> float:
    epochs = series.get("epochs", [])
    if len(epochs) < 2:
        return 0.0
    return max(0.0, float(epochs[-1] - epochs[0]) / 3600.0)


def _pick_best_series(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    valid = [c for c in candidates if isinstance(c, dict) and c.get("has_data")]
    if not valid:
        return {}
    return max(valid, key=_series_score)


def _pick_graph_series(hourly_series: Dict[str, Any], tr_series: Dict[str, Any]) -> Dict[str, Any]:
    has_hourly = bool(hourly_series.get("has_data"))
    has_tr = bool(tr_series.get("has_data"))
    if has_hourly and not has_tr:
        return hourly_series
    if has_tr and not has_hourly:
        return tr_series
    if not has_hourly and not has_tr:
        return {}

    hourly_score = _series_score(hourly_series)
    tr_score = _series_score(tr_series)

    if tr_score[0] > hourly_score[0]:
        return tr_series
    if hourly_score[0] > tr_score[0]:
        return hourly_series

    hourly_step = _median_step_seconds(hourly_series.get("epochs", []))
    tr_step = _median_step_seconds(tr_series.get("epochs", []))
    if tr_step and hourly_step and tr_step <= (hourly_step * 0.8):
        return tr_series

    if hourly_score[1] >= tr_score[1]:
        return hourly_series
    return tr_series


def _last_valid(values: Sequence[float]) -> float:
    for value in reversed(list(values)):
        fv = _safe_float(value)
        if not _is_nan(fv):
            return fv
    return float("nan")


def _today_window_epoch() -> Tuple[int, int]:
    now_local = datetime.now()
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def _today_values(values: Sequence[float], epochs: Sequence[int]) -> List[float]:
    day_start, day_end = _today_window_epoch()
    out: List[float] = []
    for ep, value in zip(epochs, values):
        epoch_i = int(ep)
        if epoch_i < day_start or epoch_i >= day_end:
            continue
        fv = _safe_float(value)
        if not _is_nan(fv):
            out.append(float(fv))
    return out


def _max_valid(values: Sequence[float]) -> float:
    clean = [_safe_float(v) for v in values]
    clean = [v for v in clean if not _is_nan(v)]
    return max(clean) if clean else float("nan")


def _min_valid(values: Sequence[float]) -> float:
    clean = [_safe_float(v) for v in values]
    clean = [v for v in clean if not _is_nan(v)]
    return min(clean) if clean else float("nan")


def _pressure_3h_reference(epochs: Sequence[int], pressures_msl: Sequence[float]) -> Tuple[float, Optional[int], Optional[int]]:
    valid = [
        (int(ep), float(p))
        for ep, p in zip(epochs, pressures_msl)
        if not _is_nan(_safe_float(p))
    ]
    if len(valid) < 2:
        return float("nan"), None, None
    valid.sort(key=lambda item: item[0])
    ep_now, _p_now = valid[-1]
    target_ep = ep_now - (3 * 3600)
    ep_old, p_old = min(valid, key=lambda item: abs(item[0] - target_ep))
    return p_old, ep_old, ep_now


def _with_default_series(series: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(series, dict):
        return {}
    epochs = [int(ep) for ep in series.get("epochs", [])]
    out = {
        "epochs": epochs,
        "temps": [float(v) for v in series.get("temps", [])],
        "humidities": [float(v) for v in series.get("humidities", [])],
        "pressures_abs": [float(v) for v in series.get("pressures_abs", [])],
        "winds": [float(v) for v in series.get("winds", [])],
        "gusts": [float(v) for v in series.get("gusts", [])],
        "wind_dirs": [float(v) for v in series.get("wind_dirs", [])],
        "solar_radiations": [float(v) for v in series.get("solar_radiations", [])],
        "has_data": bool(series.get("has_data", False)),
    }
    return out


def is_poem_connection() -> bool:
    return str(st.session_state.get("connection_type", "")).strip().upper() == "POEM"


def get_poem_data() -> Optional[Dict[str, Any]]:
    if not is_poem_connection():
        return None

    st.session_state["poem_last_error"] = ""
    station_id = (
        st.session_state.get("poem_station_id")
        or st.session_state.get("provider_station_id")
        or ""
    )
    station_id = str(station_id).strip()
    if not station_id:
        st.session_state["poem_last_error"] = "station_id vacío"
        return None

    station_meta = _find_station(station_id)
    station_name = str(station_meta.get("nombre", "")).strip() or station_id
    station_lat = _safe_float(station_meta.get("lat"))
    station_lon = _safe_float(station_meta.get("lon"))
    station_type = str(station_meta.get("tipo", "")).strip()
    elevation = 0.0

    auth_headers, _auth_params, _auth_basic, auth_configured, auth_fingerprint = _poem_auth_config()
    _ = auth_headers  # solo para evaluar configuración y fingerprint
    runtime_cache_key = f"{station_id}|{auth_fingerprint}"
    runtime_cache = st.session_state.get("_poem_runtime_cache", {})
    if isinstance(runtime_cache, dict):
        cached = runtime_cache.get(runtime_cache_key)
        if isinstance(cached, dict):
            ts = _safe_float(cached.get("ts"), default=0.0)
            age = time.time() - ts
            cached_data = cached.get("data")
            # Éxito: TTL mayor para evitar recalcular en cada rerun.
            if isinstance(cached_data, dict) and age >= 0 and age < 45:
                cached_error = str(cached.get("error", "")).strip()
                if cached_error:
                    st.session_state["poem_last_error"] = cached_error
                return dict(cached_data)
            # Error/None: TTL corto para permitir recuperación rápida.
            if cached_data is None and age >= 0 and age < 8:
                cached_error = str(cached.get("error", "")).strip()
                if cached_error:
                    st.session_state["poem_last_error"] = cached_error
                return None

    try:
        station_meta = _station_meta(station_id)
    except Exception:
        station_meta = dict(station_meta)
    station_name = str(station_meta.get("nombre", "")).strip() or station_name
    station_lat = _safe_float(
        station_meta.get("lat", station_meta.get("latitud")),
        default=station_lat,
    )
    station_lon = _safe_float(
        station_meta.get("lon", station_meta.get("longitud")),
        default=station_lon,
    )
    station_type = str(station_meta.get("tipo", "")).strip() or station_type
    tr_endpoints, hourly_endpoints, feed_reason = _resolve_station_endpoints(station_meta)
    if not tr_endpoints and not hourly_endpoints:
        error_text = f"Sin endpoint POEM verificado para {station_id} ({feed_reason})"
        st.session_state["poem_last_error"] = error_text
        if isinstance(runtime_cache, dict):
            runtime_cache[runtime_cache_key] = {"ts": time.time(), "data": None, "error": error_text}
            st.session_state["_poem_runtime_cache"] = runtime_cache
        return None

    tr_candidates: List[Dict[str, Any]] = []
    debug_attempts: List[str] = []
    for endpoint in tr_endpoints:
        fetched = fetch_poem_endpoint_series(endpoint, station_id, auth_cache_key=auth_fingerprint)
        ok = bool(fetched.get("ok")) if isinstance(fetched, dict) else False
        err = str(fetched.get("error", "")).strip() if isinstance(fetched, dict) else ""
        series = fetched.get("series", {}) if isinstance(fetched, dict) else {}
        if isinstance(series, dict):
            if series.get("has_data"):
                series = dict(series)
                series["_source_endpoint"] = endpoint
            debug_attempts.append(
                f"TR {endpoint}: {'OK' if ok else 'NO'}"
                + (f" ({err})" if (not ok and err) else "")
            )
            tr_candidates.append(series)
            if series.get("has_data"):
                break

    hourly_candidates: List[Dict[str, Any]] = []
    for endpoint in hourly_endpoints:
        fetched = fetch_poem_endpoint_series(endpoint, station_id, auth_cache_key=auth_fingerprint)
        ok = bool(fetched.get("ok")) if isinstance(fetched, dict) else False
        err = str(fetched.get("error", "")).strip() if isinstance(fetched, dict) else ""
        series = fetched.get("series", {}) if isinstance(fetched, dict) else {}
        if isinstance(series, dict):
            if series.get("has_data"):
                series = dict(series)
                series["_source_endpoint"] = endpoint
            debug_attempts.append(
                f"HOR {endpoint}: {'OK' if ok else 'NO'}"
                + (f" ({err})" if (not ok and err) else "")
            )
            hourly_candidates.append(series)
            if series.get("has_data"):
                break

    tr_series = _pick_best_series(tr_candidates)
    hourly_series = _pick_best_series(hourly_candidates)
    chart_series = _pick_graph_series(hourly_series, tr_series)

    span_ranking = []
    for s in [tr_series, hourly_series, chart_series]:
        if isinstance(s, dict) and s.get("has_data"):
            span_ranking.append(s)
    trend_series = max(span_ranking, key=_series_span_hours) if span_ranking else {}

    current_series = tr_series if tr_series.get("has_data") else chart_series
    if not current_series.get("has_data"):
        error_text = " | ".join(debug_attempts[:6]) or "Sin datos en endpoints POEM"
        if (not auth_configured) and ("401" in error_text or "Unauthorized" in error_text):
            error_text = f"{error_text} | {_poem_auth_help()}"
        st.session_state["poem_last_error"] = error_text
        if isinstance(runtime_cache, dict):
            runtime_cache[runtime_cache_key] = {"ts": time.time(), "data": None, "error": error_text}
            st.session_state["_poem_runtime_cache"] = runtime_cache
        return None

    epochs = current_series.get("epochs", [])
    if not epochs:
        st.session_state["poem_last_error"] = "Serie sin epochs útiles"
        if isinstance(runtime_cache, dict):
            runtime_cache[runtime_cache_key] = {"ts": time.time(), "data": None, "error": "Serie sin epochs útiles"}
            st.session_state["_poem_runtime_cache"] = runtime_cache
        return None

    temps = current_series.get("temps", [])
    rhs = current_series.get("humidities", [])
    p_abs_series = current_series.get("pressures_abs", [])
    p_msl_series = current_series.get("pressures_msl", [])
    winds = current_series.get("winds", [])
    gusts = current_series.get("gusts", [])
    dirs = current_series.get("wind_dirs", [])
    precs = current_series.get("precips", [])
    lats = current_series.get("lats", [])
    lons = current_series.get("lons", [])

    temp_now = _last_valid(temps)
    rh_now = _last_valid(rhs)
    p_abs_now = _last_valid(p_abs_series)
    p_msl_now = _last_valid(p_msl_series)
    if _is_nan(p_abs_now) and not _is_nan(p_msl_now):
        p_abs_now = float(p_msl_now) / math.exp(float(elevation) / 8000.0)
    if _is_nan(p_msl_now) and not _is_nan(p_abs_now):
        p_msl_now = float(p_abs_now) * math.exp(float(elevation) / 8000.0)

    wind_now = _last_valid(winds)
    gust_now = _last_valid(gusts)
    wind_dir_now = _last_valid(dirs)

    lat_now = _last_valid(lats)
    lon_now = _last_valid(lons)
    if _is_nan(lat_now):
        lat_now = station_lat
    if _is_nan(lon_now):
        lon_now = station_lon

    base_epoch = int(epochs[-1])

    series_for_stats = chart_series if chart_series.get("has_data") else current_series

    chart_epochs = series_for_stats.get("epochs", [])
    chart_temps = series_for_stats.get("temps", [])
    chart_rhs = series_for_stats.get("humidities", [])
    chart_gusts = series_for_stats.get("gusts", [])
    chart_precs = series_for_stats.get("precips", [])
    chart_p_abs = series_for_stats.get("pressures_abs", [])
    chart_p_msl = series_for_stats.get("pressures_msl", [])

    if _valid_count(chart_p_msl) == 0:
        chart_p_msl = [
            (float(p) * math.exp(float(elevation) / 8000.0)) if not _is_nan(_safe_float(p)) else float("nan")
            for p in chart_p_abs
        ]

    temp_today = _today_values(chart_temps, chart_epochs)
    rh_today = _today_values(chart_rhs, chart_epochs)
    gust_today = _today_values(chart_gusts, chart_epochs)
    prec_today_vals = _today_values(chart_precs, chart_epochs)

    temp_max = _max_valid(temp_today)
    temp_min = _min_valid(temp_today)
    rh_max = _max_valid(rh_today)
    rh_min = _min_valid(rh_today)
    gust_max = _max_valid(gust_today)

    precip_total = float("nan")
    if prec_today_vals:
        precip_total = float(sum(max(0.0, _safe_float(v)) for v in prec_today_vals))

    pressure_3h_ago, epoch_3h_ago, _epoch_now_ref = _pressure_3h_reference(chart_epochs, chart_p_msl)

    trend_norm = _with_default_series(trend_series)
    chart_norm = _with_default_series(chart_series)
    if not chart_norm.get("has_data"):
        chart_norm = _with_default_series(current_series)

    base = {
        "idema": station_id,
        "station_code": station_id,
        "station_name": station_name,
        "station_type": station_type,
        "lat": _safe_float(lat_now),
        "lon": _safe_float(lon_now),
        "elevation": float(elevation),
        "epoch": int(base_epoch),
        "Tc": temp_now,
        "RH": rh_now,
        "Td": float("nan"),
        "p_hpa": p_msl_now,
        "p_abs_hpa": p_abs_now,
        "pressure_3h_ago": pressure_3h_ago,
        "epoch_3h_ago": epoch_3h_ago,
        "wind": wind_now,
        "gust": gust_now,
        "wind_dir_deg": wind_dir_now,
        "precip_total": precip_total,
        "solar_radiation": float("nan"),
        "uv": float("nan"),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "temp_max": temp_max,
        "temp_min": temp_min,
        "rh_max": rh_max,
        "rh_min": rh_min,
        "gust_max": gust_max,
        "_series": chart_norm,
        "_series_7d": {
            "epochs": trend_norm.get("epochs", []),
            "temps": trend_norm.get("temps", []),
            "humidities": trend_norm.get("humidities", []),
            "pressures_abs": trend_norm.get("pressures_abs", []),
            "has_data": bool(trend_norm.get("has_data", False)),
        },
    }
    if isinstance(runtime_cache, dict):
        runtime_cache[runtime_cache_key] = {"ts": time.time(), "data": dict(base), "error": ""}
        st.session_state["_poem_runtime_cache"] = runtime_cache
    return base
