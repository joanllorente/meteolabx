"""
Esquemas Pydantic de observaciones meteorológicas.

Diseño:
- ``CurrentObservation`` modela el shape público devuelto por
  ``GET/POST /v1/observations/current``. Es **independiente del proveedor**;
  cada servicio (WU, AEMET, Meteocat…) produce un dict crudo que se
  normaliza a este modelo antes de salir por HTTP.
- Los floats meteorológicos usan ``Optional[float]`` (``None`` en JSON)
  en vez de NaN porque NaN **no es JSON válido**; los parsers estándar lo
  rechazan o lo interpretan distinto. El helper ``from_provider_dict``
  hace la conversión NaN → None.
- Los request bodies viven en este mismo módulo por cohesión: una
  observación = un request + una response. Si crecen, se separan.

Convención de campos: contrato canónico independiente del proveedor.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Mapping, Optional

from pydantic import BaseModel, Field, ValidationInfo, field_validator


def _nan_to_none(value: Any) -> Optional[float]:
    """Convierte NaN/None/strings inválidos en ``None``; floats normales se mantienen."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


# =====================================================================
# Request
# =====================================================================

class _ProviderStationRequest(BaseModel):
    """
    Base común para peticiones que identifican proveedor + estación
    (+ credenciales si aplica). Heredan ``CurrentObservationRequest``
    y ``TodaySeriesRequest``.

    Auth por proveedor:
    - ``WU``: la ``api_key`` viaja en cada petición (per-user).
    - ``AEMET``: la API key vive en el backend (env var
      ``METEOLABX_AEMET_API_KEY``); ``api_key`` en el body se ignora.
    - ``METEOCAT``: igual que AEMET, key del servidor (env var
      ``METEOLABX_METEOCAT_API_KEY``).
    - ``EUSKALMET``: credenciales del servidor (JWT manual o
      autogenerado desde PEM + api key opcional).

    Por eso ``api_key`` es **opcional** (string vacío permitido). El
    router valida lo que necesita cada proveedor.

    ⚠️ Cuando hay ``api_key`` no vacía es un secreto: nunca debe aparecer
    en logs, métricas ni respuestas.
    """

    provider: Literal[
        "WU", "AEMET", "METEOCAT", "EUSKALMET", "METEOGALICIA", "NWS",
        "METEOFRANCE", "METOFFICE", "FROST", "POEM", "METEOHUB_IT",
        "IPMA", "GEOSPHERE", "SMHI", "ECCC", "IEM", "WEATHERLINK", "WINDY", "NETATMO",
    ] = Field(
        default="WU",
        description=(
            "Identificador del proveedor. ``WU`` (Weather Underground), "
            "``AEMET`` (OpenData España), ``METEOCAT`` (XEMA Catalunya), "
            "``EUSKALMET`` (Euskadi), ``METEOGALICIA`` (Galicia, API "
            "pública), ``NWS`` (EE. UU., API pública), ``METEOFRANCE`` "
            "(DPObs) o ``METOFFICE`` (DataHub, geohash como station_id)."
        ),
    )
    station_id: str = Field(
        description="ID de estación. WU: ``IBARCE12345``; AEMET: IDEMA tipo ``0201X``; Meteocat: codi tipo ``C6``.",
        min_length=1,
        max_length=128,
    )
    api_key: str = Field(
        default="",
        description=(
            "API key del proveedor cuando aplica. WU la requiere; AEMET "
            "la ignora (usa la del backend). **No se loguea**."
        ),
        max_length=4096,  # algunos proveedores (AEMET) usan JWT largos (>256)
    )
    api_secret: str = Field(
        default="",
        description=(
            "Secreto adicional para proveedores con doble credencial "
            "per-user (WeatherLink: ``X-Api-Secret``). Vacío para el "
            "resto. **No se loguea**."
        ),
        max_length=4096,
    )

    @field_validator("station_id", mode="before")
    @classmethod
    def _normalize_station(cls, value: Any, info: ValidationInfo) -> str:
        station_id = str(value or "").strip()
        # Windy es case-sensitive y las MAC de Netatmo van en minúsculas.
        if info.data.get("provider") in ("WINDY", "NETATMO"):
            return station_id
        return station_id.upper()

    @field_validator("api_key", "api_secret", mode="before")
    @classmethod
    def _normalize_api_key(cls, value: Any) -> str:
        return str(value or "").strip()


