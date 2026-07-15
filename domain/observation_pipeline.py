"""
Pipeline de procesamiento de observaciones meteorológicas.

Función pura ``process_observation`` que toma un ``base`` (observación
canónica de un proveedor) y un ``ProcessingContext``, y devuelve un
``ProcessingResult`` con todas las derivadas (termodinámica, radiación,
ET0, tendencia de presión, series normalizadas) **sin ningún efecto
secundario sobre Streamlit, sesión, logging ni UI**.

Por qué importa:
- El backend FastAPI puede importar este módulo y devolver observaciones
  ya procesadas vía API sin duplicar lógica.
- Es trivialmente testeable: input → output.
- El "imperative shell" del frontend Streamlit (en ``meteolabx.py``)
  aplica los efectos secundarios sobre ``st.session_state`` y emite
  ``st.warning`` a partir de lo que esta función produce
  (``ProcessingResult.session_updates``, ``warnings``,
  ``chart_series``, ``trend_hourly_series``, ``chart_series_owner``,
  ``trend_hourly_owner_action`` + ``trend_hourly_owner``).

Esta función reemplaza la parte pura de
``meteolabx.process_standard_provider``. El adapter que la llama desde
Streamlit se queda como capa fina de efectos secundarios.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

# Constantes meteo del config legacy (módulo Python puro).
from config import (
    MAX_DATA_AGE_MINUTES,
    RD,
    RAIN_TRACE, RAIN_VERY_LIGHT, RAIN_LIGHT, RAIN_MODERATE_LIGHT,
    RAIN_MODERATE, RAIN_HEAVY, RAIN_VERY_HEAVY,
)

# Termodinámica y radiación. Sin streamlit.
from models.thermodynamics import (
    apparent_temperature,
    absolute_humidity,
    air_density,
    dewpoint_from_vapor_pressure,
    e_s,
    equivalent_temperature,
    heat_index_rothfusz,
    lcl_height,
    potential_temperature,
    specific_humidity,
    vapor_pressure,
    virtual_temperature,
    wet_bulb_celsius,
)
from models.radiation import (
    penman_monteith_et0,
    sky_clarity_index,
    water_balance,
)

from domain import observation_warnings

# NOTA: NO importamos de ``utils.series_state`` ni ``utils.state_keys``
# porque ``utils/__init__.py`` carga eagerly ``utils.storage`` →
# ``streamlit_local_storage`` → ``streamlit``, contaminando el dominio.
# Inlinamos los helpers que necesitamos. Cuando se haga cleanup del
# paquete ``utils/`` para que su ``__init__`` deje de ser eager, esto
# puede volver a delegarse.


# ---- Claves de session_state que el caller persistirá ---------------
# (Copiadas literalmente de utils.state_keys para no arrastrar streamlit.)
LAST_UPDATE_TIME = "last_update_time"
STATION_LAT = "station_lat"
STATION_LON = "station_lon"
STATION_ELEVATION = "station_elevation"
ELEVATION_SOURCE = "elevation_source"
PROVIDER_STATION_ID = "provider_station_id"
PROVIDER_STATION_NAME = "provider_station_name"
PROVIDER_STATION_LAT = "provider_station_lat"
PROVIDER_STATION_LON = "provider_station_lon"
PROVIDER_STATION_ALT = "provider_station_alt"
PROVIDER_STATION_TZ = "provider_station_tz"


# ---- Normalizador de series (inline de utils.series_state) ---------
_SERIES_NORMALIZED_KEYS = (
    "epochs", "temps", "humidities", "dewpts",
    "uv_indexes", "solar_radiations", "winds", "gusts", "wind_dirs",
)


def _empty_chart_series() -> Dict[str, Any]:
    return {
        "epochs": [],
        "temps": [],
        "humidities": [],
        "dewpts": [],
        "pressures_abs": [],
        "uv_indexes": [],
        "solar_radiations": [],
        "winds": [],
        "gusts": [],
        "wind_dirs": [],
        "precips": [],
        "theta_e": [],
        "mixing_ratios": [],
        "theta_e_trends": [],
        "mixing_ratio_trends": [],
        "pressure_trends": [],
        "vapor_pressures": [],
        "saturation_pressures": [],
        "theoretical_solar_radiations": [],
        "wind_u": [],
        "wind_v": [],
        "sunrise_epoch": None,
        "sunset_epoch": None,
        "solar_altitude": None,
        "solar_altitude_max": None,
        "is_nighttime": None,
        "has_data": False,
    }


def _last_valid_float(values: Any) -> float:
    if not isinstance(values, list):
        return float("nan")
    for value in reversed(values):
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            continue
        if not _is_nan(candidate):
            return candidate
    return float("nan")


def _apply_wind_fallback_from_series(base: Dict[str, Any], chart_series: Mapping[str, Any]) -> None:
    for base_key, series_key in (
        ("wind", "winds"),
        ("gust", "gusts"),
        ("wind_dir_deg", "wind_dirs"),
    ):
        try:
            current = float(base.get(base_key, float("nan")))
        except (TypeError, ValueError):
            current = float("nan")
        if not _is_nan(current):
            continue
        fallback = _last_valid_float(chart_series.get(series_key, []))
        if not _is_nan(fallback):
            base[base_key] = fallback


def _local_day_start_epoch(epoch: int, tz_name: str) -> int:
    try:
        tz = ZoneInfo(str(tz_name).strip()) if str(tz_name).strip() else None
        local = datetime.fromtimestamp(epoch, tz=tz)
    except Exception:
        local = datetime.fromtimestamp(epoch)
    return int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def _solar_energy_today_wh_m2(
    epochs: List[Any], solar_values: List[Any], *, now_epoch: int, tz_name: str,
) -> float:
    day_start = _local_day_start_epoch(now_epoch, tz_name)
    points: List[Tuple[int, float]] = []
    for epoch, value in zip(epochs, solar_values):
        try:
            ep, solar = int(epoch), max(0.0, float(value))
        except (TypeError, ValueError):
            continue
        if solar == solar and day_start <= ep <= now_epoch:
            points.append((ep, solar))
    points.sort()
    if len(points) < 2:
        return float("nan")
    energy = 0.0
    previous_epoch, previous_value = points[0]
    for epoch, value in points[1:]:
        elapsed = epoch - previous_epoch
        if 0 < elapsed <= 7200:
            energy += (previous_value + value) * 0.5 * elapsed / 3600.0
        previous_epoch, previous_value = epoch, value
    return max(0.0, energy)


def _erythemal_metrics(epochs: List[Any], uv_values: List[Any], *, now_epoch: int) -> Tuple[float, float]:
    points: List[Tuple[int, float]] = []
    for epoch, value in zip(epochs, uv_values):
        try:
            ep, uv_index = int(epoch), max(0.0, float(value))
        except (TypeError, ValueError):
            continue
        if uv_index == uv_index and 0 < ep <= now_epoch:
            points.append((ep, uv_index))
    points.sort()
    if not points:
        return float("nan"), float("nan")
    dose = 0.0
    for index, (epoch, uv_index) in enumerate(points):
        next_epoch = points[index + 1][0] if index + 1 < len(points) else now_epoch
        elapsed = next_epoch - epoch
        if elapsed <= 0 and index:
            elapsed = epoch - points[index - 1][0]
        elapsed = max(60, min(1800, elapsed if elapsed > 0 else 300))
        dose += 0.025 * uv_index * elapsed
    return dose / 100.0, dose


def _wet_bulb_risk(value: float) -> Tuple[str, str]:
    # Umbrales alineados con la evidencia empírica (Vecellio et al. 2022):
    # el límite crítico real en humanos ronda Tw 25-28 °C, muy por debajo
    # del teórico de 35 °C.
    if _is_nan(value) or value < 27.0:
        return "", ""
    if value >= 34.0:
        return "extreme", "danger"
    if value >= 30.0:
        return "critical", "warning"
    return "potential", ""


def _heat_index_risk(value: float) -> Tuple[str, str]:
    """Aviso por heat index (Rothfusz): complementa al de bulbo húmedo en
    calor seco/moderadamente húmedo, donde Tw se queda corto."""
    if _is_nan(value) or value < 40.0:
        return "", ""
    if value >= 50.0:
        return "extreme", "danger"
    if value >= 45.0:
        return "very_high", "warning"
    return "high", ""


def normalize_chart_series(
    payload: Optional[Mapping[str, Any]], *, pressure_key: str = "pressures_abs",
) -> Dict[str, Any]:
    """
    Convierte un dict de serie crudo al shape canónico que usa la app.
    Réplica de ``utils.series_state.normalize_chart_series`` (esa versión
    sigue existiendo para uso de ``utils.series_state.store_series_state``
    en el shell imperativo; aquí necesitamos la versión pura sin que
    Python cargue todo ``utils/``).
    """
    series = payload if isinstance(payload, Mapping) else {}
    normalized = _empty_chart_series()
    normalized["epochs"] = list(series.get("epochs", []))
    normalized["temps"] = list(series.get("temps", []))
    normalized["humidities"] = list(series.get("humidities", []))
    normalized["dewpts"] = list(series.get("dewpts", []))
    normalized["pressures_abs"] = list(series.get(pressure_key, []))
    normalized["uv_indexes"] = list(series.get("uv_indexes", []))
    normalized["solar_radiations"] = list(series.get("solar_radiations", []))
    normalized["winds"] = list(series.get("winds", []))
    normalized["gusts"] = list(series.get("gusts", []))
    normalized["wind_dirs"] = list(series.get("wind_dirs", []))
    normalized["precips"] = list(series.get("precips", []))
    for series_key in (
        "theta_e", "mixing_ratios", "theta_e_trends", "mixing_ratio_trends",
        "pressure_trends", "vapor_pressures", "saturation_pressures",
        "theoretical_solar_radiations", "wind_u", "wind_v",
    ):
        normalized[series_key] = list(series.get(series_key, []))
    for series_key in (
        "sunrise_epoch", "sunset_epoch", "solar_altitude",
        "solar_altitude_max", "is_nighttime",
    ):
        normalized[series_key] = series.get(series_key)
    normalized["has_data"] = bool(series.get("has_data", False))
    return normalized


# =====================================================================
# Helper: derivadas básicas que se calculan SIEMPRE (nunca del proveedor)
# =====================================================================

def add_basic_derived(base: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calcula y añade ``Td``, ``feels_like`` y ``heat_index`` a ``base``
    a partir de los campos primarios (``Tc``, ``RH``, ``wind``).

    Por qué es importante: los proveedores meteorológicos suelen devolver
    estos valores ya calculados, pero cada uno usa fórmulas y filtros
    distintos. Para que los números sean consistentes entre proveedores
    (y entre la app actual y futuras integraciones) los calculamos
    **siempre** nosotros con las fórmulas estándar:

    - ``Td``  → Magnus-Tetens (``dewpoint_from_vapor_pressure``).
    - ``feels_like`` → Steadman 1984 (``apparent_temperature``).
    - ``heat_index`` → Rothfusz NWS, regresión polinómica de 9
      coeficientes (``heat_index_rothfusz``).

    ``wind_chill`` se calcula en la fase pipeline cuando es relevante;
    no se incluye aquí ni se toma del proveedor.

    Muta ``base`` in-place y lo devuelve, manteniendo la convención del
    pipeline legacy. Si faltan ``Tc`` o ``RH`` los derivados quedan a
    ``NaN`` (excepto ``feels_like`` que toma ``Tc`` como fallback igual
    que hacía el legacy).
    """
    Tc = base.get("Tc")
    RH = base.get("RH")
    wind_kmh = base.get("wind", 0.0)

    if Tc is None or _is_nan(Tc) or RH is None or _is_nan(RH):
        base["Td"] = float("nan")
        base["heat_index"] = float("nan")
        # feels_like cae a Tc cuando no hay RH (criterio legacy: mejor
        # mostrar la temperatura medida que un NaN).
        base["feels_like"] = float(Tc) if Tc is not None and not _is_nan(Tc) else float("nan")
        return base

    # Td: Magnus-Tetens vía presión de vapor saturante de Tetens.
    e_v = vapor_pressure(Tc, RH)
    base["Td"] = dewpoint_from_vapor_pressure(e_v)

    # feels_like: Steadman 1984 (T + 0.33·e − 0.70·v − 4).
    if _is_nan(wind_kmh) or wind_kmh is None:
        wind_kmh_clean = 0.0
    else:
        wind_kmh_clean = float(wind_kmh)
    wind_ms = wind_kmh_clean / 3.6
    base["feels_like"] = apparent_temperature(Tc, e_v, wind_ms)

    # heat_index: Rothfusz NWS (regresión polinómica 9 coeficientes).
    base["heat_index"] = heat_index_rothfusz(Tc, RH)

    return base


