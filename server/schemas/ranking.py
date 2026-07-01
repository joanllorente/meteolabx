"""Esquemas Pydantic del endpoint /ranking."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class RankingEntry(BaseModel):
    rank: int = Field(description="Posición en el ranking (1 = mejor).")
    station_id: str
    name: str
    locality: str = ""
    provider: str
    country: str = Field(default="", description="ISO2 del país de la estación.")
    local_time: str = Field(default="", description="Hora local de la lectura (HH:MM).")
    value: float = Field(description="Valor de la métrica (unidad en el padre).")
    lat: Optional[float] = None
    lon: Optional[float] = None


class RankingResponse(BaseModel):
    """Top-N por las 4 métricas para un conjunto de proveedores (o todos)."""

    providers: List[str] = Field(default_factory=list, description="Proveedores incluidos con datos.")
    updated_at: Optional[str] = Field(
        default=None, description="Marca ISO-8601 (UTC) del último refresco del ranking."
    )
    day: str = Field(default="", description="Fecha local (ISO YYYY-MM-DD) que muestra este ranking.")
    days: List[str] = Field(
        default_factory=list,
        description="Fechas locales disponibles (orden cronológico) para el selector ◀▶.",
    )
    units: Dict[str, str] = Field(description="Unidad por métrica (tmax/tmin/gust/rain).")
    metrics: Dict[str, List[RankingEntry]] = Field(
        description="Listas top-N indexadas por métrica: tmax, tmin, gust, rain."
    )
