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