# =====================================================================
# Tipos públicos
# =====================================================================

@dataclass(frozen=True)
class ProcessingContext:
    """
    Entradas a ``process_observation`` que NO viven en el dict ``base``
    (provienen de sesión Streamlit, env, defaults, etc.).

    Todos los campos tienen defaults razonables para facilitar tests y
    permitir usar el pipeline desde el backend con info parcial.
    """
    provider_name: str
    elevation_fallback: float = 0.0
    """Altitud a usar si ``base["elevation"]`` no está disponible. En el
    frontend Streamlit suele venir de ``st.session_state[<prov>_alt]``;
    en el backend, del payload de conexión."""

    provider_for_pressure: str = ""
    """ID del proveedor activo para decidir decimales al formatear
    presión. WU usa 0 decimales; resto, 1. En el frontend es
    ``st.session_state[CONNECTION_TYPE]``."""

    sun_tz_name: str = ""
    """Timezone para el cálculo de claridad del cielo (ortos/ocasos).
    Por defecto se usará la zona del navegador; aquí se pasa explícito."""

    max_data_age_minutes: float = MAX_DATA_AGE_MINUTES
    """Si la observación es más antigua que esto, se emite un warning."""

    series_override: Optional[dict] = None
    """Serie temporal canónica explícita; ``None`` representa serie vacía."""

    series_7d: Optional[dict] = None
    """Serie horaria de 7 días (para tendencia hourly). ``None`` = limpia owner."""

    owner_station_id: str = ""
    """Identificador de estación para etiquetar quién es dueño de las
    series almacenadas. Útil para detectar staleness al cambiar de
    estación. Si vacío, no se setea owner."""

    station_name: str = ""
    station_tz: str = ""


