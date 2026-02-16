#!/usr/bin/env python3
"""
Precalcula el mapa estación->medida->sensor para Euskalmet.

Salida por defecto:
  data_station_sensor_map_euskalmet.json

Uso:
  python3 build_euskalmet_sensor_map.py
  python3 build_euskalmet_sensor_map.py --max-stations 20
  python3 build_euskalmet_sensor_map.py --base-url https://api.sandbox.euskadi.eus
"""

import argparse
import base64
import json
import os
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests


LOCAL_TZ = ZoneInfo("Europe/Madrid")

MEASURE_SPECS: Dict[str, Tuple[str, str]] = {
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
    ("measuresForWater", "precipitation"): ["PL", "PP", "PR", "RR"],
    ("measuresForSun", "irradiance"): ["SP", "RS", "SR"],
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _load_stations(stations_path: str) -> List[Dict[str, Any]]:
    with open(stations_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("stationId", "")).strip().upper()
        if sid:
            out.append(item)
    return out


def _load_existing_map(path: str) -> Dict[str, Dict[str, str]]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return {}
        out: Dict[str, Dict[str, str]] = {}
        for sid, mapping in payload.items():
            if not isinstance(mapping, dict):
                continue
            cleaned = {}
            for mk, sensor in mapping.items():
                mks = str(mk).strip()
                sv = str(sensor).strip().upper()
                if mks and sv:
                    cleaned[mks] = sv
            if cleaned:
                out[str(sid).strip().upper()] = cleaned
        return out
    except Exception:
        return {}


def _save_map(path: str, sensor_map: Dict[str, Dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sensor_map, f, ensure_ascii=False, indent=2, sort_keys=True)


def _build_auto_jwt(args: argparse.Namespace) -> str:
    env_jwt = str(os.getenv("EUSKALMET_JWT", "")).strip()
    if env_jwt:
        return env_jwt

    private_key_path = str(args.private_key or os.getenv("EUSKALMET_PRIVATE_KEY_PATH", "")).strip()
    if not private_key_path:
        private_key_path = "/Users/joantisdale/Downloads/Apikey/privateKey.pem"
    if not os.path.exists(private_key_path):
        return ""

    aud = str(args.aud or os.getenv("EUSKALMET_JWT_AUD", "met01.apikey")).strip()
    iss = str(args.iss or os.getenv("EUSKALMET_JWT_ISS", "")).strip()
    email = str(args.email or os.getenv("EUSKALMET_JWT_EMAIL", "meteolabx@gmail.com")).strip()
    version = str(args.version or os.getenv("EUSKALMET_JWT_VERSION", "1.0.0")).strip()
    login_id = str(args.login_id or os.getenv("EUSKALMET_JWT_LOGIN_ID", "")).strip()

    if not iss:
        iss = email.split("@", 1)[0] if "@" in email else "meteolabx"

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
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

    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    ).encode("ascii")

    proc = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", private_key_path],
        input=signing_input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return ""
    return signing_input.decode("ascii") + "." + _b64url(proc.stdout)


def _request_json(
    base_url: str,
    path: str,
    jwt_token: str,
    api_key: str,
    api_key_public: str,
    timeout_s: float,
) -> Any:
    headers = {"Accept": "application/json"}
    if jwt_token:
        headers["Authorization"] = f"Bearer {jwt_token}"
    if api_key:
        headers["apikey"] = api_key
        headers["x-api-key"] = api_key
        headers["X-API-Key"] = api_key
    if api_key_public:
        headers["x-api-key-id"] = api_key_public
        headers["X-API-Key-Id"] = api_key_public
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    r = requests.get(url, headers=headers, timeout=timeout_s)
    if r.status_code >= 400:
        txt = (r.text or "").strip().replace("\n", " ")
        if len(txt) > 180:
            txt = txt[:180] + "..."
        raise requests.HTTPError(f"{r.status_code} {r.reason} | {path} | {txt}")
    return r.json()


def _request_json_with_auto_refresh(
    args: argparse.Namespace,
    path: str,
    jwt_token: str,
    api_key: str,
    api_key_public: str,
    timeout_s: float,
) -> Tuple[Any, str]:
    try:
        payload = _request_json(
            base_url=args.base_url,
            path=path,
            jwt_token=jwt_token,
            api_key=api_key,
            api_key_public=api_key_public,
            timeout_s=timeout_s,
        )
        return payload, jwt_token
    except Exception as exc:
        msg = str(exc)
        if "NOT_VERIFIED_JWT" in msg and "EXPIRED" in msg:
            fresh = _build_auto_jwt(args)
            if fresh:
                payload = _request_json(
                    base_url=args.base_url,
                    path=path,
                    jwt_token=fresh,
                    api_key=api_key,
                    api_key_public=api_key_public,
                    timeout_s=timeout_s,
                )
                return payload, fresh
        raise


def _extract_sensor_ids_from_current(payload: Any) -> List[str]:
    sensors: List[str] = []
    if isinstance(payload, dict):
        raw = payload.get("sensors")
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                sk = str(item.get("sensorKey", "")).strip()
                if sk:
                    sid = sk.split("/")[-1].strip().upper()
                    if sid:
                        sensors.append(sid)
    out: List[str] = []
    seen = set()
    for sid in sensors:
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _order_sensors_for_measure(sensor_ids: List[str], measure_type: str, measure_id: str) -> List[str]:
    prefixes = MEASURE_SENSOR_PREFIXES.get((measure_type, measure_id), [])
    if not prefixes:
        return sensor_ids
    pri: List[str] = []
    sec: List[str] = []
    for sid in sensor_ids:
        if any(sid.startswith(p) for p in prefixes):
            pri.append(sid)
        else:
            sec.append(sid)
    return pri + sec


def _probe_hours(now_local: datetime) -> List[int]:
    vals = [now_local.hour, (now_local.hour - 1) % 24, (now_local.hour - 2) % 24]
    out: List[int] = []
    for h in vals:
        if h not in out:
            out.append(h)
    return out


def _has_values(payload: Any) -> bool:
    data = payload
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            data = data[0]
        else:
            return bool(data)
    if not isinstance(data, dict):
        return False
    values = data.get("values")
    if isinstance(values, list) and len(values) > 0:
        return True
    for alt in ("lectures", "datos", "data"):
        v = data.get(alt)
        if isinstance(v, list) and len(v) > 0:
            return True
    return False


def _resolve_sensor_for_measure(
    args: argparse.Namespace,
    base_url: str,
    station_id: str,
    sensor_ids: List[str],
    measure_type: str,
    measure_id: str,
    jwt_token: str,
    api_key: str,
    api_key_public: str,
    timeout_s: float,
    per_measure_sensor_cap: int,
) -> Tuple[str, str]:
    now_local = datetime.now(LOCAL_TZ)
    year, month, day = now_local.year, now_local.month, now_local.day
    ordered = _order_sensors_for_measure(sensor_ids, measure_type, measure_id)
    probe_ids = ordered[:max(3, per_measure_sensor_cap)]
    hours = _probe_hours(now_local)

    for sid in probe_ids:
        for h in hours:
            path = (
                f"euskalmet/readings/forStation/{station_id}/{sid}/measures/"
                f"{measure_type}/{measure_id}/at/{year:04d}/{month:02d}/{day:02d}/{h:02d}"
            )
            try:
                payload, jwt_token = _request_json_with_auto_refresh(
                    args=args,
                    path=path,
                    jwt_token=jwt_token,
                    api_key=api_key,
                    api_key_public=api_key_public,
                    timeout_s=timeout_s,
                )
            except Exception:
                continue
            if _has_values(payload):
                return sid, jwt_token
    return "", jwt_token


def build_map(args: argparse.Namespace) -> Dict[str, Dict[str, str]]:
    stations = _load_stations(args.stations_path)
    if args.max_stations > 0:
        stations = stations[: args.max_stations]

    jwt_token = _build_auto_jwt(args)
    if not jwt_token:
        raise RuntimeError("No se pudo generar JWT (ni se encontró EUSKALMET_JWT).")

    api_key = str(args.api_key or os.getenv("EUSKALMET_API_KEY", "")).strip()
    api_key_public = str(args.api_key_public or os.getenv("EUSKALMET_API_KEY_PUBLIC", "")).strip()

    sensor_map = _load_existing_map(args.output) if args.resume else {}
    total = len(stations)
    ok_count = 0

    for idx, st in enumerate(stations, start=1):
        station_id = str(st.get("stationId", "")).strip().upper()
        if not station_id:
            continue

        existing = sensor_map.get(station_id, {})
        if args.resume and len(existing) >= len(MEASURE_SPECS):
            ok_count += 1
            print(f"[{idx}/{total}] {station_id}: ya mapeada ({len(existing)} medidas)")
            continue

        print(f"[{idx}/{total}] {station_id}: resolviendo sensores...")
        current_path = f"euskalmet/stations/{station_id}/current"
        sensor_ids: List[str] = []
        try:
            current_payload, jwt_token = _request_json_with_auto_refresh(
                args=args,
                path=current_path,
                jwt_token=jwt_token,
                api_key=api_key,
                api_key_public=api_key_public,
                timeout_s=args.timeout,
            )
            sensor_ids = _extract_sensor_ids_from_current(current_payload)
        except Exception as exc:
            print(f"  ⚠️ current falló: {exc}")

        if not sensor_ids:
            print("  ⚠️ sin sensores en /current, se salta estación")
            continue

        station_map = dict(existing)
        for _, (measure_type, measure_id) in MEASURE_SPECS.items():
            mkey = f"{measure_type}/{measure_id}"
            if mkey in station_map and station_map[mkey]:
                continue
            resolved = _resolve_sensor_for_measure(
                args=args,
                base_url=args.base_url,
                station_id=station_id,
                sensor_ids=sensor_ids,
                measure_type=measure_type,
                measure_id=measure_id,
                jwt_token=jwt_token,
                api_key=api_key,
                api_key_public=api_key_public,
                timeout_s=args.timeout,
                per_measure_sensor_cap=args.per_measure_sensor_cap,
            )
            sensor_id, jwt_token = resolved
            if sensor_id:
                station_map[mkey] = sensor_id
            if args.sleep_ms > 0:
                time.sleep(args.sleep_ms / 1000.0)

        if station_map:
            sensor_map[station_id] = station_map
            mapped_n = len(station_map)
            print(f"  ✅ {mapped_n}/{len(MEASURE_SPECS)} medidas mapeadas")
            if mapped_n >= 3:
                ok_count += 1
        else:
            print("  ⚠️ sin medidas mapeadas")

        if idx % args.save_every == 0:
            _save_map(args.output, sensor_map)
            print(f"  💾 checkpoint guardado en {args.output}")

    _save_map(args.output, sensor_map)
    print("\n=== RESUMEN ===")
    print(f"Estaciones procesadas: {total}")
    print(f"Estaciones con mapa útil: {ok_count}")
    print(f"Salida: {args.output}")
    return sensor_map


def main() -> None:
    parser = argparse.ArgumentParser(description="Construye mapa station->measure->sensor de Euskalmet")
    parser.add_argument("--base-url", default=os.getenv("EUSKALMET_BASE_URL", "https://api.euskadi.eus"))
    parser.add_argument("--stations-path", default="data_estaciones_euskalmet.json")
    parser.add_argument("--output", default="data_station_sensor_map_euskalmet.json")
    parser.add_argument("--resume", action="store_true", default=True)
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.add_argument("--max-stations", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--sleep-ms", type=int, default=0)
    parser.add_argument("--save-every", type=int, default=10)
    parser.add_argument("--per-measure-sensor-cap", type=int, default=8)
    parser.add_argument("--api-key", default=os.getenv("EUSKALMET_API_KEY", ""))
    parser.add_argument("--api-key-public", default=os.getenv("EUSKALMET_API_KEY_PUBLIC", ""))
    parser.add_argument("--private-key", default=os.getenv("EUSKALMET_PRIVATE_KEY_PATH", ""))
    parser.add_argument("--aud", default=os.getenv("EUSKALMET_JWT_AUD", "met01.apikey"))
    parser.add_argument("--iss", default=os.getenv("EUSKALMET_JWT_ISS", ""))
    parser.add_argument("--email", default=os.getenv("EUSKALMET_JWT_EMAIL", "meteolabx@gmail.com"))
    parser.add_argument("--version", default=os.getenv("EUSKALMET_JWT_VERSION", "1.0.0"))
    parser.add_argument("--login-id", default=os.getenv("EUSKALMET_JWT_LOGIN_ID", ""))
    args = parser.parse_args()

    print("=== BUILD EUSKALMET SENSOR MAP ===")
    print(f"Hora local: {datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Base URL: {args.base_url}")
    print(f"Output: {args.output}")
    build_map(args)


if __name__ == "__main__":
    main()
