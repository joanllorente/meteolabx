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
  (``ProcessingResult.session_updates``, ``warnings``, ``pressure_push``,
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
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Constantes meteo del config legacy (módulo Python puro).
from config import (
    MAX_DATA_AGE_MINUTES,
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
        "has_data": False,
    }


def _has_real_number(values: List[Any]) -> bool:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number:
            return True
    return False


def _has_positive_number(values: List[Any]) -> bool:
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == number and number > 0.0:
            return True
    return False


def _select_precip_series(series: Mapping[str, Any]) -> List[Any]:
    fallback: List[Any] = []
    first_real: List[Any] = []
    for key in ("precips", "precip_accum_mm", "precip_step_mm"):
        values = series.get(key)
        if values is None:
            continue
        values_list = list(values)
        if not fallback and values_list:
            fallback = values_list
        if not first_real and _has_real_number(values_list):
            first_real = values_list
        if _has_positive_number(values_list):
            return values_list
    return first_real or fallback


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
    normalized["precips"] = _select_precip_series(series)
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
class ProcessedObservation:
    """
    Resultado de procesar una observación. Estructura idéntica a la antigua
    ``meteolabx.ProcessedData`` para que el resto del frontend siga
    consumiéndola sin cambios (``meteolabx.py`` re-exporta como alias).
    """
    z: float
    p_abs: float
    p_msl: float
    p_abs_disp: str
    p_msl_disp: str
    dp3: float
    rate_h: float
    p_label: str
    p_arrow: str
    inst_mm_h: float
    r5_mm_h: float
    r10_mm_h: float
    inst_label: str
    e_sat: float
    e: float
    Td_calc: float
    Tw: float
    q: float
    q_gkg: float
    theta: float
    Tv: float
    Te: float
    rho: float
    rho_v_gm3: float
    lcl: float
    solar_rad: float
    uv: float
    et0: float
    clarity: float
    balance: float
    has_radiation: bool
    has_chart_data: bool


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
    """Serie temporal explícita; si es ``None`` se usa ``base["_series"]``."""

    series_7d: Optional[dict] = None
    """Serie horaria de 7 días (para tendencia hourly). ``None`` = limpia owner."""

    owner_station_id: str = ""
    """Identificador de estación para etiquetar quién es dueño de las
    series almacenadas. Útil para detectar staleness al cambiar de
    estación. Si vacío, no se setea owner."""


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
    - ``warnings`` → mensajes a emitir vía ``st.warning(...)``.
    - ``pressure_push`` → ``(p_abs, epoch)`` a meter en el historial de
      presión, o ``None`` si no hay dato válido.
    - ``chart_series_owner`` → ``(provider_id, station_id)`` si se debe
      etiquetar a alguien como dueño; ``None`` si no.
    - ``trend_hourly_owner_action`` → ``"set"``, ``"clear"`` o ``"none"``.
    - ``trend_hourly_owner`` → usado solo si la acción es ``"set"``.
    """
    processed: ProcessedObservation
    base: Dict[str, Any]
    chart_series: Dict[str, Any]
    trend_hourly_series: Optional[Dict[str, Any]]
    session_updates: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    pressure_push: Optional[Tuple[float, int]] = None
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

    Duplica la lógica de ``services.rain.rain_intensity_label`` (que vive
    en un módulo que importa streamlit). Las constantes vienen de
    ``config.py``, que es Python puro.
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
    Tendencia 3h dada presión en dos puntos. Réplica de
    ``services.pressure_trend_3h`` pero local y pura para no arrastrar el
    módulo legacy aquí. Devuelve ``(dp3, rate_h, label, arrow)``.
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
    # Clasificación tomada del legacy services.pressure (criterios estándar).
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
    precip_values = _select_precip_series(series)
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
    base: Mapping[str, Any], provider_name: str, z: float,
) -> Dict[str, Any]:
    """
    Construye el dict de claves de sesión que el caller debe aplicar.
    No incluye lat/lon/elevation (se añaden aparte); solo metadata
    estación + prefijada por proveedor.
    """
    prefix = str(provider_name or "").strip().lower()
    station_id = str(base.get("station_code") or base.get("idema") or "").strip()
    station_name = str(base.get("station_name") or "").strip()
    station_tz = str(base.get("station_tz") or "").strip()

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
    session_updates.update(_build_provider_metadata_updates(base, ctx.provider_name, z))

    # ---- 2. Warning si datos antiguos --------------------------------
    warnings: List[str] = []
    try:
        data_age_minutes = (now_epoch - float(base.get("epoch", 0))) / 60.0
    except (TypeError, ValueError):
        data_age_minutes = 0.0
    if data_age_minutes > ctx.max_data_age_minutes:
        warnings.append(
            f"⚠️ Datos de {ctx.provider_name} con {data_age_minutes:.0f} minutos "
            "de antigüedad. La estación puede no estar reportando."
        )

    # ---- 3. Inicialización de variables ------------------------------
    inst_mm_h = r5_mm_h = r10_mm_h = NaN

    # ---- 4. Presión: valores y formato ------------------------------
    p_abs = float(base.get("p_abs_hpa", NaN)) if base.get("p_abs_hpa") is not None else NaN
    p_msl = float(base.get("p_hpa", NaN)) if base.get("p_hpa") is not None else NaN
    provider_for_pressure = ctx.provider_for_pressure or ctx.provider_name
    p_abs_disp = _fmt_pressure(p_abs, provider_for_pressure)
    p_msl_disp = _fmt_pressure(p_msl, provider_for_pressure)

    pressure_push: Optional[Tuple[float, int]] = None
    if not _is_nan(p_abs):
        try:
            pressure_push = (p_abs, int(base["epoch"]))
        except (KeyError, TypeError, ValueError):
            pressure_push = None

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

    # ---- 6. Termodinámica básica + extendida -------------------------
    # ``add_basic_derived`` calcula y escribe ``Td``, ``feels_like`` y
    # ``heat_index`` en ``base`` con las fórmulas estándar (Magnus-Tetens,
    # Steadman 1984, Rothfusz). Se reutiliza también desde
    # ``server/services/wu.py`` para garantizar consistencia entre el
    # endpoint ``/current`` y el ``/processed``.
    add_basic_derived(base)

    # Magnitudes extendidas que solo aplican aquí (necesitan ``p_abs`` u
    # otras combinaciones que el helper básico no incluye).
    e_sat = e_v = Td_calc = Tw = q_val = q_gkg = theta = Tv_val = Te_val = NaN
    rho_val = rho_v_gm3 = lcl_val = NaN

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

    # ---- 7. Radiación / UV / claridad --------------------------------
    solar_rad = base.get("solar_radiation", NaN) if base.get("solar_radiation") is not None else NaN
    uv = base.get("uv", NaN) if base.get("uv") is not None else NaN
    has_radiation = (not _is_nan(solar_rad)) or (not _is_nan(uv))
    et0 = clarity = balance = NaN

    if has_radiation:
        lat = base.get("lat", NaN)
        lon = base.get("lon", NaN)
        clarity = sky_clarity_index(
            solar_rad, lat, z, base.get("epoch", 0), lon, tz_name=ctx.sun_tz_name,
        )

    # ---- 8. Serie temporal del chart --------------------------------
    raw_series = ctx.series_override if ctx.series_override is not None else base.get("_series")
    chart_series = normalize_chart_series(raw_series if isinstance(raw_series, Mapping) else {})
    chart_epochs = chart_series["epochs"]
    chart_temps = chart_series["temps"]
    chart_humidities = chart_series["humidities"]
    chart_pressures = chart_series["pressures_abs"]
    chart_winds = chart_series["winds"]
    chart_solar_radiations = chart_series["solar_radiations"]
    has_chart_data = bool(chart_series.get("has_data"))

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
    processed = ProcessedObservation(
        z=z,
        p_abs=p_abs, p_msl=p_msl, p_abs_disp=p_abs_disp, p_msl_disp=p_msl_disp,
        dp3=dp3, rate_h=rate_h, p_label=p_label, p_arrow=p_arrow,
        inst_mm_h=inst_mm_h, r5_mm_h=r5_mm_h, r10_mm_h=r10_mm_h, inst_label=inst_label,
        e_sat=e_sat, e=e_v, Td_calc=Td_calc, Tw=Tw, q=q_val, q_gkg=q_gkg,
        theta=theta, Tv=Tv_val, Te=Te_val, rho=rho_val, rho_v_gm3=rho_v_gm3, lcl=lcl_val,
        solar_rad=solar_rad, uv=uv, et0=et0, clarity=clarity, balance=balance,
        has_radiation=has_radiation, has_chart_data=has_chart_data,
    )

    return ProcessingResult(
        processed=processed,
        base=base,
        chart_series=chart_series,
        trend_hourly_series=trend_hourly_series,
        session_updates=session_updates,
        warnings=warnings,
        pressure_push=pressure_push,
        chart_series_owner=chart_series_owner,
        trend_hourly_owner_action=trend_hourly_owner_action,
        trend_hourly_owner=trend_hourly_owner,
    )