@dataclass(frozen=True)
class ProcessingResult:
    """
    Todo lo que el pipeline necesita comunicar al caller. El caller
    decide qué hacer con cada cosa:

    - ``processed`` → datos derivados (cards, displays).
    - ``base`` → dict mutado (con ``Td``, ``feels_like``, ``heat_index``
      añadidos a partir de cálculos).
    - ``chart_series`` → serie normalizada para persistir en
      ``st.session_state`` vía ``store_chart_series``.
    - ``trend_hourly_series`` → ídem para la serie horaria; ``None``
      indica que no hay nada que persistir este ciclo.
    - ``session_updates`` → diccionario de claves de sesión a aplicar
      (lat, lon, elevation, station_id…). El caller hace
      ``state.update(session_updates)`` o equivalente con NaN-skip.
    - ``warnings`` → avisos estructurados ``{"code", "params"}`` (códigos
      estables en ``domain.observation_warnings``); el frontend los
      traduce vía i18n y los emite con ``st.warning(...)``.
    - ``chart_series_owner`` → ``(provider_id, station_id)`` si se debe
      etiquetar a alguien como dueño; ``None`` si no.
    - ``trend_hourly_owner_action`` → ``"set"``, ``"clear"`` o ``"none"``.
    - ``trend_hourly_owner`` → usado solo si la acción es ``"set"``.
    """
    derivatives: Dict[str, Any]
    base: Dict[str, Any]
    chart_series: Dict[str, Any]
    trend_hourly_series: Optional[Dict[str, Any]]
    session_updates: Dict[str, Any] = field(default_factory=dict)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    chart_series_owner: Optional[Tuple[str, str]] = None
    trend_hourly_owner_action: str = "none"
    trend_hourly_owner: Optional[Tuple[str, str]] = None


