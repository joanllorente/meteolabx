"""
Esquemas del endpoint de datasets históricos/climogramas.

Diseño "lift & shift": el backend ejecuta el dispatcher legacy
(``utils.historical_dispatch.fetch_historical_dataset``) en un
threadpool y devuelve el DataFrame serializado con
``to_json(orient="table")`` (preserva dtypes en el round-trip). Cuando
los fetchers climo se porten a async puro, sustituirán la
implementación por dentro sin tocar este contrato.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ClimoPeriod(BaseModel):
    """Periodo de climograma: etiqueta + rango de fechas inclusivo."""

    label: str = Field(default="", max_length=64)
    start: date
    end: date


class ClimoDatasetRequest(BaseModel):
    """
    Petición de ``POST /v1/climo/dataset``. Calca los argumentos del
    dispatcher legacy: el modo (mensual/anual), los periodos de fechas
    y, para Frost, los periodos de normales climáticas ("1991/2020").
    ``api_secret`` es opcional para todos salvo WeatherLink.
    """

    provider: str = Field(min_length=1, max_length=32)
    station_id: str = Field(min_length=1, max_length=64)
    api_key: str = Field(default="", max_length=4096)  # AEMET usa JWT (>256)
    api_secret: str = Field(default="", max_length=4096)
    summary_mode: Literal["monthly", "annual"] = "monthly"
    periods: List[ClimoPeriod] = Field(default_factory=list)
    selected_years: List[int] = Field(default_factory=list)
    selected_months: List[int] = Field(default_factory=list)
    frost_period: str = Field(default="", max_length=32)
    frost_periods: List[str] = Field(default_factory=list)

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: Any) -> str:
        return str(value or "").strip().upper()

    @field_validator("station_id", mode="before")
    @classmethod
    def _normalize_station(cls, value: Any) -> str:
        return str(value or "").strip()


class ClimoDatasetResponse(BaseModel):
    """
    Dataset histórico serializado.

    ``dataset`` es el JSON ``orient="table"`` del DataFrame (o ``null``
    si el proveedor no devolvió datos); ``extremes`` es el dict de
    extremos diarios que algunos proveedores (Meteocat) adjuntan.
    """

    dataset: Optional[str] = Field(default=None, description="DataFrame en JSON orient='table'.")
    extremes: Optional[Dict[str, Any]] = Field(default=None)
    has_data: bool = False


class ClimoSummaryRequest(ClimoDatasetRequest):
    """Petición de la pestaña de Histórico ya resumida.

    Añade a la petición del dataset el idioma —las etiquetas de métrica se
    traducen en el servidor, donde vive el catálogo— y las preferencias de
    unidades, que deciden si los valores salen en °C o °F y en mm o pulgadas.
    """

    language: str = Field(default="es", max_length=8)
    unit_preferences: Dict[str, str] = Field(default_factory=dict)


class ClimoMetricRow(BaseModel):
    """Fila de una tabla de métricas: nombre ya traducido y valor formateado.

    ``key`` es la clave canónica —``absolute_max``, ``max_gust``…—, y es lo
    que permite al frontend agrupar dos filas en una sola tarjeta sin tener
    que reconocer el nombre traducido a seis idiomas.
    """

    key: str = ""
    metric: str
    value: str = ""
    date: str = ""


class ClimoChartSeries(BaseModel):
    """Series del climograma: barras de precipitación y líneas de temperatura."""

    labels: List[str] = Field(default_factory=list)
    temp_mean: List[Optional[float]] = Field(default_factory=list)
    temp_max: List[Optional[float]] = Field(default_factory=list)
    temp_min: List[Optional[float]] = Field(default_factory=list)
    precip_total: List[Optional[float]] = Field(default_factory=list)


class ClimoWindSeries(BaseModel):
    """Serie de viento del periodo: media, racha y rumbo por punto.

    Cada red publica un trozo distinto, así que las listas pueden venir
    vacías o llenas de huecos: sin veleta no hay ``direction``, y hay
    históricos que solo dan la racha. ``direction_kind`` dice de qué veleta
    sale el rumbo —``mean`` la del viento medio, ``gust`` la de la racha—,
    que no es lo mismo y conviene decirlo en la leyenda.
    """

    labels: List[str] = Field(default_factory=list)
    wind_mean: List[Optional[float]] = Field(default_factory=list)
    gust_max: List[Optional[float]] = Field(default_factory=list)
    direction: List[Optional[float]] = Field(default_factory=list)
    direction_kind: str = ""
    unit: str = "km/h"


class ClimoHistogramSeries(BaseModel):
    """Histograma de una variable térmica diaria.

    Los intervalos viajan como límites numéricos para que el frontend pueda
    formatearlos con el idioma y las unidades de la persona que consulta.
    ``sample_count`` es también el denominador de los porcentajes.
    """

    bin_start: List[float] = Field(default_factory=list)
    bin_end: List[float] = Field(default_factory=list)
    counts: List[int] = Field(default_factory=list)
    percentages: List[float] = Field(default_factory=list)
    sample_count: int = 0


class ClimoTemperatureDistribution(BaseModel):
    """Distribución de máximas, mínimas y medias diarias del periodo."""

    temp_max: ClimoHistogramSeries = Field(default_factory=ClimoHistogramSeries)
    temp_min: ClimoHistogramSeries = Field(default_factory=ClimoHistogramSeries)
    temp_mean: ClimoHistogramSeries = Field(default_factory=ClimoHistogramSeries)
    expected_days: int = 0
    unit: str = "°C"
    bin_width: float = 2.0


class ClimoDirection(BaseModel):
    """Rumbo cardinal y grados: ``NNE`` · ``15°``."""

    cardinal: str = ""
    degrees: str = ""


class ClimoDetails(BaseModel):
    """Lo que las tarjetas enseñan además del valor de la métrica.

    Sale del dataset diario —direcciones de viento, intensidad de lluvia—,
    que el frontend no recibe: se calcula aquí y viaja ya formateado.
    """

    gust_direction: ClimoDirection = Field(default_factory=ClimoDirection)
    predominant_direction: ClimoDirection = Field(default_factory=ClimoDirection)
    windiest_day_direction: ClimoDirection = Field(default_factory=ClimoDirection)
    windiest_month_direction: ClimoDirection = Field(default_factory=ClimoDirection)
    max_precip_rate: str = ""
    max_precip_rate_date: str = ""
    windiest_month_label: str = ""


class ClimoTable(BaseModel):
    """Tabla de todos los datos, con sus columnas ya etiquetadas."""

    columns: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)


class ClimoSummaryResponse(BaseModel):
    """Todo lo que pinta la pestaña de Histórico, en una sola respuesta."""

    has_data: bool = False
    summary_mode: str = "monthly"
    granularity: str = "monthly"
    general: List[ClimoMetricRow] = Field(default_factory=list)
    extremes: List[ClimoMetricRow] = Field(default_factory=list)
    chart: ClimoChartSeries = Field(default_factory=ClimoChartSeries)
    wind: ClimoWindSeries = Field(default_factory=ClimoWindSeries)
    temperature_distribution: ClimoTemperatureDistribution = Field(
        default_factory=ClimoTemperatureDistribution
    )
    table: ClimoTable = Field(default_factory=ClimoTable)
    units: Dict[str, str] = Field(default_factory=dict)
    details: ClimoDetails = Field(default_factory=ClimoDetails)
    # Cuántos bloques se pidieron y si tocan comparar años entre sí: de eso
    # depende qué tarjetas tienen sentido.
    period_count: int = 0
    annual_comparison: bool = False
    # Cada red publica el sol a su manera: irradiación, irradiancia u horas.
    solar_metric_kind: str = "sunshine_hours"


class FrostPeriodOptionsRequest(BaseModel):
    """Petición de ``POST /v1/climo/frost/period-options``."""

    station_id: str = Field(min_length=1, max_length=64)

    @field_validator("station_id", mode="before")
    @classmethod
    def _normalize_station(cls, value: Any) -> str:
        return str(value or "").strip()


class FrostPeriodOptionsResponse(BaseModel):
    """Periodos de normales disponibles para los climogramas de Frost."""

    monthly: List[str] = Field(default_factory=list)
    annual: List[str] = Field(default_factory=list)
