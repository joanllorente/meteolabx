"""
Router de estadísticas internas de uso.

``POST /v1/stats/visit`` lo llama el frontend en cada conexión a una
estación (fire-and-forget). ``GET /v1/stats/stations`` alimenta el panel
interno y exige la contraseña de administración
(``METEOLABX_STATS_ADMIN_PASSWORD``) en el header ``X-Stats-Password``.

El backend no está expuesto públicamente (escucha en 127.0.0.1; solo el
frontend lo alcanza), pero la contraseña se comprueba igualmente: defensa
en profundidad por si algún día se publica la API.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from server.config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stats", tags=["stats"])


class VisitRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    station_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=200)


@router.post("/visit", status_code=204, summary="Registrar una conexión a estación")
def post_visit(body: VisitRequest, settings: Settings = Depends(get_settings)) -> Response:
    from server.services import usage_stats

    try:
        usage_stats.record_visit(body.provider, body.station_id, body.name, settings=settings)
    except Exception:
        # Las estadísticas nunca deben tumbar una conexión: log y a seguir.
        logger.warning("stats: no se pudo registrar la visita", exc_info=True)
    return Response(status_code=204)


@router.get("/stations", summary="Visitas agregadas por estación (panel interno)")
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