@dataclass(frozen=True)
class ObservationEffectPlan:
    """Client-owned effects prepared without calculating meteorological derivatives."""

    base: Dict[str, Any]
    chart_series: Dict[str, Any]
    trend_hourly_series: Optional[Dict[str, Any]]
    session_updates: Dict[str, Any] = field(default_factory=dict)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    chart_series_owner: Optional[Tuple[str, str]] = None
    trend_hourly_owner_action: str = "none"
    trend_hourly_owner: Optional[Tuple[str, str]] = None


# =====================================================================
# Helpers puros
# =====================================================================

def _is_nan(value: Any) -> bool:
    return value != value


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return int(default)
    if _is_nan(parsed):
        return int(default)
    try:
        return int(parsed)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _pressure_decimals(provider_id: str) -> int:
    return 0 if str(provider_id).strip().upper() == "WU" else 1


def _fmt_pressure(value: Any, provider_id: str) -> str:
    """Formatea una presión a string con los decimales adecuados al proveedor."""
    try:
        v = float(value)
    except Exception:
        return "—"
    if _is_nan(v):
        return "—"
    return f"{v:.{_pressure_decimals(provider_id)}f}"


def rain_intensity_label(rate_mm_h: float) -> str:
    """
    Clasifica una intensidad de lluvia (mm/h) en una etiqueta humana.

    Las constantes vienen de ``config.py`` y no dependen del estado de UI.
    """
    if _is_nan(rate_mm_h) or rate_mm_h <= 0:
        return "Sin precipitación"
    if rate_mm_h < RAIN_TRACE:
        return "Traza de precipitación"
    if rate_mm_h < RAIN_VERY_LIGHT:
        return "Lluvia muy débil"
    if rate_mm_h < RAIN_LIGHT:
        return "Lluvia débil"
    if rate_mm_h < RAIN_MODERATE_LIGHT:
        return "Lluvia ligera"
    if rate_mm_h < RAIN_MODERATE:
        return "Lluvia moderada"
    if rate_mm_h < RAIN_HEAVY:
        return "Lluvia fuerte"
    if rate_mm_h < RAIN_VERY_HEAVY:
        return "Lluvia muy fuerte"
    return "Lluvia torrencial"


def _pressure_trend_from_endpoints(
    p_now: float, epoch_now: int, p_3h_ago: float, epoch_3h_ago: int,
) -> Tuple[float, float, str, str]:
    """
    Tendencia 3h dada presión en dos puntos explícitos del proveedor.
    Devuelve ``(dp3, rate_h, label, arrow)``.
    """
    if _is_nan(p_now) or _is_nan(p_3h_ago):
        return float("nan"), float("nan"), "—", "•"
    if _safe_int(epoch_now, 0) <= 0 or _safe_int(epoch_3h_ago, 0) <= 0:
        return float("nan"), float("nan"), "—", "•"
    dt_h = (epoch_now - epoch_3h_ago) / 3600.0
    if dt_h <= 0:
        return float("nan"), float("nan"), "—", "•"
    dp3 = p_now - p_3h_ago
    rate_h = dp3 / dt_h
    # Umbrales meteorológicos compartidos por todos los proveedores.
    abs_dp = abs(dp3)
    if abs_dp < 1.0:
        label, arrow = "Estable", "→"
    elif dp3 > 0:
        if abs_dp < 3.0:
            label, arrow = "Subida débil", "↗"
        elif abs_dp < 6.0:
            label, arrow = "Subida moderada", "↑"
        else:
            label, arrow = "Subida fuerte", "⇑"
    else:
        if abs_dp < 3.0:
            label, arrow = "Bajada débil", "↘"
        elif abs_dp < 6.0:
            label, arrow = "Bajada moderada", "↓"
        else:
            label, arrow = "Bajada fuerte", "⇓"
    return dp3, rate_h, label, arrow


def _precip_rate_between_last_measurements(
    epochs: List[Any], precip_values: List[Any],
) -> float:
    """
    Intensidad (mm/h) a partir de las dos últimas muestras de una serie.

    Réplica de ``meteolabx._precip_rate_between_last_measurements``;
    funciona tanto con series acumuladas (la mayoría) como con series
    por intervalo, autodetectando el régimen.
    """
    points: List[Tuple[int, float]] = []
    for ep, precip in zip(epochs or [], precip_values or []):
        try:
            ep_i = int(float(ep))
            precip_f = float(precip)
        except (TypeError, ValueError):
            continue
        if ep_i <= 0 or _is_nan(precip_f):
            continue
        points.append((ep_i, max(0.0, precip_f)))

    if len(points) < 2:
        return float("nan")

    points.sort(key=lambda item: item[0])
    values = [v for _, v in points]
    diffs = [values[i] - values[i - 1] for i in range(1, len(values))]
    non_negative_ratio = (
        sum(1 for diff in diffs if diff >= -0.05) / len(diffs)
        if diffs else 1.0
    )
    looks_cumulative = non_negative_ratio >= 0.8

    latest_ep, latest_precip = points[-1]
    for previous_ep, previous_precip in reversed(points[:-1]):
        dt_seconds = latest_ep - previous_ep
        if dt_seconds <= 0:
            continue
        if looks_cumulative:
            delta_mm = latest_precip - previous_precip
            if delta_mm < -0.05:
                delta_mm = latest_precip
            delta_mm = max(0.0, delta_mm)
        else:
            delta_mm = latest_precip
        return (delta_mm / dt_seconds) * 3600.0

    return float("nan")


