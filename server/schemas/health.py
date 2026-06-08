"""Esquemas Pydantic del endpoint /health."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Respuesta de ``GET /v1/health``."""

    ok: bool = Field(description="True si el servicio responde correctamente.")
    version: str = Field(description="Versión del paquete server (`server.__version__`).")
    api_version: str = Field(description="Versión de la API (``v1``, ``v2``…).")
