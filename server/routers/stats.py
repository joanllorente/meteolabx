"""
Router de estadísticas internas de uso.

``POST /v1/stats/visit`` lo llama el frontend en cada conexión a una estación.
``POST /v1/stats/seo-view`` registra la apertura del HTML de una ficha SEO y
``POST /v1/stats/panel-click`` que esa ficha ha abierto el panel completo.
``POST /v1/stats/error`` registra fallos y ``POST /v1/stats/section`` las
transiciones reales de navegación (todos fire-and-forget). ``GET
/v1/stats/stations`` alimenta el panel interno y exige la contraseña de administración
(``METEOLABX_STATS_ADMIN_PASSWORD``) en el header ``X-Stats-Password``.

El backend no está expuesto públicamente (escucha en 127.0.0.1; solo el
frontend lo alcanza), pero la contraseña se comprueba igualmente: defensa
en profundidad por si algún día se publica la API.
"""

from __future__ import annotations

import hmac
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from server.config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stats", tags=["stats"])


class VisitRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    station_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=200)
    source: Literal["app", "seo"] = "app"


@router.post("/visit", status_code=204, summary="Registrar una conexión a estación")
def post_visit(body: VisitRequest, settings: Settings = Depends(get_settings)) -> Response:
    from server.services import usage_stats

    try:
        usage_stats.record_visit(
            body.provider, body.station_id, body.name,
            source=body.source, settings=settings,
        )
    except Exception:
        # Las estadísticas nunca deben tumbar una conexión: log y a seguir.
        logger.warning("stats: no se pudo registrar la visita", exc_info=True)
    return Response(status_code=204)


class ConnectionErrorRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    station_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=200)
    error_kind: str = Field(min_length=1, max_length=40)
    status_code: Optional[int] = Field(default=None, ge=100, le=599)


class SectionVisitRequest(BaseModel):
    section: Literal[
        "observation",
        "trends",
        "historical",
        "map.stations",
        "map.temperature",
        "map.wind",
        "map.precipitation",
        "forecast.streamlit",
        "forecast.direct",
        "ranking",
    ]


class SeoPanelClickRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    station_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=200)
    language: str = Field(default="", max_length=8)


class SeoPageViewRequest(SeoPanelClickRequest):
    pass


@router.post("/error", status_code=204, summary="Registrar un error de conexión a estación")
def post_connection_error(
    body: ConnectionErrorRequest, settings: Settings = Depends(get_settings)
) -> Response:
    from server.services import usage_stats

    try:
        usage_stats.record_error(
            body.provider,
            body.station_id,
            body.name,
            error_kind=body.error_kind,
            status_code=body.status_code,
            settings=settings,
        )
    except Exception:
        logger.warning("stats: no se pudo registrar el error de conexión", exc_info=True)
    return Response(status_code=204)


@router.post("/section", status_code=204, summary="Registrar entrada a una pestaña o mapa")
def post_section_visit(
    body: SectionVisitRequest, settings: Settings = Depends(get_settings)
) -> Response:
    from server.services import usage_stats

    try:
        usage_stats.record_section_visit(body.section, settings=settings)
    except Exception:
        logger.warning("stats: no se pudo registrar la sección", exc_info=True)
    return Response(status_code=204)


@router.post("/panel-click", status_code=204, summary="Registrar apertura del panel desde una ficha SEO")
def post_seo_panel_click(
    body: SeoPanelClickRequest, settings: Settings = Depends(get_settings)
) -> Response:
    from server.services import usage_stats

    try:
        usage_stats.record_seo_panel_click(
            body.provider,
            body.station_id,
            body.name,
            language=body.language,
            settings=settings,
        )
    except Exception:
        logger.warning("stats: no se pudo registrar el clic SEO", exc_info=True)
    return Response(status_code=204)


@router.post("/seo-view", status_code=204, summary="Registrar apertura de una ficha SEO")
def post_seo_page_view(
    body: SeoPageViewRequest, settings: Settings = Depends(get_settings)
) -> Response:
    from server.services import usage_stats

    try:
        usage_stats.record_seo_page_view(
            body.provider,
            body.station_id,
            body.name,
            language=body.language,
            settings=settings,
        )
    except Exception:
        logger.warning("stats: no se pudo registrar la apertura SEO", exc_info=True)
    return Response(status_code=204)


@router.get("/stations", summary="Visitas y errores agregados por estación (panel interno)")
def get_station_stats(
    settings: Settings = Depends(get_settings),
    x_stats_password: str = Header(default=""),
) -> dict:
    from server.services import usage_stats

    expected = str(getattr(settings, "stats_admin_password", "") or "")
    if not expected:
        raise HTTPException(status_code=404, detail="stats disabled")
    if not hmac.compare_digest(x_stats_password.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="bad password")
    return usage_stats.visit_summary(settings=settings)