def _precip_rate_from_series(series: Optional[Mapping[str, Any]]) -> float:
    """Detecta y delega al cálculo entre últimas dos medidas."""
    if not isinstance(series, Mapping):
        return float("nan")
    epochs = series.get("epochs", [])
    precip_values = list(series.get("precips", []))
    return _precip_rate_between_last_measurements(epochs, precip_values)


def _accumulate_et0_from_series(
    *,
    chart_epochs: List[Any],
    chart_temps: List[Any],
    chart_humidities: List[Any],
    chart_solar_radiations: List[Any],
    chart_winds: List[Any],
    fallback_wind: float,
    lat: float,
    elevation_m: float,
    precip_total: float,
) -> Tuple[float, float]:
    """
    Integra ET0 (Penman-Monteith FAO-56) sobre los puntos disponibles del
    día. Réplica de ``meteolabx._accumulate_et0_from_series``.
    """
    et0_accum = 0.0
    valid_steps = 0
    if _is_nan(fallback_wind):
        fallback_wind = 2.0

    for i, epoch_i in enumerate(chart_epochs):
        solar_i = chart_solar_radiations[i] if i < len(chart_solar_radiations) else float("nan")
        temp_i = chart_temps[i] if i < len(chart_temps) else float("nan")
        rh_i = chart_humidities[i] if i < len(chart_humidities) else float("nan")
        if _is_nan(solar_i) or solar_i < 0 or _is_nan(temp_i) or _is_nan(rh_i):
            continue

        wind_i = chart_winds[i] if i < len(chart_winds) else float("nan")
        if _is_nan(wind_i):
            wind_i = fallback_wind
        if wind_i < 0.1:
            wind_i = 0.1

        et0_i = penman_monteith_et0(
            solar_i, temp_i, rh_i, wind_i, lat, elevation_m, float(epoch_i),
        )
        if _is_nan(et0_i):
            continue

        step_hours = 5.0 / 60.0
        if i > 0:
            try:
                dt_seconds = float(epoch_i) - float(chart_epochs[i - 1])
                if 120 <= dt_seconds <= 1800:
                    step_hours = dt_seconds / 3600.0
            except Exception:
                pass

        et0_accum += et0_i / 24.0 * step_hours
        valid_steps += 1

    et0 = et0_accum if valid_steps > 0 else float("nan")
    balance = water_balance(precip_total, et0) if not _is_nan(et0) else float("nan")
    return et0, balance


def _pressure_trend_from_chart_series(
    chart_epochs: List[Any],
    chart_pressures_abs: List[Any],
    z: float,
) -> Optional[Tuple[float, float, str, str]]:
    """
    Tendencia 3h calculada desde la serie del chart cuando hay datos
    suficientes. La presión del chart viene como absoluta; aquí la
    convertimos a MSL usando ``z`` (altitud).

    Devuelve ``None`` si no hay suficientes puntos.
    """
    if len(chart_epochs) != len(chart_pressures_abs):
        return None
    press_valid: List[Tuple[int, float]] = []
    for ep, p in zip(chart_epochs, chart_pressures_abs):
        try:
            p_float = float(p)
        except (TypeError, ValueError):
            continue
        if _is_nan(p_float):
            continue
        try:
            ep_int = int(ep)
        except (TypeError, ValueError):
            continue
        press_valid.append((ep_int, p_float))

    if len(press_valid) < 2:
        return None

    press_valid.sort(key=lambda item: item[0])
    ep_now, p_abs_now = press_valid[-1]
    target_ep = ep_now - (3 * 3600)
    ep_3h, p_abs_3h = min(press_valid, key=lambda item: abs(item[0] - target_ep))
    msl_factor = math.exp(z / 8000.0)
    return _pressure_trend_from_endpoints(
        p_now=p_abs_now * msl_factor,
        epoch_now=ep_now,
        p_3h_ago=p_abs_3h * msl_factor,
        epoch_3h_ago=ep_3h,
    )


def _is_meaningful_value(value: Any) -> bool:
    """``True`` si ``value`` merece persistirse en session_state."""
    if value is None:
        return False
    if isinstance(value, float) and _is_nan(value):
        return False
    return True


def _build_provider_metadata_updates(
    base: Mapping[str, Any], ctx: ProcessingContext, z: float,
) -> Dict[str, Any]:
    """
    Construye el dict de claves de sesión que el caller debe aplicar.
    No incluye lat/lon/elevation (se añaden aparte); solo metadata
    estación + prefijada por proveedor.
    """
    prefix = str(ctx.provider_name or "").strip().lower()
    station_id = str(ctx.owner_station_id or "").strip()
    station_name = str(ctx.station_name or "").strip()
    station_tz = str(ctx.station_tz or "").strip()

    candidates: Dict[str, Any] = {
        PROVIDER_STATION_LAT: base.get("lat"),
        PROVIDER_STATION_LON: base.get("lon"),
        PROVIDER_STATION_ALT: z,
        f"{prefix}_station_lat": base.get("lat"),
        f"{prefix}_station_lon": base.get("lon"),
        f"{prefix}_station_alt": z,
    }
    if station_id:
        candidates[PROVIDER_STATION_ID] = station_id
        candidates[f"{prefix}_station_id"] = station_id
    if station_name:
        candidates[PROVIDER_STATION_NAME] = station_name
        candidates[f"{prefix}_station_name"] = station_name
    if station_tz:
        candidates[PROVIDER_STATION_TZ] = station_tz
        candidates[f"{prefix}_station_tz"] = station_tz

    # Filtrar None y NaN.
    return {k: v for k, v in candidates.items() if _is_meaningful_value(v)}