class _CalibrationRequestMixin(BaseModel):
    """
    Mixin: offsets de calibración por sensor (solo WU). Compartido por
    ``/current/processed`` y ``/series/recent`` para que la serie del día
    y la serie sinóptica se calibren de forma idéntica en el backend.
    La **configuración** vive en el frontend (localStorage) y viaja en cada
    petición; el backend nunca la persiste.
    """

    calibration: Optional[Dict[str, float]] = Field(
        default=None,
        description=(
            "Offsets de calibración por sensor para WU. Se aplican a la "
            "observación y a la serie antes de calcular las derivadas. "
            "Se ignoran para otros proveedores."
        ),
    )

    @field_validator("calibration", mode="before")
    @classmethod
    def validate_calibration(cls, value):
        if value in (None, {}):
            return None
        if not isinstance(value, dict):
            raise ValueError("calibration must be an object")
        allowed = {
            "barometer", "wind_vane", "thermometer", "hygrometer",
            "anemometer", "rain_gauge", "pyranometer",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown calibration sensors: {sorted(unknown)}")
        normalized = {}
        for key, raw in value.items():
            try:
                number = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid calibration value for {key}") from exc
            if not math.isfinite(number):
                raise ValueError(f"invalid calibration value for {key}")
            normalized[key] = number
        return normalized


class CurrentObservationRequest(_ProviderStationRequest):
    """Petición de observación actual para ``POST /v1/observations/current``."""


class TodaySeriesRequest(_ProviderStationRequest):
    """Petición de series del día para ``POST /v1/observations/series/today``."""

    station_elevation: Optional[float] = Field(
        default=None,
        ge=-500.0,
        le=9000.0,
        description="Altitud de usuario (m) para derivar presión absoluta en series.",
    )
    lookback_hours: int = Field(
        default=0,
        ge=0,
        le=12,
        description=(
            "Horas previas al inicio del día local que se anteponen a la "
            "serie para calcular derivadas al arrancar el día. Por defecto "
            "0 para mantener limpios los gráficos de Observación."
        ),
    )


class RecentSeriesRequest(_ProviderStationRequest, _CalibrationRequestMixin):
    """
    Petición de serie reciente (ventana de días) para
    ``POST /v1/observations/series/recent``. Alimenta la pestaña
    Tendencias: temperatura/humedad/presión a resolución sinóptica.
    Acepta ``calibration`` (WU) para calibrar la serie sinóptica igual
    que ``/current/processed`` calibra la del día.
    """

    days_back: int = Field(
        default=7,
        ge=1,
        le=14,
        description="Días hacia atrás de la ventana (1-14; por defecto 7).",
    )
    station_elevation: Optional[float] = Field(
        default=None,
        ge=-500.0,
        le=9000.0,
        description="Altitud de usuario (m) para derivar presión absoluta en series.",
    )


# =====================================================================
# Response
# =====================================================================

class CurrentObservation(BaseModel):
    """
    Observación meteorológica actual en una estación.

    Todos los campos numéricos son ``Optional[float]``: ``None`` indica
    que el sensor no reportó o el valor no es aplicable (p.ej.
    ``wind_chill`` con temperatura templada).

    Unidades:
        - Temperaturas (``Tc``, ``Td``, ``feels_like``, ``heat_index``,
          ``wind_chill``): °C
        - Humedad relativa (``RH``): %
        - Presión (``p_hpa``): hPa absoluta
        - Viento (``wind``, ``gust``): km/h
        - Dirección viento (``wind_dir_deg``): grados (0=N, 90=E)
        - Precipitación (``precip_rate``): mm/h
        - Precipitación total día (``precip_total``): mm
        - Radiación solar (``solar_radiation``): W/m²
        - UV (``uv``): índice (0-11+)
        - Altitud (``elevation``): m
        - ``epoch``: timestamp Unix UTC en segundos
    """

    # Identificación temporal y espacial
    epoch: int = Field(description="Timestamp Unix UTC en segundos.")
    time_local: str = Field(default="", description="Hora local reportada por la estación.")
    time_utc: str = Field(default="", description="Hora UTC reportada por la estación (ISO 8601).")
    lat: Optional[float] = Field(default=None, description="Latitud de la estación (grados decimales).")
    lon: Optional[float] = Field(default=None, description="Longitud de la estación (grados decimales).")
    elevation: Optional[float] = Field(default=None, description="Altitud de la estación (m).")

    # Variables meteo
    Tc: Optional[float] = Field(default=None, description="Temperatura del aire (°C).")
    RH: Optional[float] = Field(default=None, description="Humedad relativa (%).")
    p_hpa: Optional[float] = Field(default=None, description="Presión absoluta (hPa).")
    Td: Optional[float] = Field(default=None, description="Punto de rocío (°C).")
    wind: Optional[float] = Field(default=None, description="Velocidad de viento sostenido (km/h).")
    gust: Optional[float] = Field(default=None, description="Velocidad de ráfaga (km/h).")
    wind_dir_deg: Optional[float] = Field(default=None, description="Dirección del viento (°, 0=N).")
    feels_like: Optional[float] = Field(default=None, description="Sensación térmica (°C).")
    heat_index: Optional[float] = Field(default=None, description="Heat index NOAA si Tc ≥ 25 (°C).")
    wind_chill: Optional[float] = Field(default=None, description="Wind chill si Tc ≤ 10 y wind ≥ 4.8 km/h (°C).")
    precip_rate: Optional[float] = Field(default=None, description="Intensidad de precipitación (mm/h).")
    precip_total: Optional[float] = Field(default=None, description="Precipitación acumulada del día (mm).")
    solar_radiation: Optional[float] = Field(default=None, description="Irradiancia global (W/m²).")
    uv: Optional[float] = Field(default=None, description="Índice UV.")

    @classmethod
    def from_provider_dict(cls, data: Dict[str, Any]) -> "CurrentObservation":
        """
        Construye el modelo a partir del ``dict`` que devuelven los
        servicios (``server/services/wu.py``, etc.), saneando NaN.

        Este es el único punto de conversión proveedor→API. Si en el
        futuro un proveedor introduce un nombre de campo distinto, su
        servicio se encarga de devolver el dict con las claves comunes;
        este modelo no cambia.
        """
        epoch_raw = data.get("epoch", 0) or 0
        try:
            epoch = int(epoch_raw)
        except (TypeError, ValueError):
            epoch = 0

        return cls(
            epoch=epoch,
            time_local=str(data.get("time_local", "") or ""),
            time_utc=str(data.get("time_utc", "") or ""),
            lat=_nan_to_none(data.get("lat")),
            lon=_nan_to_none(data.get("lon")),
            elevation=_nan_to_none(data.get("elevation")),
            Tc=_nan_to_none(data.get("Tc")),
            RH=_nan_to_none(data.get("RH")),
            p_hpa=_nan_to_none(data.get("p_hpa")),
            Td=_nan_to_none(data.get("Td")),
            wind=_nan_to_none(data.get("wind")),
            gust=_nan_to_none(data.get("gust")),
            wind_dir_deg=_nan_to_none(data.get("wind_dir_deg")),
            feels_like=_nan_to_none(data.get("feels_like")),
            heat_index=_nan_to_none(data.get("heat_index")),
            wind_chill=_nan_to_none(data.get("wind_chill")),
            precip_rate=_nan_to_none(data.get("precip_rate")),
            precip_total=_nan_to_none(data.get("precip_total")),
            solar_radiation=_nan_to_none(data.get("solar_radiation")),
            uv=_nan_to_none(data.get("uv")),
        )


# =====================================================================
# Series temporales del día
# =====================================================================

def _array_nan_to_none(values: Any) -> List[Optional[float]]:
    """Convierte una lista/tupla de floats (posibles NaN) a ``List[Optional[float]]``."""
    if not isinstance(values, (list, tuple)):
        return []
    return [_nan_to_none(v) for v in values]


def _valid_epoch_indices(values: Any) -> tuple[List[int], List[int]]:
    """Devuelve epochs válidos y sus índices para filtrar arrays paralelos."""
    epochs: List[int] = []
    indices: List[int] = []
    for index, value in enumerate(values or []):
        try:
            epoch = int(value)
        except (TypeError, ValueError):
            continue
        if epoch > 0:
            epochs.append(epoch)
            indices.append(index)
    return epochs, indices


def _aligned_array_nan_to_none(values: Any, indices: List[int]) -> List[Optional[float]]:
    if not isinstance(values, (list, tuple)):
        return []
    return [_nan_to_none(values[index]) for index in indices if index < len(values)]


class TodaySeries(BaseModel):
    """
    Series temporales del día (típicamente ~288 puntos a 5 min) para una
    estación. Cada array tiene la misma longitud que ``epochs``; las
    posiciones con datos ausentes son ``null``.

    Pensado para alimentar las gráficas de la pestaña Observación
    (temperatura, humedad, dewpoint, presión, radiación, viento) sin que
    el frontend tenga que renormalizar campos del payload crudo de WU.
    """

    epochs: List[int] = Field(
        default_factory=list,
        description="Timestamps Unix UTC en segundos, uno por punto.",
    )
    temps: List[Optional[float]] = Field(default_factory=list, description="Temperatura del aire (°C).")
    humidities: List[Optional[float]] = Field(default_factory=list, description="Humedad relativa (%).")
    dewpts: List[Optional[float]] = Field(default_factory=list, description="Punto de rocío (°C).")
    pressures: List[Optional[float]] = Field(
        default_factory=list,
        description="Presión (hPa) tal como la reporta el proveedor (en WU es MSL para series).",
    )
    pressures_abs: List[Optional[float]] = Field(
        default_factory=list,
        description=(
            "Presión ABSOLUTA (a nivel de estación, hPa). El frontend consume "
            "la serie de presión por esta clave (tendencia 3h, θe, razón de "
            "mezcla); si el proveedor solo da MSL, se reconstruye con la "
            "altitud. Vacío solo si no hay forma de derivarla."
        ),
    )
    uv_indexes: List[Optional[float]] = Field(default_factory=list, description="Índice UV.")
    solar_radiations: List[Optional[float]] = Field(default_factory=list, description="Irradiancia global (W/m²).")
    precips: List[Optional[float]] = Field(default_factory=list, description="Precipitación acumulada del día (mm).")
    winds: List[Optional[float]] = Field(default_factory=list, description="Velocidad de viento (km/h).")
    gusts: List[Optional[float]] = Field(default_factory=list, description="Velocidad de ráfaga (km/h).")
    wind_dirs: List[Optional[float]] = Field(default_factory=list, description="Dirección del viento (°).")
    theta_e: List[Optional[float]] = Field(default_factory=list, description="Temperatura potencial equivalente (K).")
    mixing_ratios: List[Optional[float]] = Field(default_factory=list, description="Razón de mezcla (g/kg).")
    theta_e_trends: List[Optional[float]] = Field(default_factory=list, description="Derivada temporal de θe (K/h).")
    mixing_ratio_trends: List[Optional[float]] = Field(default_factory=list, description="Derivada temporal de razón de mezcla ((g/kg)/h).")
    pressure_trends: List[Optional[float]] = Field(default_factory=list, description="Derivada temporal de presión absoluta (hPa/h).")
    vapor_pressures: List[Optional[float]] = Field(default_factory=list, description="Presión de vapor (hPa).")
    saturation_pressures: List[Optional[float]] = Field(default_factory=list, description="Presión de vapor de saturación (hPa).")
    theoretical_solar_radiations: List[Optional[float]] = Field(default_factory=list, description="Irradiancia teórica de cielo despejado (W/m²).")
    wind_u: List[Optional[float]] = Field(default_factory=list, description="Componente zonal del viento (km/h).")
    wind_v: List[Optional[float]] = Field(default_factory=list, description="Componente meridional del viento (km/h).")
    sunrise_epoch: Optional[int] = None
    sunset_epoch: Optional[int] = None
    solar_altitude: Optional[float] = None
    solar_altitude_max: Optional[float] = None
    is_nighttime: Optional[bool] = None
    theta_e_interval_minutes: int = 0
    mixing_ratio_interval_minutes: int = 0
    pressure_interval_minutes: int = 180

    lat: Optional[float] = Field(default=None, description="Latitud detectada del payload (fallback si current no la trajo).")
    lon: Optional[float] = Field(default=None, description="Longitud detectada del payload.")

    has_data: bool = Field(
        default=False,
        description="``True`` si hay al menos un punto válido; útil para decidir si renderizar gráficos.",
    )

    @classmethod
    def from_provider_dict(cls, data: Dict[str, Any]) -> "TodaySeries":
        """Construye el modelo desde el dict canónico del servicio."""
        epochs, valid_indices = _valid_epoch_indices(data.get("epochs", []))

        def aligned(field: str) -> List[Optional[float]]:
            return _aligned_array_nan_to_none(data.get(field), valid_indices)

        return cls(
            epochs=epochs,
            temps=aligned("temps"), humidities=aligned("humidities"),
            dewpts=aligned("dewpts"), pressures=aligned("pressures"),
            pressures_abs=aligned("pressures_abs"), uv_indexes=aligned("uv_indexes"),
            solar_radiations=aligned("solar_radiations"), precips=aligned("precips"),
            winds=aligned("winds"), gusts=aligned("gusts"), wind_dirs=aligned("wind_dirs"),
            theta_e=aligned("theta_e"), mixing_ratios=aligned("mixing_ratios"),
            theta_e_trends=aligned("theta_e_trends"), mixing_ratio_trends=aligned("mixing_ratio_trends"),
            pressure_trends=aligned("pressure_trends"), vapor_pressures=aligned("vapor_pressures"),
            saturation_pressures=aligned("saturation_pressures"),
            theoretical_solar_radiations=aligned("theoretical_solar_radiations"),
            wind_u=aligned("wind_u"), wind_v=aligned("wind_v"),
            sunrise_epoch=data.get("sunrise_epoch"),
            sunset_epoch=data.get("sunset_epoch"),
            solar_altitude=_nan_to_none(data.get("solar_altitude")),
            solar_altitude_max=_nan_to_none(data.get("solar_altitude_max")),
            is_nighttime=data.get("is_nighttime"),
            theta_e_interval_minutes=int(data.get("theta_e_interval_minutes", 0) or 0),
            mixing_ratio_interval_minutes=int(data.get("mixing_ratio_interval_minutes", 0) or 0),
            pressure_interval_minutes=int(data.get("pressure_interval_minutes", 180) or 180),
            lat=_nan_to_none(data.get("lat")),
            lon=_nan_to_none(data.get("lon")),
            has_data=bool(data.get("has_data", False)),
        )


class RecentSeries(BaseModel):
    """
    Serie reciente (~días) a resolución sinóptica para tendencias.
    Solo las magnitudes que usa la pestaña Tendencias: temperatura,
    humedad y presión MSL. Arrays paralelas a ``epochs`` con ``null``
    en huecos.
    """

    epochs: List[int] = Field(default_factory=list, description="Timestamps Unix UTC (s).")
    temps: List[Optional[float]] = Field(default_factory=list, description="Temperatura (°C).")
    humidities: List[Optional[float]] = Field(default_factory=list, description="Humedad relativa (%).")
    dewpts: List[Optional[float]] = Field(
        default_factory=list,
        description="Punto de rocío (°C). Solo WU lo reporta nativo; el resto lista vacía.",
    )
    pressures: List[Optional[float]] = Field(default_factory=list, description="Presión MSL (hPa).")
    pressures_abs: List[Optional[float]] = Field(default_factory=list, description="Presión absoluta (hPa).")
    theta_e: List[Optional[float]] = Field(default_factory=list, description="Temperatura potencial equivalente (K).")
    mixing_ratios: List[Optional[float]] = Field(default_factory=list, description="Razón de mezcla (g/kg).")
    theta_e_trends: List[Optional[float]] = Field(default_factory=list, description="Derivada temporal de θe (K/h).")
    mixing_ratio_trends: List[Optional[float]] = Field(default_factory=list, description="Derivada temporal de razón de mezcla ((g/kg)/h).")
    pressure_trends: List[Optional[float]] = Field(default_factory=list, description="Derivada temporal de presión absoluta (hPa/h).")
    theta_e_interval_minutes: int = 0
    mixing_ratio_interval_minutes: int = 0
    pressure_interval_minutes: int = 180
    lat: Optional[float] = Field(default=None, description="Latitud de la estación.")
    lon: Optional[float] = Field(default=None, description="Longitud de la estación.")
    has_data: bool = Field(default=False, description="``True`` si hay al menos un punto.")

    @classmethod
    def from_provider_dict(cls, data: Dict[str, Any]) -> "RecentSeries":
        epochs, valid_indices = _valid_epoch_indices(data.get("epochs", []))

        def aligned(field: str) -> List[Optional[float]]:
            return _aligned_array_nan_to_none(data.get(field), valid_indices)
        return cls(
            epochs=epochs,
            temps=aligned("temps"), humidities=aligned("humidities"),
            dewpts=aligned("dewpts"), pressures=aligned("pressures"),
            pressures_abs=aligned("pressures_abs"), theta_e=aligned("theta_e"),
            mixing_ratios=aligned("mixing_ratios"), theta_e_trends=aligned("theta_e_trends"),
            mixing_ratio_trends=aligned("mixing_ratio_trends"), pressure_trends=aligned("pressure_trends"),
            theta_e_interval_minutes=int(data.get("theta_e_interval_minutes", 0) or 0),
            mixing_ratio_interval_minutes=int(data.get("mixing_ratio_interval_minutes", 0) or 0),
            pressure_interval_minutes=int(data.get("pressure_interval_minutes", 180) or 180),
            lat=_nan_to_none(data.get("lat")),
            lon=_nan_to_none(data.get("lon")),
            has_data=bool(data.get("has_data", False)),
        )


# =====================================================================
# Observación procesada (current + derivadas)
# =====================================================================

class ObservationDerivatives(BaseModel):
    """
    Magnitudes derivadas de una observación: termodinámica, presión,
    radiación, ET0 y tendencias en el contrato público de FastAPI.

    Unidades:
        - Altitud (``z``): m
        - Presión (``p_abs``, ``p_msl``): hPa
        - Tendencia 3h (``dp3``): hPa; (``rate_h``): hPa/h
        - Precipitación (``inst_mm_h``, ``r5_mm_h``, ``r10_mm_h``): mm/h
        - Termodinámica: SI (e_sat/e en Pa, Td/Tw en °C, q adim, q_gkg g/kg,
          θ/Tv/Te en K, ρ kg/m³, rho_v en g/m³, lcl en m)
        - Radiación (``solar_rad``): W/m²; UV (``uv``): índice
        - ET0/balance: mm/día
        - ``clarity``: índice [0,1] (transparencia atmosférica)
    """
    z: float = Field(description="Altitud usada para los cálculos (m).")

    # Presión
    p_abs: Optional[float] = Field(default=None, description="Presión absoluta (hPa).")
    p_msl: Optional[float] = Field(default=None, description="Presión MSL (hPa).")
    p_abs_disp: str = Field(default="—", description="Presión absoluta formateada para display.")
    p_msl_disp: str = Field(default="—", description="Presión MSL formateada para display.")

    # Tendencia presión 3h
    dp3: Optional[float] = Field(default=None, description="Variación 3h (hPa).")
    rate_h: Optional[float] = Field(default=None, description="Tasa de cambio 3h (hPa/h).")
    p_label: str = Field(default="—", description="Etiqueta humana de tendencia.")
    p_arrow: str = Field(default="•", description="Glifo flecha de tendencia.")

    # Precipitación instantánea/recientes
    inst_mm_h: Optional[float] = Field(default=None, description="Intensidad actual (mm/h).")
    r5_mm_h: Optional[float] = Field(default=None, description="Intensidad acumulada últimos 5 min (mm/h).")
    r10_mm_h: Optional[float] = Field(default=None, description="Intensidad acumulada últimos 10 min (mm/h).")
    inst_label: str = Field(default="Sin precipitación", description="Etiqueta humana de intensidad.")

    # Termodinámica
    e_sat: Optional[float] = Field(default=None, description="Presión vapor saturante (Pa).")
    e: Optional[float] = Field(default=None, description="Presión vapor actual (Pa).")
    Td_calc: Optional[float] = Field(default=None, description="Punto de rocío calculado (°C).")
    Tw: Optional[float] = Field(default=None, description="Temperatura húmeda (°C).")
    q: Optional[float] = Field(default=None, description="Humedad específica (kg/kg).")
    q_gkg: Optional[float] = Field(default=None, description="Humedad específica (g/kg).")
    theta: Optional[float] = Field(default=None, description="Temperatura potencial (K).")
    Tv: Optional[float] = Field(default=None, description="Temperatura virtual (K).")
    Te: Optional[float] = Field(default=None, description="Temperatura equivalente (K).")
    rho: Optional[float] = Field(default=None, description="Densidad del aire (kg/m³).")
    rho_v_gm3: Optional[float] = Field(default=None, description="Densidad de vapor (g/m³).")
    lcl: Optional[float] = Field(default=None, description="Altura del LCL (m).")
    sound_speed_ms: Optional[float] = Field(default=None, description="Velocidad del sonido en aire húmedo (m/s).")
    wet_bulb_risk: str = Field(default="", description="Categoría estable: potential, critical o extreme.")
    wet_bulb_alert_level: str = Field(default="", description="Nivel de alerta estable: warning o danger.")
    heat_index_risk: str = Field(default="", description="Categoría estable: high, very_high o extreme.")
    heat_index_alert_level: str = Field(default="", description="Nivel de alerta estable: warning o danger.")

    # Radiación + ET0
    solar_rad: Optional[float] = Field(default=None, description="Irradiancia solar (W/m²).")
    uv: Optional[float] = Field(default=None, description="Índice UV.")
    et0: Optional[float] = Field(default=None, description="ET0 acumulada hoy (mm).")
    clarity: Optional[float] = Field(default=None, description="Índice de claridad del cielo (0..1).")
    balance: Optional[float] = Field(default=None, description="Balance hídrico hoy (mm).")
    solar_energy_today_wh_m2: Optional[float] = Field(default=None, description="Energía solar integrada hoy (Wh/m²).")
    erythemal_irradiance_mw_m2: Optional[float] = Field(default=None, description="Irradiancia eritemática efectiva (mW/m²).")
    erythemal_dose_today_j_m2: Optional[float] = Field(default=None, description="Dosis eritemática acumulada hoy (J/m²).")
    erythemal_dose_today_sed: Optional[float] = Field(default=None, description="Dosis eritemática acumulada hoy (SED).")
    has_radiation: bool = Field(description="True si solar_rad o uv tienen valor.")
    has_chart_data: bool = Field(description="True si la serie del día tiene datos.")

    @classmethod
    def from_mapping(cls, processed: Mapping[str, Any]) -> "ObservationDerivatives":
        """Construye el schema canónico desde el mapping del dominio."""
        value = processed.get
        return cls(
            z=float(value("z", 0.0)),
            p_abs=_nan_to_none(value("p_abs")), p_msl=_nan_to_none(value("p_msl")),
            p_abs_disp=str(value("p_abs_disp", "—")), p_msl_disp=str(value("p_msl_disp", "—")),
            dp3=_nan_to_none(value("dp3")), rate_h=_nan_to_none(value("rate_h")),
            p_label=str(value("p_label", "—")), p_arrow=str(value("p_arrow", "•")),
            inst_mm_h=_nan_to_none(value("inst_mm_h")), r5_mm_h=_nan_to_none(value("r5_mm_h")),
            r10_mm_h=_nan_to_none(value("r10_mm_h")), inst_label=str(value("inst_label", "Sin precipitación")),
            e_sat=_nan_to_none(value("e_sat")), e=_nan_to_none(value("e")),
            Td_calc=_nan_to_none(value("Td_calc")), Tw=_nan_to_none(value("Tw")),
            q=_nan_to_none(value("q")), q_gkg=_nan_to_none(value("q_gkg")),
            theta=_nan_to_none(value("theta")), Tv=_nan_to_none(value("Tv")),
            Te=_nan_to_none(value("Te")), rho=_nan_to_none(value("rho")),
            rho_v_gm3=_nan_to_none(value("rho_v_gm3")), lcl=_nan_to_none(value("lcl")),
            sound_speed_ms=_nan_to_none(value("sound_speed_ms")),
            wet_bulb_risk=str(value("wet_bulb_risk", "")),
            wet_bulb_alert_level=str(value("wet_bulb_alert_level", "")),
            heat_index_risk=str(value("heat_index_risk", "")),
            heat_index_alert_level=str(value("heat_index_alert_level", "")),
            solar_rad=_nan_to_none(value("solar_rad")), uv=_nan_to_none(value("uv")),
            et0=_nan_to_none(value("et0")), clarity=_nan_to_none(value("clarity")),
            balance=_nan_to_none(value("balance")),
            solar_energy_today_wh_m2=_nan_to_none(value("solar_energy_today_wh_m2")),
            erythemal_irradiance_mw_m2=_nan_to_none(value("erythemal_irradiance_mw_m2")),
            erythemal_dose_today_j_m2=_nan_to_none(value("erythemal_dose_today_j_m2")),
            erythemal_dose_today_sed=_nan_to_none(value("erythemal_dose_today_sed")),
            has_radiation=bool(value("has_radiation", False)),
            has_chart_data=bool(value("has_chart_data", False)),
        )


class StationInfo(BaseModel):
    """
    Metadata de la estación desde el catálogo del backend (o desde la
    propia observación para proveedores per-user sin catálogo, como WU
    y WeatherLink).
    """

    provider: str = Field(description="Identificador del proveedor.")
    network: str = Field(default="", description="Red interna del proveedor/agregador, si aplica.")
    station_id: str = Field(description="ID de la estación.")
    name: str = Field(default="", description="Nombre legible de la estación.")
    lat: Optional[float] = Field(default=None)
    lon: Optional[float] = Field(default=None)
    elevation: Optional[float] = Field(default=None, description="Altitud (m).")
    tz: Optional[str] = Field(default=None, description="Timezone IANA de la estación.")
    country: Optional[str] = Field(default=None, description="Código de país del catálogo, si existe.")
    region: Optional[str] = Field(default=None, description="Región administrativa del catálogo, si existe.")
    locality: Optional[str] = Field(default=None, description="Localidad del catálogo, si existe.")
    connectable: bool = Field(
        default=True,
        description="Indica si MeteoLabX puede conectar esta estación al backend de observaciones.",
    )
    has_historical: bool = Field(
        default=False,
        description="Indica si la estación tiene histórico disponible en la pestaña Histórico.",
    )
    is_historical_only: bool = Field(
        default=False,
        description="Indica si la estación está archivada: tiene histórico, pero no observación actual.",
    )
    manual: bool = Field(
        default=False,
        description=(
            "Estación de observador MANUAL/convencional (IEM COOP/CoCoRaHS, "
            "red KLIMA de GeoSphere): publica una lectura al día, sin datos "
            "en tiempo real."
        ),
    )
    sensors: Optional[Dict[str, bool]] = Field(
        default=None,
        description=(
            "Sensores declarados en el catálogo (thermometer, hygrometer, "
            "barometer, anemometer, wind_vane, rain_gauge, pyranometer, uv). "
            "``null`` si el proveedor no publica inventario de sensores."
        ),
    )


class DailyExtremes(BaseModel):
    """
    Extremos del día local calculados en el backend a partir de la
    serie del día + la observación actual (mismo criterio que usaba el
    frontend). ``precip_total`` es el acumulado diario del proveedor.
    """

    temp_max: Optional[float] = Field(default=None, description="Máxima del día (°C).")
    temp_min: Optional[float] = Field(default=None, description="Mínima del día (°C).")
    rh_max: Optional[float] = Field(default=None, description="HR máxima del día (%).")
    rh_min: Optional[float] = Field(default=None, description="HR mínima del día (%).")
    gust_max: Optional[float] = Field(default=None, description="Racha máxima del día (km/h).")
    precip_total: Optional[float] = Field(default=None, description="Precipitación del día (mm).")


class WarningItem(BaseModel):
    """
    Aviso estructurado del pipeline con código estable.

    El backend emite ``{"code", "params"}`` (códigos en
    ``domain.observation_warnings``) en vez de texto libre; el frontend
    traduce vía i18n (clave ``warnings.<code>``) usando ``params`` como
    argumentos de ``str.format``.
    """
    code: str = Field(description="Código estable del aviso (p. ej. ``data_age``).")
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parámetros para interpolar en el mensaje traducido.",
    )


