"""
Parsing puro de WeatherLink v2.

El backend FastAPI (``server/services/weatherlink.py``) reutiliza aquí la normalización de
payloads (current/historic, prioridades de sensor, conversiones
imperiales→métricas) sin importar ``streamlit``.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

INHG_TO_HPA = 33.8638866667
MPH_TO_KMH = 1.609344


def _safe_float(value: Any, default: float = float("nan")) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_nan(value: float) -> bool:
    return value != value


def _first_valid(*values: Any) -> float:
    for value in values:
        parsed = _safe_float(value)
        if not _is_nan(parsed):
            return parsed
    return float("nan")


def _f_to_c(value: Any) -> float:
    parsed = _safe_float(value)
    return (parsed - 32.0) * 5.0 / 9.0 if not _is_nan(parsed) else float("nan")


def _mph_to_kmh(value: Any) -> float:
    parsed = _safe_float(value)
    return parsed * MPH_TO_KMH if not _is_nan(parsed) else float("nan")


def _inch_to_mm(value: Any) -> float:
    parsed = _safe_float(value)
    return parsed * 25.4 if not _is_nan(parsed) else float("nan")


def _inhg_to_hpa(value: Any) -> float:
    parsed = _safe_float(value)
    return parsed * INHG_TO_HPA if not _is_nan(parsed) else float("nan")


def _station_id(station: Dict[str, Any]) -> str:
    return str(station.get("station_id") or station.get("station_id_uuid") or "").strip()


def _station_name(station: Dict[str, Any], fallback: str = "") -> str:
    """
    Devuelve el nombre más amistoso disponible para una estación
    WeatherLink. Distintas estaciones exponen el "alias" del owner en
    campos distintos según el firmware/registro:

    - ``station_name``: el campo principal del schema v2.
    - ``name``: alias plano legacy.
    - ``username``: muchas estaciones particulares lo usan como nombre
      público (ej. ``meteo_roses``, ``pws_madrid``…).
    - ``device_name``: alias del datalogger en algunos despliegues.
    - ``gateway_id_hex``: último recurso identificable antes del id.

    Solo caemos al ``fallback`` (típicamente el ``station_id`` numérico)
    cuando ninguno de los anteriores aporta un nombre legible.
    """
    for key in ("station_name", "name", "username", "device_name", "gateway_id_hex"):
        value = station.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    # Log temporal para diagnóstico: si caemos al fallback, dejamos en
    # logs los nombres de campos disponibles (sin valores sensibles) para
    # poder identificar qué campo expone WeatherLink el alias amistoso
    # en esa estación concreta. Eliminar este log una vez confirmado el
    # mapeo correcto en producción.
    try:
        keys_preview = sorted(k for k in station.keys() if isinstance(k, str))
        logger.info(
            "WeatherLink station sin nombre amistoso; usando id=%r. "
            "Campos disponibles: %s",
            fallback,
            keys_preview,
        )
    except Exception:
        pass
    return fallback


def normalize_weatherlink_stations(payload: Any) -> List[Dict[str, Any]]:
    stations = payload.get("stations", []) if isinstance(payload, dict) else []
    out: List[Dict[str, Any]] = []
    for item in stations if isinstance(stations, list) else []:
        if not isinstance(item, dict):
            continue
        sid = _station_id(item)
        if not sid:
            continue
        station = dict(item)
        station["station_id"] = sid
        station["station_name"] = _station_name(station, sid)
        out.append(station)
    return out


def _current_records(payload: Dict[str, Any]) -> List[Tuple[int, int, int, Dict[str, Any]]]:
    records: List[Tuple[int, int, int, Dict[str, Any]]] = []
    sensors = payload.get("sensors", []) if isinstance(payload, dict) else []
    for sensor in sensors if isinstance(sensors, list) else []:
        if not isinstance(sensor, dict):
            continue
        sensor_type = int(_safe_float(sensor.get("sensor_type"), -1))
        structure = int(_safe_float(sensor.get("data_structure_type"), -1))
        for row in sensor.get("data", []) if isinstance(sensor.get("data"), list) else []:
            if not isinstance(row, dict):
                continue
            epoch = int(_safe_float(row.get("ts"), 0) or 0)
            if epoch <= 0:
                continue
            records.append((epoch, sensor_type, structure, row))
    records.sort(key=lambda item: (item[0], _record_priority(item[1], item[2])), reverse=True)
    return records


def _record_priority(sensor_type: int, structure: int) -> int:
    if structure in (6, 10, 23):
        return 100
    if structure in (3, 4, 7, 11, 24):
        return 95
    if structure in (12, 13, 20):
        return 85
    if sensor_type == 3 or structure == 9:
        return 80
    return 10


def _first_from_records(records: List[Tuple[int, int, int, Dict[str, Any]]], keys: Iterable[str], *, convert=None) -> float:
    for _epoch, _sensor_type, _structure, row in records:
        value = _first_valid(*(row.get(key) for key in keys))
        if not _is_nan(value):
            return convert(value) if callable(convert) else value
    return float("nan")


def _values_from_records(records: List[Tuple[int, int, int, Dict[str, Any]]], keys: Iterable[str], *, convert=None) -> List[float]:
    values: List[float] = []
    for _epoch, _sensor_type, _structure, row in records:
        value = _first_valid(*(row.get(key) for key in keys))
        if not _is_nan(value):
            values.append(convert(value) if callable(convert) else value)
    return values


def _with_fallback(value: float, fallback: float) -> float:
    return fallback if _is_nan(value) else value


def _valid_values(values: Iterable[Any]) -> List[float]:
    valid: List[float] = []
    for value in values or []:
        parsed = _safe_float(value)
        if not _is_nan(parsed):
            valid.append(parsed)
    return valid


def _station_tzinfo(station: Dict[str, Any]):
    tz_name = str(station.get("time_zone") or "").strip()
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def _today_window(station: Dict[str, Any], *, now_epoch: Optional[int] = None) -> Tuple[int, int]:
    tzinfo = _station_tzinfo(station)
    if now_epoch is None:
        now_dt = datetime.now(tzinfo)
    else:
        now_dt = datetime.fromtimestamp(int(now_epoch), tzinfo)
    day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = now_dt.replace(second=0, microsecond=0)
    end_dt = end_dt - timedelta(minutes=end_dt.minute % 5)
    if end_dt <= day_start:
        end_dt = now_dt.replace(second=0, microsecond=0)
    return int(day_start.timestamp()), int(end_dt.timestamp())


def _first_precip_mm(rows: List[Tuple[int, int, int, Dict[str, Any]]]) -> float:
    """Lluvia caída EN el intervalo del registro histórico (incremento). Solo
    campos por-intervalo (``rainfall_mm`` / ``rainfall_in``): el consumidor
    (climograma diario, serie de observación) los SUMA, así que NO se puede caer
    a ``rainfall_daily_mm`` (acumulado corrido) ni a ``rain_rate_last_mm``
    (tasa) — sumar esos daría un total disparatado. Sin campo por-intervalo →
    NaN (dato ausente), que es preferible a un total erróneo."""
    value = _first_from_records(rows, ("rainfall_mm",))
    if not _is_nan(value):
        return value
    return _first_from_records(rows, ("rainfall_in",), convert=_inch_to_mm)


def _series_value_from_rows(
    rows: List[Tuple[int, int, int, Dict[str, Any]]],
    keys: Iterable[str],
    *,
    convert=None,
) -> float:
    return _first_from_records(rows, keys, convert=convert)


def normalize_weatherlink_historic_series(payload: Dict[str, Any], altitude_m: Any = None) -> Dict[str, Any]:
    records = _current_records(payload if isinstance(payload, dict) else {})
    elevation = _first_valid(altitude_m, 0.0)
    if _is_nan(elevation):
        elevation = 0.0

    grouped: Dict[int, List[Tuple[int, int, int, Dict[str, Any]]]] = {}
    for epoch, sensor_type, structure, row in records:
        grouped.setdefault(int(epoch), []).append((int(epoch), sensor_type, structure, row))

    series = {
        "epochs": [],
        "temps": [],
        "humidities": [],
        "dewpts": [],
        "pressures_abs": [],
        "pressures_msl": [],
        "winds": [],
        "gusts": [],
        "wind_dirs": [],
        "precips": [],
        "solar_radiations": [],
        "uv_indexes": [],
        "has_data": False,
    }

    for epoch in sorted(grouped):
        rows = sorted(
            grouped[epoch],
            key=lambda item: _record_priority(item[1], item[2]),
            reverse=True,
        )
        temp_c = _series_value_from_rows(rows, ("temp_last", "temp", "temp_out"), convert=_f_to_c)
        rh = _series_value_from_rows(rows, ("hum_last", "hum", "hum_out"))
        dew_c = _series_value_from_rows(rows, ("dew_point_last", "dew_point", "dew_point_out"), convert=_f_to_c)
        wind_kmh = _series_value_from_rows(
            rows,
            ("wind_speed_avg", "wind_speed_last", "wind_speed_avg_last_10_min"),
            convert=_mph_to_kmh,
        )
        gust_kmh = _series_value_from_rows(
            rows,
            ("wind_speed_hi", "wind_speed_hi_last_10_min", "wind_gust"),
            convert=_mph_to_kmh,
        )
        wind_dir = _series_value_from_rows(
            rows,
            ("wind_dir_of_prevail", "wind_dir_scalar_avg", "wind_dir_last"),
        )
        p_msl = _series_value_from_rows(rows, ("bar", "bar_sea_level", "pressure_last", "bar_alt"), convert=_inhg_to_hpa)
        p_abs = _series_value_from_rows(rows, ("bar_absolute", "abs_press"), convert=_inhg_to_hpa)
        if _is_nan(p_abs):
            p_abs = _pressure_abs_from_msl(p_msl, float(elevation))
        precip_mm = _first_precip_mm(rows)
        solar = _series_value_from_rows(rows, ("solar_rad_avg", "solar_rad", "solar_rad_hi"))
        uv = _series_value_from_rows(rows, ("uv_index_avg", "uv_index", "uv_index_hi"))

        if all(_is_nan(value) for value in (temp_c, rh, dew_c, p_abs, p_msl, wind_kmh, gust_kmh, precip_mm, solar, uv)):
            continue

        series["epochs"].append(int(epoch))
        series["temps"].append(float(temp_c))
        series["humidities"].append(float(rh))
        series["dewpts"].append(float(dew_c))
        series["pressures_abs"].append(float(p_abs))
        series["pressures_msl"].append(float(p_msl))
        series["winds"].append(float(wind_kmh))
        series["gusts"].append(float(gust_kmh))
        series["wind_dirs"].append(float(wind_dir))
        series["precips"].append(float(precip_mm))
        series["solar_radiations"].append(float(solar))
        series["uv_indexes"].append(float(uv))

    temp_highs = _values_from_records(records, ("temp_hi", "temp_high", "temp_out_hi"), convert=_f_to_c)
    temp_lows = _values_from_records(records, ("temp_low", "temp_lo", "temp_min", "temp_out_low"), convert=_f_to_c)
    rh_highs = _values_from_records(records, ("hum_hi", "hum_high", "hum_out_hi"))
    rh_lows = _values_from_records(records, ("hum_low", "hum_lo", "hum_min", "hum_out_low"))
    gust_highs = _values_from_records(records, ("wind_speed_hi", "wind_speed_hi_day", "wind_gust_hi"), convert=_mph_to_kmh)

    temp_values = _valid_values(series["temps"])
    rh_values = _valid_values(series["humidities"])
    gust_values = _valid_values(series["gusts"])
    series["daily_extremes"] = {
        "temp_max": max(temp_highs or temp_values, default=float("nan")),
        "temp_min": min(temp_lows or temp_values, default=float("nan")),
        "rh_max": max(rh_highs or rh_values, default=float("nan")),
        "rh_min": min(rh_lows or rh_values, default=float("nan")),
        "gust_max": max(gust_highs or gust_values, default=float("nan")),
    }
    series["has_data"] = bool(series["epochs"])
    return series


def _empty_weatherlink_series() -> Dict[str, Any]:
    return {
        "epochs": [],
        "temps": [],
        "humidities": [],
        "dewpts": [],
        "pressures_abs": [],
        "pressures_msl": [],
        "winds": [],
        "gusts": [],
        "wind_dirs": [],
        "precips": [],
        "solar_radiations": [],
        "uv_indexes": [],
        "has_data": False,
    }


def _series_keys() -> Tuple[str, ...]:
    return (
        "epochs", "temps", "humidities", "dewpts",
        "pressures_abs", "pressures_msl", "winds", "gusts",
        "wind_dirs", "precips", "solar_radiations", "uv_indexes",
    )


def _latest_epoch(records: List[Tuple[int, int, int, Dict[str, Any]]], payload: Dict[str, Any]) -> int:
    for epoch, _sensor_type, _structure, _row in records:
        if epoch > 0:
            return int(epoch)
    generated = int(_safe_float(payload.get("generated_at"), 0) or 0)
    return generated if generated > 0 else int(datetime.now(timezone.utc).timestamp())


def _pressure_abs_from_msl(p_msl_hpa: float, elevation_m: float) -> float:
    if _is_nan(p_msl_hpa):
        return float("nan")
    return p_msl_hpa / math.exp(float(elevation_m or 0.0) / 8000.0)


def normalize_weatherlink_current(payload: Dict[str, Any], station: Optional[Dict[str, Any]] = None, altitude_m: Any = None) -> Dict[str, Any]:
    station = dict(station or {})
    records = _current_records(payload if isinstance(payload, dict) else {})
    station_id = str((payload or {}).get("station_id") or _station_id(station)).strip()
    elevation = _first_valid(altitude_m, station.get("elevation"), 0.0)
    if _is_nan(elevation):
        elevation = 0.0

    temp_c = _first_from_records(records, ("temp", "temp_out"), convert=_f_to_c)
    rh = _first_from_records(records, ("hum", "hum_out"))
    dew_c = _first_from_records(records, ("dew_point", "dew_point_out"), convert=_f_to_c)
    wind_kmh = _first_from_records(
        records,
        (
            "wind_speed_last",
            "wind_speed_avg_last_1_min",
            "wind_speed_avg_last_2_min",
            "wind_speed_avg_last_10_min",
            "wind_speed_avg",
        ),
        convert=_mph_to_kmh,
    )
    gust_kmh = _first_from_records(
        records,
        (
            "wind_speed_hi_last_10_min",
            "wind_speed_hi_last_2_min",
            "wind_speed_hi",
            "wind_gust",
        ),
        convert=_mph_to_kmh,
    )
    wind_dir = _first_from_records(
        records,
        (
            "wind_dir_last",
            "wind_dir_scalar_avg_last_1_min",
            "wind_dir_scalar_avg_last_2_min",
            "wind_dir_scalar_avg_last_10_min",
            "wind_dir_of_prevail",
        ),
    )
    p_msl = _first_from_records(
        records,
        ("bar_sea_level", "pressure_last", "bar", "bar_alt"),
        convert=_inhg_to_hpa,
    )
    p_abs = _first_from_records(records, ("bar_absolute", "abs_press"), convert=_inhg_to_hpa)
    if _is_nan(p_abs):
        p_abs = _pressure_abs_from_msl(p_msl, float(elevation))

    precip_total = _first_from_records(
        records,
        ("rainfall_daily_mm", "rainfall_last_24_hr_mm", "rainfall_mm", "rain_storm_mm"),
    )
    rain_rate = _first_from_records(records, ("rain_rate_last_mm", "rain_rate_hi_mm", "rain_rate_hi_last_15_min_mm"))
    solar = _first_from_records(records, ("solar_rad", "solar_rad_hi", "solar_rad_avg"))
    uv = _first_from_records(records, ("uv_index", "uv_index_hi", "uv_index_avg"))
    heat_index_c = _first_from_records(records, ("heat_index", "heat_index_out", "thw_index"), convert=_f_to_c)
    wind_chill_c = _first_from_records(records, ("wind_chill", "wind_chill_last"), convert=_f_to_c)
    temp_max_c = _first_from_records(
        records,
        (
            "temp_hi",
            "temp_high",
            "temp_out_hi",
            "temp_out_high",
            "temp_day_high",
            "temp_high_today",
            "temp_hi_today",
        ),
        convert=_f_to_c,
    )
    temp_min_c = _first_from_records(
        records,
        (
            "temp_low",
            "temp_lo",
            "temp_min",
            "temp_out_low",
            "temp_out_lo",
            "temp_day_low",
            "temp_low_today",
            "temp_lo_today",
        ),
        convert=_f_to_c,
    )
    rh_max = _first_from_records(
        records,
        ("hum_hi", "hum_high", "hum_out_hi", "hum_out_high", "hum_day_high", "hum_hi_today"),
    )
    rh_min = _first_from_records(
        records,
        ("hum_low", "hum_lo", "hum_min", "hum_out_low", "hum_out_lo", "hum_day_low", "hum_lo_today"),
    )
    gust_max_kmh = _first_from_records(
        records,
        (
            "wind_speed_hi_day",
            "wind_speed_hi_today",
            "wind_speed_hi",
            "wind_gust_hi",
            "wind_gust_high",
            "wind_speed_hi_last_10_min",
            "wind_speed_hi_last_2_min",
            "wind_gust",
        ),
        convert=_mph_to_kmh,
    )

    epoch = _latest_epoch(records, payload if isinstance(payload, dict) else {})
    lat = _first_valid(station.get("latitude"), station.get("lat"))
    lon = _first_valid(station.get("longitude"), station.get("lon"))
    name = _station_name(station, station_id or "WeatherLink")

    return {
        "station_name": name,
        "lat": lat,
        "lon": lon,
        "elevation": float(elevation),
        "epoch": int(epoch),
        "Tc": temp_c,
        "RH": rh,
        "Td": dew_c,
        "p_hpa": p_msl,
        "p_abs_hpa": p_abs,
        "pressure_3h_ago": float("nan"),
        "epoch_3h_ago": None,
        "wind": wind_kmh,
        "gust": gust_kmh,
        "wind_dir_deg": wind_dir,
        "precip_total": precip_total,
        "precip_rate": rain_rate,
        "solar_radiation": solar,
        "uv": uv,
        "feels_like": float("nan"),
        "heat_index": heat_index_c,
        "wind_chill": wind_chill_c,
        "temp_max": _with_fallback(temp_max_c, temp_c),
        "temp_min": _with_fallback(temp_min_c, temp_c),
        "rh_max": _with_fallback(rh_max, rh),
        "rh_min": _with_fallback(rh_min, rh),
        "gust_max": _with_fallback(gust_max_kmh, gust_kmh),
    }


def find_weatherlink_station(stations: Iterable[Dict[str, Any]], station_id: str) -> Dict[str, Any]:
    target = str(station_id or "").strip()
    if not target:
        return {}
    for station in stations or []:
        if not isinstance(station, dict):
            continue
        if _station_id(station) == target or str(station.get("station_id_uuid", "")).strip() == target:
            return dict(station)
    return {}
