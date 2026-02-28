"""
Servicio para integrar observaciones de Euskalmet.
"""
import json
import os
import time
import base64
import subprocess
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
import streamlit as st


BASE_URL = os.getenv("EUSKALMET_BASE_URL", "https://api.euskadi.eus")
EUSKALMET_API_KEY = os.getenv(
    "EUSKALMET_API_KEY",
    "2a57be1a9e3974f95b65140b683f16304ada51552ab2664358302b81ca470163",
)
EUSKALMET_API_KEY_PUBLIC = os.getenv("EUSKALMET_API_KEY_PUBLIC", "")
EUSKALMET_API_KEY_PRIVATE = os.getenv("EUSKALMET_API_KEY_PRIVATE", "")
EUSKALMET_JWT = os.getenv("EUSKALMET_JWT", "")
EUSKALMET_JWT_AUD = os.getenv("EUSKALMET_JWT_AUD", "met01.apikey")
EUSKALMET_JWT_ISS = os.getenv("EUSKALMET_JWT_ISS", "")
EUSKALMET_JWT_VERSION = os.getenv("EUSKALMET_JWT_VERSION", "1.0.0")
EUSKALMET_JWT_EMAIL = os.getenv("EUSKALMET_JWT_EMAIL", "meteolabx@gmail.com")
EUSKALMET_JWT_LOGIN_ID = os.getenv("EUSKALMET_JWT_LOGIN_ID", "")
EUSKALMET_PRIVATE_KEY_PATH = os.getenv("EUSKALMET_PRIVATE_KEY_PATH", "")
EUSKALMET_PUBLIC_KEY_PATH = os.getenv("EUSKALMET_PUBLIC_KEY_PATH", "")
EUSKALMET_SENSORS_PATH = os.getenv("EUSKALMET_SENSORS_PATH", "")
EUSKALMET_SENSOR_MAP_PATH = os.getenv(
    "EUSKALMET_SENSOR_MAP_PATH",
    "data_station_sensor_map_euskalmet.json",
)
EUSKALMET_STRICT_SENSOR_MAP = os.getenv("EUSKALMET_STRICT_SENSOR_MAP", "1") == "1"
TIMEOUT_SECONDS = 12
DISCOVERY_TIMEOUT_SECONDS = float(os.getenv("EUSKALMET_DISCOVERY_TIMEOUT_S", "2.5"))
DISCOVERY_MAX_CANDIDATES = int(os.getenv("EUSKALMET_DISCOVERY_MAX_CANDIDATES", "20"))
DISCOVERY_BUDGET_SECONDS = float(os.getenv("EUSKALMET_DISCOVERY_BUDGET_S", "6.0"))
LOCAL_TZ = ZoneInfo("Europe/Madrid")


MEASURE_SPECS = {
    "temp": ("measuresForAir", "temperature"),
    "rh": ("measuresForAir", "humidity"),
    "pressure_abs": ("measuresForAtmosphere", "pressure"),
    "pressure_msl": ("measuresForAtmosphere", "sea_level_pressure"),
    "wind": ("measuresForWind", "mean_speed"),
    "gust": ("measuresForWind", "max_speed"),
    "wind_dir": ("measuresForWind", "mean_direction"),
    "precip": ("measuresForWater", "precipitation"),
    "solar": ("measuresForSun", "irradiance"),
}

MEASURE_SENSOR_PREFIXES: Dict[Tuple[str, str], List[str]] = {
    ("measuresForAir", "temperature"): ["TA"],
    ("measuresForAir", "humidity"): ["HA"],
    ("measuresForAtmosphere", "pressure"): ["PA", "CT", "CB"],
    ("measuresForAtmosphere", "sea_level_pressure"): ["PA", "CT", "CB"],
    ("measuresForWind", "mean_direction"): ["DV"],
    ("measuresForWind", "mean_speed"): ["VV", "DV"],
    ("measuresForWind", "max_speed"): ["VV", "DV"],
    ("measuresForWater", "precipitation"): ["PL", "PR", "PP", "RR"],
    ("measuresForSun", "irradiance"): ["SP", "RS", "SR"],
}


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


