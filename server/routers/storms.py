"""Ciclones tropicales activos, con su trayectoria prevista, para el visor."""

from __future__ import annotations

from fastapi import APIRouter, Response
import json

from server.services.tropical_cyclones import active_storms


router = APIRouter(prefix="/forecast/storms", tags=["forecast"])


@router.get("", summary="Ciclones tropicales activos del NHC")
def get_storms() -> Response:
    return Response(
        content=json.dumps(active_storms(), separators=(",", ":")),
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=900"},
    )