class ProcessedCurrentObservationResponse(BaseModel):
    """
    Respuesta del endpoint ``POST /v1/observations/current/processed``.

    Une la observación cruda (ya con ``Td``, ``feels_like`` y ``heat_index``
    calculados por el pipeline) con las derivadas meteorológicas
    (``ObservationDerivatives``) y la lista de avisos que el pipeline
    haya generado (p. ej. datos antiguos).
    """
    observation: CurrentObservation = Field(
        description="Observación cruda + ``Td``, ``feels_like``, ``heat_index`` calculados."
    )
    derivatives: ObservationDerivatives = Field(
        description="Magnitudes derivadas: presión, termodinámica, radiación, ET0, tendencias."
    )
    warnings: List[WarningItem] = Field(
        default_factory=list,
        description=(
            "Avisos estructurados con código estable (``data_age``, "
            "``missing_elevation``…). El frontend los traduce vía i18n."
        ),
    )
    station: Optional[StationInfo] = Field(
        default=None,
        description="Metadata de la estación (catálogo backend + sensors).",
    )
    daily_extremes: Optional[DailyExtremes] = Field(
        default=None,
        description="Extremos del día calculados en el backend.",
    )
    series: Optional[TodaySeries] = Field(
        default=None,
        description=(
            "Serie del día ya descargada por el pipeline. Incluirla aquí "
            "hace de ``/processed`` el payload completo de dashboard: "
            "observación + derivadas + extremos + serie + estación en "
            "UNA petición."
        ),
    )