@lru_cache(maxsize=8)
def _read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _measure_key(measure_type: str, measure_id: str) -> str:
    return f"{str(measure_type).strip()}/{str(measure_id).strip()}"


@lru_cache(maxsize=1)
def _load_station_sensor_map() -> Dict[str, Dict[str, str]]:
    path = str(EUSKALMET_SENSOR_MAP_PATH).strip()
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return {}
        out: Dict[str, Dict[str, str]] = {}
        for stid, mapping in payload.items():
            if not isinstance(mapping, dict):
                continue
            cleaned: Dict[str, str] = {}
            for mkey, sid in mapping.items():
                ms = str(mkey).strip()
                sv = str(sid).strip().upper()
                if ms and sv:
                    cleaned[ms] = sv
            if cleaned:
                out[str(stid).strip().upper()] = cleaned
        return out
    except Exception:
        return {}


def _save_station_sensor_map(sensor_map: Dict[str, Dict[str, str]]) -> None:
    path = str(EUSKALMET_SENSOR_MAP_PATH).strip()
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sensor_map, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        return


def _remember_sensor_mapping(station_id: str, measure_type: str, measure_id: str, sensor_id: str) -> None:
    sid = str(station_id).strip().upper()
    srs = str(sensor_id).strip().upper()
    if not sid or not srs:
        return
    mkey = _measure_key(measure_type, measure_id)
    sensor_map = _load_station_sensor_map()
    station_map = sensor_map.get(sid, {})
    if station_map.get(mkey) == srs:
        return
    station_map[mkey] = srs
    sensor_map[sid] = station_map
    _save_station_sensor_map(sensor_map)
    _load_station_sensor_map.cache_clear()


def _remembered_sensor(station_id: str, measure_type: str, measure_id: str) -> str:
    sid = str(station_id).strip().upper()
    if not sid:
        return ""
    mkey = _measure_key(measure_type, measure_id)
    sensor_map = _load_station_sensor_map()
    return str(sensor_map.get(sid, {}).get(mkey, "")).strip().upper()


def _sort_sensors_for_measure(sensors: List[str], measure_type: str, measure_id: str) -> List[str]:
    wanted = MEASURE_SENSOR_PREFIXES.get((measure_type, measure_id), [])
    if not wanted:
        return [str(s).strip().upper() for s in sensors if str(s).strip()]
    prioritized: List[str] = []
    fallback: List[str] = []
    for raw in sensors:
        sid = str(raw).strip().upper()
        if not sid:
            continue
        if any(sid.startswith(pref) for pref in wanted):
            prioritized.append(sid)
        else:
            fallback.append(sid)
    return prioritized + fallback


@lru_cache(maxsize=4)
def _load_sensor_inventory_ids(path_hint: str = "") -> List[str]:
    candidates = [
        str(path_hint or "").strip(),
        str(EUSKALMET_SENSORS_PATH).strip(),
        "data_sensors_euskalmet.json",
        "data_sensores_euskalmet.json",
        "/Users/joantisdale/Downloads/sensors.json",
        "sensors.json",
    ]
    for p in candidates:
        if not p or not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, list):
                continue
            out: List[str] = []
            seen = set()
            for item in payload:
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("sensorId", "")).strip().upper()
                if not sid:
                    sid = _sensor_from_ref(str(item.get("key", "")).strip())
                if sid and sid not in seen:
                    seen.add(sid)
                    out.append(sid)
            if out:
                return out
        except Exception:
            continue
    return []