# =====================================================================
# Orquestador principal
# =====================================================================

def prepare_observation_effects(
    base: Dict[str, Any],
    ctx: ProcessingContext,
    *,
    now_epoch: Optional[float] = None,
) -> ObservationEffectPlan:
    """Prepare session/UI effects without running the derivative pipeline."""
    NaN = float("nan")
    current_epoch = time.time() if now_epoch is None else float(now_epoch)

    z_from_base = base.get("elevation")
    if z_from_base is None or (
        isinstance(z_from_base, float) and _is_nan(z_from_base)
    ):
        z = float(ctx.elevation_fallback or 0)
    else:
        z = float(z_from_base)

    session_updates: Dict[str, Any] = {
        LAST_UPDATE_TIME: current_epoch,
        STATION_LAT: base.get("lat", NaN),
        STATION_LON: base.get("lon", NaN),
        STATION_ELEVATION: z,
        ELEVATION_SOURCE: ctx.provider_name,
    }
    session_updates.update(_build_provider_metadata_updates(base, ctx, z))

    warnings: List[Dict[str, Any]] = []
    try:
        data_age_minutes = (current_epoch - float(base.get("epoch", 0))) / 60.0
    except (TypeError, ValueError):
        data_age_minutes = 0.0
    if data_age_minutes > ctx.max_data_age_minutes:
        warnings.append(
            observation_warnings.data_age(ctx.provider_name, data_age_minutes)
        )

    raw_series = ctx.series_override or {}
    chart_series = normalize_chart_series(
        raw_series if isinstance(raw_series, Mapping) else {}
    )
    _apply_wind_fallback_from_series(base, chart_series)

    chart_series_owner: Optional[Tuple[str, str]] = None
    if ctx.owner_station_id and (
        bool(chart_series.get("has_data")) or len(chart_series.get("epochs", [])) > 0
    ):
        chart_series_owner = (ctx.provider_name, ctx.owner_station_id)

    if ctx.series_7d is not None:
        if isinstance(ctx.series_7d, Mapping) and ctx.series_7d.get("has_data"):
            trend_hourly_series = normalize_chart_series(ctx.series_7d)
        else:
            trend_hourly_series = chart_series
    else:
        trend_hourly_series = normalize_chart_series({})

    if (
        ctx.owner_station_id
        and (
            bool(trend_hourly_series.get("has_data", False))
            or len(trend_hourly_series.get("epochs", [])) > 0
        )
    ):
        trend_hourly_owner_action = "set"
        trend_hourly_owner: Optional[Tuple[str, str]] = (
            ctx.provider_name,
            ctx.owner_station_id,
        )
    else:
        trend_hourly_owner_action = "clear"
        trend_hourly_owner = None

    return ObservationEffectPlan(
        base=base,
        chart_series=chart_series,
        trend_hourly_series=trend_hourly_series,
        session_updates=session_updates,
        warnings=warnings,
        chart_series_owner=chart_series_owner,
        trend_hourly_owner_action=trend_hourly_owner_action,
        trend_hourly_owner=trend_hourly_owner,
    )