class ProcessedCurrentObservationRequest(_ProviderStationRequest, _CalibrationRequestMixin):
    """
    Petición para ``POST /v1/observations/current/processed``.

    Comparte la base de credenciales/estación con los otros endpoints y
    añade campos opcionales que afectan al pipeline (timezone para los
    cálculos de claridad del cielo, antigüedad máxima admitida).
    """

    sun_tz_name: str = Field(
        default="",
        description=(
            "Timezone IANA (``Europe/Madrid``…) para los cálculos solares "
            "(claridad del cielo, ortos/ocasos). Si vacío, se usan los "
            "fallbacks del servicio."
        ),
        max_length=64,
    )
    max_data_age_minutes: float = Field(
        default=60.0,
        ge=0.0,
        description=(
            "Si la observación es más antigua, el pipeline incluye un "
            "warning. No bloquea la respuesta."
        ),
    )
    station_elevation: Optional[float] = Field(
        default=None,
        ge=-500.0,
        le=9000.0,
        description=(
            "Altitud introducida por el usuario (m). Si es > 0 SUSTITUYE "
            "a la elevación que reporte el proveedor — misma prioridad "
            "usuario > API que el frontend legacy. Crítica para la "
            "presión absoluta y la termodinámica (θ, ρ, q…)."
        ),
    )