def _candidate_inventory_sensors(station_id: str, measure_type: str, measure_id: str) -> List[str]:
    sid = str(station_id or "").strip().upper()
    all_ids = _load_sensor_inventory_ids()
    if not all_ids:
        return []

    groups: List[List[str]] = []

    if sid:
        # Mapeo heurístico frecuente en estaciones C0XY -> CB0Y (ej: C0B9 -> CB09).
        if len(sid) == 4 and sid[0].isalpha() and sid[1].isdigit() and sid[2].isalpha() and sid[3].isdigit():
            direct = f"{sid[0]}{sid[2]}0{sid[3]}"
            groups.append([x for x in all_ids if x == direct])
        groups.append([x for x in all_ids if x.startswith(sid)])
        if len(sid) >= 3 and sid[2].isalpha():
            p = sid[0] + sid[2]
            groups.append([x for x in all_ids if x.startswith(p)])
        if len(sid) >= 2:
            groups.append([x for x in all_ids if x.startswith(sid[:2])])
        groups.append([x for x in all_ids if x.startswith(sid[:1])])
        if len(sid) >= 2:
            sfx = sid[-2:]
            groups.append([x for x in all_ids if x.endswith(sfx)])

    if measure_type == "measuresForWind":
        groups.append([x for x in all_ids if x.startswith("DV")])
    if measure_type == "measuresForSun":
        groups.append([x for x in all_ids if x.startswith("SR") or x.startswith("RS")])
    if measure_type == "measuresForAtmosphere":
        groups.append([x for x in all_ids if x.startswith("CT") or x.startswith("CB")])

    ordered: List[str] = []
    seen = set()
    for g in groups:
        for x in g:
            xu = str(x).upper()
            if xu in seen:
                continue
            seen.add(xu)
            ordered.append(xu)

    # Limitar el barrido para no degradar la latencia de conexión.
    return ordered[:max(4, DISCOVERY_MAX_CANDIDATES)]


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_private_key_path() -> str:
    candidates = [
        str(st.session_state.get("euskalmet_private_key_path", "")).strip(),
        str(EUSKALMET_PRIVATE_KEY_PATH).strip(),
        os.path.join(_PROJECT_ROOT, "keys", "euskalmet", "privateKey.pem"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return ""


def _resolve_public_key_path() -> str:
    candidates = [
        str(st.session_state.get("euskalmet_public_key_path", "")).strip(),
        str(EUSKALMET_PUBLIC_KEY_PATH).strip(),
        os.path.join(_PROJECT_ROOT, "keys", "euskalmet", "publicKey.pem"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return ""


def _resolve_identity_email() -> str:
    for key_name in ("euskalmet_email",):
        val = str(st.session_state.get(key_name, "")).strip()
        if val:
            return val
    return str(EUSKALMET_JWT_EMAIL).strip()


def _resolve_identity_iss() -> str:
    for key_name in ("euskalmet_iss",):
        val = str(st.session_state.get(key_name, "")).strip()
        if val:
            return val
    env_iss = str(EUSKALMET_JWT_ISS).strip()
    if env_iss:
        return env_iss
    email = _resolve_identity_email()
    if email and "@" in email:
        return email.split("@", 1)[0]
    return "meteolabx"


def _resolve_login_id() -> str:
    for key_name in ("euskalmet_login_id",):
        val = str(st.session_state.get(key_name, "")).strip()
        if val:
            return val
    return str(EUSKALMET_JWT_LOGIN_ID).strip()


@lru_cache(maxsize=16)
def _build_auto_jwt_cached(
    bucket_epoch: int,
    private_key_path: str,
    iss: str,
    email: str,
    aud: str,
    version: str,
    login_id: str,
) -> str:
    if not private_key_path:
        return ""
    now = int(bucket_epoch)
    payload: Dict[str, Any] = {
        "aud": aud,
        "iss": iss,
        "version": version,
        "iat": now,
        "exp": now + 3600,
    }
    if email:
        payload["email"] = email
    if login_id:
        payload["loginId"] = login_id

    header = {"alg": "RS256", "typ": "JWT"}
    header_b64 = _b64url(json.dumps(header, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    try:
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", private_key_path],
            input=signing_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except Exception:
        return ""

    if proc.returncode != 0 or not proc.stdout:
        return ""

    signature_b64 = _b64url(proc.stdout)
    return f"{header_b64}.{payload_b64}.{signature_b64}"


def _generate_auto_jwt() -> str:
    private_key_path = _resolve_private_key_path()
    if not private_key_path:
        return ""
    bucket = int(time.time() // 3600 * 3600)
    return _build_auto_jwt_cached(
        bucket_epoch=bucket,
        private_key_path=private_key_path,
        iss=_resolve_identity_iss() or "meteolabx",
        email=_resolve_identity_email(),
        aud=str(EUSKALMET_JWT_AUD).strip() or "met01.apikey",
        version=str(EUSKALMET_JWT_VERSION).strip() or "1.0.0",
        login_id=_resolve_login_id(),
    )


def _resolve_jwt(jwt: Optional[str] = None) -> str:
    if jwt is not None and str(jwt).strip():
        return str(jwt).strip()
    session_jwt = str(st.session_state.get("euskalmet_jwt", "")).strip()
    if session_jwt:
        return session_jwt
    env_jwt = str(EUSKALMET_JWT).strip()
    if env_jwt:
        return env_jwt
    return _generate_auto_jwt()


def _resolve_api_key(api_key: Optional[str] = None) -> str:
    if api_key is not None and str(api_key).strip():
        return str(api_key).strip()
    for key_name in (
        "euskalmet_api_key",
        "euskalmet_api_key_private",
        "euskalmet_api_key_public",
    ):
        session_key = str(st.session_state.get(key_name, "")).strip()
        if session_key:
            return session_key
    for env_key in (EUSKALMET_API_KEY_PRIVATE, EUSKALMET_API_KEY, EUSKALMET_API_KEY_PUBLIC):
        val = str(env_key).strip()
        if val:
            return val
    return ""


def _resolve_public_key() -> str:
    for key_name in ("euskalmet_api_key_public",):
        session_key = str(st.session_state.get(key_name, "")).strip()
        if session_key:
            return session_key
    return str(EUSKALMET_API_KEY_PUBLIC).strip()


def _request_json(
    path: str,
    jwt: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout_s: Optional[float] = None,
) -> Any:
    token = _resolve_jwt(jwt)
    key = _resolve_api_key(api_key)
    public_key = _resolve_public_key()
    url = f"{BASE_URL.rstrip('/')}/{path.lstrip('/')}"

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if key:
        headers["apikey"] = key
        headers["x-api-key"] = key
        headers["X-API-Key"] = key
    if public_key:
        headers["x-api-key-id"] = public_key
        headers["X-API-Key-Id"] = public_key

    response = requests.get(url, headers=headers, timeout=float(timeout_s or TIMEOUT_SECONDS))
    if response.status_code >= 400:
        txt = (response.text or "").strip().replace("\n", " ")
        if len(txt) > 240:
            txt = txt[:240] + "..."
        raise requests.HTTPError(f"{response.status_code} {response.reason} | {path} | {txt}")
    return response.json()


def _sensor_from_ref(ref: str, station_id: str = "") -> str:
    txt = str(ref or "").strip().strip("/")
    if not txt:
        return ""
    parts = [p for p in txt.split("/") if p]
    low_parts = [p.lower() for p in parts]

    if "sensors" in low_parts:
        idx = low_parts.index("sensors")
        if idx + 1 < len(parts):
            return parts[idx + 1].upper()

    if "forstation" in low_parts:
        idx = low_parts.index("forstation")
        if idx + 2 < len(parts):
            return parts[idx + 2].upper()

    # Fallback conservador: códigos tipo DV05, AN12, etc.
    for p in reversed(parts):
        up = p.upper()
        if station_id and up == station_id.upper():
            continue
        if len(up) == 4 and up[:2].isalpha() and up[2:].isdigit():
            return up
    return ""


def _extract_sensor_ids(payload: Any, station_id: str = "") -> List[str]:
    ids: List[str] = []
    items: List[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("sensors", "sensor", "items", "data", "results"):
            v = payload.get(key)
            if isinstance(v, list):
                items.extend(v)
            elif isinstance(v, dict):
                items.append(v)
        items.append(payload)

    for item in items:
        if isinstance(item, str):
            sid = _sensor_from_ref(item, station_id=station_id)
            if sid:
                ids.append(sid)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("sensorId", "sensor_id", "sensor", "sensorCode", "code"):
            sid = str(item.get(key, "")).strip()
            if sid and len(sid) <= 16 and sid.upper() != str(station_id).upper():
                ids.append(sid)
        for key in ("sensorKey", "sensor_key", "oid", "key"):
            ref = str(item.get(key, "")).strip()
            sid = _sensor_from_ref(ref, station_id=station_id)
            if sid:
                ids.append(sid)
    out: List[str] = []
    seen = set()
    for sid in ids:
        sid_u = sid.upper()
        if sid_u not in seen:
            seen.add(sid_u)
            out.append(sid_u)
    return out


@lru_cache(maxsize=2)
def _load_stations(path: str = "data_estaciones_euskalmet.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _find_station(station_id: str) -> Dict[str, Any]:
    sid = str(station_id).strip().upper()
    for station in _load_stations():
        if str(station.get("stationId", "")).strip().upper() == sid:
            return station
    return {}


def _hour_points(year: int, month: int, day: int, hour: int, values: List[Any]) -> List[Tuple[int, float]]:
    points: List[Tuple[int, float]] = []
    base = datetime(year, month, day, hour, 0, 0, tzinfo=LOCAL_TZ)
    for idx, raw in enumerate(values):
        minute = idx * 10
        dt = base + timedelta(minutes=minute)
        points.append((int(dt.timestamp()), _safe_float(raw)))
    return points


@st.cache_data(ttl=600)
def fetch_station_sensors(station_id: str, jwt: Optional[str] = None, api_key: Optional[str] = None) -> Dict[str, Any]:
    sid = str(station_id).strip().upper()
    if not sid:
        return {"ok": False, "error": "station_id vacío", "sensors": []}

    station_meta = _find_station(sid)
    meta_paths: List[str] = []
    for key in ("key", "oid"):
        raw = str(station_meta.get(key, "")).strip()
        if raw:
            meta_paths.append(raw.lstrip("/"))

    candidates = [
        f"euskalmet/stations/{sid}/current",
        f"euskalmet/stations/{sid}",
        f"euskalmet/stations/{sid}/sensors",
        f"euskalmet/stations/{sid}/sensor",
        f"euskalmet/sensors/forStation/{sid}",
    ]
    for base in meta_paths:
        candidates.extend(
            [
                base,
                f"{base}/sensors",
                f"{base}/sensor",
            ]
        )

    # Deduplicar manteniendo orden.
    seen_paths = set()
    ordered_candidates = []
    for p in candidates:
        if p not in seen_paths:
            seen_paths.add(p)
            ordered_candidates.append(p)

    errors: List[str] = []
    for path in ordered_candidates:
        try:
            payload = _request_json(path, jwt=jwt, api_key=api_key)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
            continue
        sensors = _extract_sensor_ids(payload, station_id=sid)
        if sensors:
            # Mantener el orden de descubrimiento: suele reflejar mejor sensores principales.
            unique: List[str] = []
            seen = set()
            for s in sensors:
                su = str(s).strip().upper()
                if su and su not in seen:
                    seen.add(su)
                    unique.append(su)
            return {"ok": True, "sensors": unique}
    err = "No se pudo resolver lista de sensores"
    if errors:
        err += " | " + " || ".join(errors[:2])
    return {"ok": False, "error": err, "sensors": []}


@st.cache_data(ttl=600)
def fetch_hourly_reading(
    station_id: str,
    sensor_id: str,
    measure_type: str,
    measure_id: str,
    year: int,
    month: int,
    day: int,
    hour: int,
    jwt: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    path = (
        f"euskalmet/readings/forStation/{station_id}/{sensor_id}/measures/"
        f"{measure_type}/{measure_id}/at/{year:04d}/{month:02d}/{day:02d}/{hour:02d}"
    )
    try:
        payload = _request_json(path, jwt=jwt, api_key=api_key, timeout_s=timeout_s)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "points": []}

    data = payload
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            data = data[0]
        else:
            data = {"values": data}
    if not isinstance(data, dict):
        data = {}

    values = data.get("values", [])
    if not isinstance(values, list):
        for alt in ("lectures", "datos", "data"):
            v = data.get(alt)
            if isinstance(v, list):
                values = v
                break
    if not isinstance(values, list):
        values = []
    if not isinstance(values, list):
        values = []

    points = _hour_points(year, month, day, hour, values) if values else []
    return {"ok": True, "points": points}


@st.cache_data(ttl=600)
def resolve_sensor_for_measure(
    station_id: str,
    measure_type: str,
    measure_id: str,
    year: int,
    month: int,
    day: int,
    hour: int,
    jwt: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    tried: List[str] = []
    last_errors: List[str] = []

    remembered = _remembered_sensor(station_id, measure_type, measure_id)
    if not remembered:
        return {
            "ok": False,
            "error": (
                f"Sin sensor mapeado en inventario para {measure_type}/{measure_id} "
                f"(station={station_id})"
            ),
            "sensor_id": "",
            "points": [],
        }

    out = fetch_hourly_reading(
        station_id=station_id,
        sensor_id=remembered,
        measure_type=measure_type,
        measure_id=measure_id,
        year=year,
        month=month,
        day=day,
        hour=hour,
        jwt=jwt,
        api_key=api_key,
        timeout_s=DISCOVERY_TIMEOUT_SECONDS,
    )
    tried.append(remembered)
    if out.get("ok") and out.get("points"):
        return {"ok": True, "sensor_id": remembered, "points": out.get("points", [])}
    if out.get("error"):
        last_errors.append(str(out.get("error")))

    if EUSKALMET_STRICT_SENSOR_MAP:
        return {
            "ok": False,
            "error": (
                f"Sensor mapeado sin datos para {measure_type}/{measure_id} "
                f"(sensor={remembered}) | {last_errors[0] if last_errors else 'sin detalle'}"
            ),
            "sensor_id": "",
            "points": [],
        }

    return {
        "ok": False,
        "error": f"No se encontró sensor válido para medida (probados: {', '.join(tried[:8])})",
        "sensor_id": "",
        "points": [],
    }


@st.cache_data(ttl=600)
def fetch_day_measure_series(
    station_id: str,
    measure_type: str,
    measure_id: str,
    year: int,
    month: int,
    day: int,
    jwt: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    now_local = datetime.now(LOCAL_TZ)
    hours = range(0, 24 if (year, month, day) < (now_local.year, now_local.month, now_local.day) else (now_local.hour + 1))
    if not hours:
        return {"ok": False, "error": "Sin horas a consultar", "sensor_id": "", "points": []}

    # Resolver sensor con pocas horas de sondeo para no bloquear la UI.
    sensor_id = ""
    sensor_resolve_error = ""
    points: List[Tuple[int, float]] = []
    probe_hours: List[int] = []
    if hours:
        probe_hours.extend([int(hours[-1]), int(hours[0]), int(hours[len(hours) // 2])])
    probe_hours = [h for i, h in enumerate(probe_hours) if h in hours and h not in probe_hours[:i]]
    for h in probe_hours:
        resolved = resolve_sensor_for_measure(
            station_id=station_id,
            measure_type=measure_type,
            measure_id=measure_id,
            year=year,
            month=month,
            day=day,
            hour=h,
            jwt=jwt,
            api_key=api_key,
        )
        if resolved.get("ok"):
            sensor_id = str(resolved.get("sensor_id", "")).strip()
            points.extend(resolved.get("points", []))
            break
        sensor_resolve_error = str(resolved.get("error", "")).strip()
    if not sensor_id:
        return {
            "ok": False,
            "error": (
                f"Sin sensor para {measure_type}/{measure_id}. "
                f"Detalle: {sensor_resolve_error or 'n/d'}"
            ),
            "sensor_id": "",
            "points": [],
        }

    for h in hours:
        out = fetch_hourly_reading(
            station_id=station_id,
            sensor_id=sensor_id,
            measure_type=measure_type,
            measure_id=measure_id,
            year=year,
            month=month,
            day=day,
            hour=h,
            jwt=jwt,
            api_key=api_key,
        )
        if out.get("ok"):
            points.extend(out.get("points", []))

    # Deduplicar por epoch, conservar el último.
    dedup: Dict[int, float] = {}
    for ep, val in points:
        dedup[int(ep)] = float(val)
    epochs = sorted(dedup.keys())
    return {
        "ok": True,
        "sensor_id": sensor_id,
        "points": [(ep, dedup[ep]) for ep in epochs],
    }


@st.cache_data(ttl=600)
def fetch_euskalmet_day_series(
    station_id: str,
    day_local: Optional[datetime] = None,
    jwt: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    sid = str(station_id).strip().upper()
    if not sid:
        return {"ok": False, "error": "station_id vacío"}

    d = day_local.astimezone(LOCAL_TZ) if day_local else datetime.now(LOCAL_TZ)
    y, m, dd = d.year, d.month, d.day

    raw: Dict[str, Dict[str, Any]] = {}
    for key, (mtype, mid) in MEASURE_SPECS.items():
        raw[key] = fetch_day_measure_series(
            station_id=sid,
            measure_type=mtype,
            measure_id=mid,
            year=y,
            month=m,
            day=dd,
            jwt=jwt,
            api_key=api_key,
        )

    all_epochs = set()
    for item in raw.values():
        if item.get("ok"):
            all_epochs.update(ep for ep, _ in item.get("points", []))
    epochs = sorted(all_epochs)

    def _value_at(name: str, ep: int) -> float:
        points = raw.get(name, {}).get("points", [])
        idx = {int(t): float(v) for t, v in points}
        return idx.get(int(ep), float("nan"))

    temps = [_value_at("temp", ep) for ep in epochs]
    rhs = [_value_at("rh", ep) for ep in epochs]
    p_abs = [_value_at("pressure_abs", ep) for ep in epochs]
    p_msl = [_value_at("pressure_msl", ep) for ep in epochs]
    winds = [_value_at("wind", ep) for ep in epochs]
    gusts = [_value_at("gust", ep) for ep in epochs]
    dirs = [_value_at("wind_dir", ep) for ep in epochs]
    precs = [_value_at("precip", ep) for ep in epochs]
    solars = [_value_at("solar", ep) for ep in epochs]

    # Convertir viento a km/h si llega en m/s.
    winds_kmh = [float("nan") if _is_nan(v) else v * 3.6 for v in winds]
    gusts_kmh = [float("nan") if _is_nan(v) else v * 3.6 for v in gusts]

    errors: List[str] = []
    for k, item in raw.items():
        if not item.get("ok"):
            errors.append(f"{k}: {item.get('error', 'sin detalle')}")

    return {
        "ok": len(epochs) > 0,
        "station_id": sid,
        "epochs": epochs,
        "temps": temps,
        "humidities": rhs,
        "pressures_abs": p_abs,
        "pressures_msl": p_msl,
        "winds": winds_kmh,
        "gusts": gusts_kmh,
        "wind_dirs": dirs,
        "precips": precs,
        "solar_radiations": solars,
        "has_data": len(epochs) > 0,
        "error": " | ".join(errors[:3]) if errors else "",
        "raw": raw,
    }


def is_euskalmet_connection() -> bool:
    return str(st.session_state.get("connection_type", "")).strip().upper() == "EUSKALMET"


def get_euskalmet_data(jwt: Optional[str] = None, api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not is_euskalmet_connection():
        return None

    st.session_state["euskalmet_last_error"] = ""
    station_id = (
        st.session_state.get("euskalmet_station_id")
        or st.session_state.get("provider_station_id")
        or ""
    )
    station_id = str(station_id).strip().upper()
    if not station_id:
        st.session_state["euskalmet_last_error"] = "station_id vacío"
        return None

    resolved_jwt = _resolve_jwt(jwt)
    resolved_api_key = _resolve_api_key(api_key)
    if not resolved_jwt:
        st.session_state["euskalmet_last_error"] = (
            "No hay JWT disponible (manual ni autogenerado desde PEM)."
        )
        return None

    series = fetch_euskalmet_day_series(
        station_id,
        jwt=resolved_jwt,
        api_key=resolved_api_key,
    )
    if not series.get("ok"):
        st.session_state["euskalmet_last_error"] = str(series.get("error", "Serie sin datos"))
        return None

    epochs = series.get("epochs", [])
    if not epochs:
        st.session_state["euskalmet_last_error"] = "Serie vacía para la estación"
        return None

    # Último punto válido por variable.
    def _last_valid(values: List[float]) -> float:
        for v in reversed(values):
            if not _is_nan(float(v)):
                return float(v)
        return float("nan")

    station_meta = _find_station(station_id)
    lat = _safe_float(station_meta.get("lat"))
    lon = _safe_float(station_meta.get("lon"))
    elevation = _safe_float(station_meta.get("altitude_m"), default=0.0)

    temps = series.get("temps", [])
    rhs = series.get("humidities", [])
    p_abs = series.get("pressures_abs", [])
    p_msl = series.get("pressures_msl", [])
    winds = series.get("winds", [])
    gusts = series.get("gusts", [])
    dirs = series.get("wind_dirs", [])
    precs = series.get("precips", [])
    solars = series.get("solar_radiations", [])

    # Precipitación acumulada hoy como suma de incrementos.
    precip_today = sum(max(0.0, float(v)) for v in precs if not _is_nan(float(v)))

    temp_valid = [float(v) for v in temps if not _is_nan(float(v))]
    rh_valid = [float(v) for v in rhs if not _is_nan(float(v))]
    gust_valid = [float(v) for v in gusts if not _is_nan(float(v))]

    return {
        "Tc": _last_valid(temps),
        "RH": _last_valid(rhs),
        "p_hpa": _last_valid(p_msl),
        "p_abs_hpa": _last_valid(p_abs),
        "Td": float("nan"),
        "wind": _last_valid(winds),
        "gust": _last_valid(gusts),
        "feels_like": float("nan"),
        "heat_index": float("nan"),
        "wind_chill": float("nan"),
        "wind_dir_deg": _last_valid(dirs),
        "precip_total": precip_today,
        "solar_radiation": _last_valid(solars),
        "uv": float("nan"),
        "epoch": int(max(epochs)),
        "time_local": "",
        "time_utc": "",
        "lat": lat,
        "lon": lon,
        "elevation": elevation,
        "idema": station_id,
        "station_code": station_id,
        "temp_max": max(temp_valid) if temp_valid else None,
        "temp_min": min(temp_valid) if temp_valid else None,
        "rh_max": max(rh_valid) if rh_valid else None,
        "rh_min": min(rh_valid) if rh_valid else None,
        "gust_max": max(gust_valid) if gust_valid else None,
        "pressure_3h_ago": float("nan"),
        "epoch_3h_ago": float("nan"),
        "_series": series,
    }