def process_observation(base: Dict[str, Any], ctx: ProcessingContext) -> ProcessingResult:
    """
    Procesa una observación canónica y devuelve todas las derivadas.

    **Pura.** No hace I/O, no toca Streamlit, no escribe logs. Mutará
    ``base`` añadiendo ``Td``, ``feels_like`` y ``heat_index`` calculados
    (manteniendo la convención del código legacy para que el resto del
    frontend no rompa).

    Parámetros
    ----------
    base : dict
        Observación canónica con claves como ``Tc``, ``RH``, ``p_hpa``,
        ``epoch``, ``lat``, ``lon``, ``elevation``, ``wind``,
        ``solar_radiation``, ``uv``, ``precip_total``, opcionalmente
        ``_series``, ``pressure_3h_ago``, ``epoch_3h_ago``…
    ctx : ProcessingContext
        Contexto que normalmente vive en ``st.session_state``.

    Devuelve
    --------
    ProcessingResult
        Un wrapper con la observación procesada + lo que el caller debe
        aplicar como efectos secundarios.
    """
    NaN = float("nan")
    now_epoch = time.time()

    # ---- 1. Session-state updates (provider metadata) ----------------
    z_from_base = base.get("elevation")
    if z_from_base is None or (isinstance(z_from_base, float) and _is_nan(z_from_base)):
        z = float(ctx.elevation_fallback or 0)
    else:
        z = float(z_from_base)

    session_updates: Dict[str, Any] = {
        LAST_UPDATE_TIME: now_epoch,
        STATION_LAT: base.get("lat", NaN),
        STATION_LON: base.get("lon", NaN),
        STATION_ELEVATION: z,
        ELEVATION_SOURCE: ctx.provider_name,
    }
    session_updates.update(_build_provider_metadata_updates(base, ctx, z))

    # ---- 2. Warning si datos antiguos --------------------------------
    # Warnings estructurados {code, params}: el cálculo (backend) emite el
    # código estable; la presentación (idioma/emoji) vive en el frontend.
    warnings: List[Dict[str, Any]] = []
    try:
        data_age_minutes = (now_epoch - float(base.get("epoch", 0))) / 60.0
    except (TypeError, ValueError):
        data_age_minutes = 0.0
    if data_age_minutes > ctx.max_data_age_minutes:
        warnings.append(
            observation_warnings.data_age(ctx.provider_name, data_age_minutes)
        )

    # ---- 3. Inicialización de variables ------------------------------
    inst_mm_h = r5_mm_h = r10_mm_h = NaN

    # ---- 4. Presión: valores y formato ------------------------------
    p_abs = float(base.get("p_abs_hpa", NaN)) if base.get("p_abs_hpa") is not None else NaN
    p_msl = float(base.get("p_hpa", NaN)) if base.get("p_hpa") is not None else NaN
    provider_for_pressure = ctx.provider_for_pressure or ctx.provider_name
    p_abs_disp = _fmt_pressure(p_abs, provider_for_pressure)
    p_msl_disp = _fmt_pressure(p_msl, provider_for_pressure)

    # ---- 5. Tendencia presión 3h desde base["pressure_3h_ago"] ------
    if not _is_nan(p_msl):
        dp3, rate_h, p_label, p_arrow = _pressure_trend_from_endpoints(
            p_now=p_msl,
            epoch_now=_safe_int(base.get("epoch", 0), 0),
            p_3h_ago=float(base.get("pressure_3h_ago", NaN)) if base.get("pressure_3h_ago") is not None else NaN,
            epoch_3h_ago=_safe_int(base.get("epoch_3h_ago", 0), 0),
        )
    else:
        dp3, rate_h, p_label, p_arrow = NaN, NaN, "—", "•"

    # ---- 6. Serie temporal del chart + fallback de viento -------------
    raw_series = ctx.series_override or {}
    chart_series = normalize_chart_series(raw_series if isinstance(raw_series, Mapping) else {})
    chart_epochs = chart_series["epochs"]
    chart_temps = chart_series["temps"]
    chart_humidities = chart_series["humidities"]
    chart_pressures = chart_series["pressures_abs"]
    chart_winds = chart_series["winds"]
    chart_solar_radiations = chart_series["solar_radiations"]
    has_chart_data = bool(chart_series.get("has_data"))

    _apply_wind_fallback_from_series(base, chart_series)

    # ---- 7. Termodinámica básica + extendida -------------------------
    # ``add_basic_derived`` calcula y escribe ``Td``, ``feels_like`` y
    # ``heat_index`` en ``base`` con las fórmulas estándar (Magnus-Tetens,
    # Steadman 1984, Rothfusz). Se reutiliza también desde
    # ``server/services/wu.py`` para garantizar consistencia entre el
    # endpoint ``/current`` y el ``/processed``.
    add_basic_derived(base)

    # Magnitudes extendidas que solo aplican aquí (necesitan ``p_abs`` u
    # otras combinaciones que el helper básico no incluye).
    e_sat = e_v = Td_calc = Tw = q_val = q_gkg = theta = Tv_val = Te_val = NaN
    rho_val = rho_v_gm3 = lcl_val = sound_speed_ms = NaN
    wet_bulb_risk = wet_bulb_alert_level = ""

    Tc = base.get("Tc")
    RH = base.get("RH")
    if Tc is not None and not _is_nan(Tc) and RH is not None and not _is_nan(RH):
        e_sat = e_s(Tc)
        e_v = vapor_pressure(Tc, RH)
        Td_calc = base["Td"]  # ya calculado en add_basic_derived
        Tw = wet_bulb_celsius(Tc, RH, p_abs)

        if not _is_nan(p_abs):
            q_val = specific_humidity(e_v, p_abs)
            q_gkg = q_val * 1000
            theta = potential_temperature(Tc, p_abs)
            Tv_val = virtual_temperature(Tc, q_val)
            Te_val = equivalent_temperature(Tc, q_val)
            rho_val = air_density(p_abs, Tv_val)
            rho_v_gm3 = absolute_humidity(e_v, Tc)
            lcl_val = lcl_height(Tc, Td_calc)
            if not _is_nan(Tv_val):
                sound_speed_ms = math.sqrt(1.4 * RD * (Tv_val + 273.15))

        wet_bulb_risk, wet_bulb_alert_level = _wet_bulb_risk(Tw)

    heat_index_risk, heat_index_alert_level = _heat_index_risk(
        base.get("heat_index", NaN) if base.get("heat_index") is not None else NaN
    )

    # ---- 8. Radiación / UV / claridad --------------------------------
    solar_rad = base.get("solar_radiation", NaN) if base.get("solar_radiation") is not None else NaN
    uv = base.get("uv", NaN) if base.get("uv") is not None else NaN
    has_radiation = (not _is_nan(solar_rad)) or (not _is_nan(uv))
    et0 = clarity = balance = NaN
    solar_energy_today_wh_m2 = erythemal_dose_today_sed = NaN
    erythemal_dose_today_j_m2 = erythemal_irradiance_mw_m2 = NaN

    if has_radiation:
        lat = base.get("lat", NaN)
        lon = base.get("lon", NaN)
        clarity = sky_clarity_index(
            solar_rad, lat, z, base.get("epoch", 0), lon, tz_name=ctx.sun_tz_name,
        )

    observation_epoch = _safe_int(base.get("epoch", 0), int(now_epoch))
    if observation_epoch <= 0:
        observation_epoch = int(now_epoch)
    solar_energy_today_wh_m2 = _solar_energy_today_wh_m2(
        chart_epochs, chart_solar_radiations,
        now_epoch=observation_epoch, tz_name=ctx.sun_tz_name,
    )
    erythemal_dose_today_sed, erythemal_dose_today_j_m2 = _erythemal_metrics(
        chart_epochs, chart_series["uv_indexes"], now_epoch=observation_epoch,
    )
    if not _is_nan(uv):
        erythemal_irradiance_mw_m2 = 25.0 * float(uv)

    # Intensidad instantánea desde la serie (no-WU típicamente).
    inst_mm_h = _precip_rate_from_series(raw_series if isinstance(raw_series, Mapping) else {})
    inst_label = rain_intensity_label(inst_mm_h)

    # Owner del chart si hay datos.
    chart_series_owner: Optional[Tuple[str, str]] = None
    if ctx.owner_station_id and (has_chart_data or len(chart_epochs) > 0):
        chart_series_owner = (ctx.provider_name, ctx.owner_station_id)

    # ---- 8.5. ET0 acumulada desde serie ------------------------------
    if has_radiation and chart_solar_radiations:
        et0, balance = _accumulate_et0_from_series(
            chart_epochs=chart_epochs,
            chart_temps=chart_temps,
            chart_humidities=chart_humidities,
            chart_solar_radiations=chart_solar_radiations,
            chart_winds=chart_winds,
            fallback_wind=float(base.get("wind", 2.0)) if base.get("wind") is not None else 2.0,
            lat=base.get("lat", NaN),
            elevation_m=z,
            precip_total=base.get("precip_total", NaN),
        )

    # ---- 9. Tendencia presión 3h desde serie (refina la de base) ----
    trend_from_series = _pressure_trend_from_chart_series(chart_epochs, chart_pressures, z)
    if trend_from_series is not None:
        dp3, rate_h, p_label, p_arrow = trend_from_series

    # ---- 10. Serie horaria 7d (trend hourly) -------------------------
    trend_hourly_series: Optional[Dict[str, Any]] = None
    trend_hourly_owner_action = "none"
    trend_hourly_owner: Optional[Tuple[str, str]] = None

    if ctx.series_7d is not None:
        if isinstance(ctx.series_7d, Mapping) and ctx.series_7d.get("has_data"):
            trend_hourly_series = normalize_chart_series(ctx.series_7d)
        else:
            # Fallback: usar la misma serie del chart como trend hourly.
            trend_hourly_series = chart_series
    else:
        # Sin series_7d explícito: el caller debe LIMPIAR el trend hourly previo
        # (en el legacy se hace ``store_trend_hourly_series(state, None)``).
        trend_hourly_series = normalize_chart_series({})

    if (
        ctx.owner_station_id
        and trend_hourly_series is not None
        and (
            bool(trend_hourly_series.get("has_data", False))
            or len(trend_hourly_series.get("epochs", [])) > 0
        )
    ):
        trend_hourly_owner_action = "set"
        trend_hourly_owner = (ctx.provider_name, ctx.owner_station_id)
    else:
        trend_hourly_owner_action = "clear"

    # ---- Resultado final --------------------------------------------
    derivatives = {
        "z": z,
        "p_abs": p_abs, "p_msl": p_msl,
        "p_abs_disp": p_abs_disp, "p_msl_disp": p_msl_disp,
        "dp3": dp3, "rate_h": rate_h, "p_label": p_label, "p_arrow": p_arrow,
        "inst_mm_h": inst_mm_h, "r5_mm_h": r5_mm_h,
        "r10_mm_h": r10_mm_h, "inst_label": inst_label,
        "e_sat": e_sat, "e": e_v, "Td_calc": Td_calc, "Tw": Tw,
        "q": q_val, "q_gkg": q_gkg, "theta": theta, "Tv": Tv_val,
        "Te": Te_val, "rho": rho_val, "rho_v_gm3": rho_v_gm3, "lcl": lcl_val,
        "sound_speed_ms": sound_speed_ms,
        "wet_bulb_risk": wet_bulb_risk, "wet_bulb_alert_level": wet_bulb_alert_level,
        "heat_index_risk": heat_index_risk, "heat_index_alert_level": heat_index_alert_level,
        "solar_rad": solar_rad, "uv": uv, "et0": et0,
        "clarity": clarity, "balance": balance,
        "solar_energy_today_wh_m2": solar_energy_today_wh_m2,
        "erythemal_irradiance_mw_m2": erythemal_irradiance_mw_m2,
        "erythemal_dose_today_j_m2": erythemal_dose_today_j_m2,
        "erythemal_dose_today_sed": erythemal_dose_today_sed,
        "has_radiation": has_radiation, "has_chart_data": has_chart_data,
    }

    return ProcessingResult(
        derivatives=derivatives,
        base=base,
        chart_series=chart_series,
        trend_hourly_series=trend_hourly_series,
        session_updates=session_updates,
        warnings=warnings,
        chart_series_owner=chart_series_owner,
        trend_hourly_owner_action=trend_hourly_owner_action,
        trend_hourly_owner=trend_hourly_owner,
    )
