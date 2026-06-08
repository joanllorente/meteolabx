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

Convención de campos: mismos nombres que el dict legacy del cliente WU
para que el frontend Streamlit pueda consumir esto sin refactorizar tabs.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


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

    Por eso ``api_key`` es **opcional** (string vacío permitido). El
    router valida lo que necesita cada proveedor.

    ⚠️ Cuando hay ``api_key`` no vacía es un secreto: nunca debe aparecer
    en logs, métricas ni respuestas.
    """

    provider: Literal["WU", "AEMET"] = Field(
        default="WU",
        description="Identificador del proveedor. ``WU`` (Weather Underground) o ``AEMET`` (OpenData España).",
    )
    station_id: str = Field(
        description="ID de estación. WU: ``IBARCE12345``; AEMET: IDEMA tipo ``0201X``.",
        min_length=1,
        max_length=64,
    )
    api_key: str = Field(
        default="",
        description=(
            "API key del proveedor cuando aplica. WU la requiere; AEMET "
            "la ignora (usa la del backend). **No se loguea**."
        ),
        max_length=256,
    )

    @field_validator("station_id", mode="before")
    @classmethod
    def _normalize_station(cls, value: Any) -> str:
        return str(value or "").strip().upper()

    @field_validator("api_key", mode="before")
    @classmethod
    def _normalize_api_key(cls, value: Any) -> str:
        return str(value or "").strip()


class CurrentObservationRequest(_ProviderStationRequest):
    """Petición de observación actual para ``POST /v1/observations/current``."""


class TodaySeriesRequest(_ProviderStationRequest):
    """Petición de series del día para ``POST /v1/observations/series/today``."""


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
    uv_indexes: List[Optional[float]] = Field(default_factory=list, description="Índice UV.")
    solar_radiations: List[Optional[float]] = Field(default_factory=list, description="Irradiancia global (W/m²).")
    winds: List[Optional[float]] = Field(default_factory=list, description="Velocidad de viento (km/h).")
    gusts: List[Optional[float]] = Field(default_factory=list, description="Velocidad de ráfaga (km/h).")
    wind_dirs: List[Optional[float]] = Field(default_factory=list, description="Dirección del viento (°).")

    lat: Optional[float] = Field(default=None, description="Latitud detectada del payload (fallback si current no la trajo).")
    lon: Optional[float] = Field(default=None, description="Longitud detectada del payload.")

    has_data: bool = Field(
        default=False,
        description="``True`` si hay al menos un punto válido; útil para decidir si renderizar gráficos.",
    )

    @classmethod
    def from_provider_dict(cls, data: Dict[str, Any]) -> "TodaySeries":
        """Construye el modelo desde el ``dict`` del servicio (mismo shape legacy)."""
        epochs_raw = data.get("epochs", []) or []
        # Aseguramos enteros; ignoramos puntos con epoch inválido.
        epochs: List[int] = []
        for value in epochs_raw:
            try:
                epoch_int = int(value)
            except (TypeError, ValueError):
                continue
            if epoch_int > 0:
                epochs.append(epoch_int)

        return cls(
            epochs=epochs,
            temps=_array_nan_to_none(data.get("temps")),
            humidities=_array_nan_to_none(data.get("humidities")),
            dewpts=_array_nan_to_none(data.get("dewpts")),
            pressures=_array_nan_to_none(data.get("pressures")),
            uv_indexes=_array_nan_to_none(data.get("uv_indexes")),
            solar_radiations=_array_nan_to_none(data.get("solar_radiations")),
            winds=_array_nan_to_none(data.get("winds")),
            gusts=_array_nan_to_none(data.get("gusts")),
            wind_dirs=_array_nan_to_none(data.get("wind_dirs")),
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
    radiación, ET0, tendencias. Espejo del dataclass
    ``domain.observation_pipeline.ProcessedObservation`` pero como modelo
    Pydantic (con ``Optional[float]`` en vez de NaN).

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

    # Radiación + ET0
    solar_rad: Optional[float] = Field(default=None, description="Irradiancia solar (W/m²).")
    uv: Optional[float] = Field(default=None, description="Índice UV.")
    et0: Optional[float] = Field(default=None, description="ET0 acumulada hoy (mm).")
    clarity: Optional[float] = Field(default=None, description="Índice de claridad del cielo (0..1).")
    balance: Optional[float] = Field(default=None, description="Balance hídrico hoy (mm).")
    has_radiation: bool = Field(description="True si solar_rad o uv tienen valor.")
    has_chart_data: bool = Field(description="True si la serie del día tiene datos.")

    @classmethod
    def from_processed_obs(cls, processed: Any) -> "ObservationDerivatives":
        """
        Construye desde un ``ProcessedObservation`` (dataclass del dominio).
        Convierte NaN a None en floats; el resto pasa tal cual.
        """
        return cls(
            z=float(processed.z),
            p_abs=_nan_to_none(processed.p_abs),
            p_msl=_nan_to_none(processed.p_msl),
            p_abs_disp=str(processed.p_abs_disp),
            p_msl_disp=str(processed.p_msl_disp),
            dp3=_nan_to_none(processed.dp3),
            rate_h=_nan_to_none(processed.rate_h),
            p_label=str(processed.p_label),
            p_arrow=str(processed.p_arrow),
            inst_mm_h=_nan_to_none(processed.inst_mm_h),
            r5_mm_h=_nan_to_none(processed.r5_mm_h),
            r10_mm_h=_nan_to_none(processed.r10_mm_h),
            inst_label=str(processed.inst_label),
            e_sat=_nan_to_none(processed.e_sat),
            e=_nan_to_none(processed.e),
            Td_calc=_nan_to_none(processed.Td_calc),
            Tw=_nan_to_none(processed.Tw),
            q=_nan_to_none(processed.q),
            q_gkg=_nan_to_none(processed.q_gkg),
            theta=_nan_to_none(processed.theta),
            Tv=_nan_to_none(processed.Tv),
            Te=_nan_to_none(processed.Te),
            rho=_nan_to_none(processed.rho),
            rho_v_gm3=_nan_to_none(processed.rho_v_gm3),
            lcl=_nan_to_none(processed.lcl),
            solar_rad=_nan_to_none(processed.solar_rad),
            uv=_nan_to_none(processed.uv),
            et0=_nan_to_none(processed.et0),
            clarity=_nan_to_none(processed.clarity),
            balance=_nan_to_none(processed.balance),
            has_radiation=bool(processed.has_radiation),
            has_chart_data=bool(processed.has_chart_data),
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
    warnings: List[str] = Field(
        default_factory=list,
        description=(
            "Avisos generados por el pipeline (p. ej. ``data_age`` cuando la "
            "observación es demasiado antigua). Pensados como hint para "
            "logs/UI; los códigos estables aún no están unificados."
        ),
    )


class ProcessedCurrentObservationRequest(_ProviderStationRequest):
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
