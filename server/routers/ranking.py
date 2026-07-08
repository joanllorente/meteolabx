"""
Router del ranking diario de estaciones.

Sirve el top-N por Tmáx/Tmín/ráfaga/lluvia del día local, leyendo del
``RankingStore`` en memoria que rellena el job del lifespan. No hace HTTP
a proveedores en la request (eso lo hace el job) → respuestas baratas.

Ámbitos:
- ``providers=AEMET,METEOCAT,METEOGALICIA`` → ranking de un país (sus
  proveedores). Cualquier estación de esos proveedores entra.
- sin ``providers`` → combinado global de todas las estaciones disponibles.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from fastapi import APIRouter, Query, Request

from server.schemas.ranking import RankingEntry, RankingResponse
from server.services import ranking as ranking_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ranking", tags=["ranking"])

# Unidad de cada métrica (homogénea entre proveedores).
_UNITS = {"tmax": "°C", "tmin": "°C", "gust": "km/h", "rain": "mm"}


@router.get(
    "/countries",
    summary="Países (ISO2) con datos de ranking hoy",
    response_model=Dict[str, list],
)
async def get_ranking_countries(request: Request) -> Dict[str, list]:
    store: Optional[ranking_svc.RankingStore] = getattr(request.app.state, "ranking_store", None)
    return {"countries": store.countries() if store is not None else []}


@router.get("", response_model=RankingResponse, summary="Ranking diario top-N")
async def get_ranking(
    request: Request,
    providers: Optional[str] = Query(
        default=None,
        description="Lista de proveedores separada por comas (filtro de país). Sin valor → global.",
    ),
    country: Optional[str] = Query(
        default=None,
        description=(
            "ISO2 del país a rankear (p. ej. ``DE``). Filtra por país de la "
            "estación; incluye IEM (multi-país) junto a los proveedores "
            "nacionales. Sin valor → no filtra por país."
        ),
    ),
    day: Optional[str] = Query(
        default=None,
        description=(
            "Fecha local (ISO ``YYYY-MM-DD``) a mostrar. Sin valor → la fecha "
            "principal (la que más estaciones tienen en curso). El ranking nunca "
            "mezcla husos: una sola fecha por lista."
        ),
    ),
    exclude: Optional[str] = Query(
        default=None,
        description="ISO2 a EXCLUIR, separados por comas (p.ej. ``AQ`` para quitar la Antártida).",
    ),
    order: Optional[str] = Query(
        default=None,
        description=(
            "Sentido del orden por métrica: pares ``metrica:asc|desc`` separados "
            "por comas (p.ej. ``tmax:asc,tmin:desc``). Solo altera las métricas "
            "listadas; el resto usa su orden natural (Tmáx desc, Tmín asc). "
            "Permite ver el otro extremo: la Tmáx más baja o la Tmín más alta."
        ),
    ),
    limit: int = Query(default=10, ge=1, le=50),
) -> RankingResponse:
    store: Optional[ranking_svc.RankingStore] = getattr(request.app.state, "ranking_store", None)
    prov_filter = [p.strip().upper() for p in (providers or "").split(",") if p.strip()] or None
    country_filter = (country or "").strip().upper() or None
    exclude_set = {c.strip().upper() for c in (exclude or "").split(",") if c.strip()} or None
    requested_day = (day or "").strip() or None
    # {metrica: descendente?} para las métricas cuyo orden se fuerza.
    order_map: Dict[str, bool] = {}
    for part in (order or "").split(","):
        metric_name, _, direction = part.strip().partition(":")
        metric_name = metric_name.strip().lower()
        direction = direction.strip().lower()
        if metric_name in ranking_svc.METRICS and direction in ("asc", "desc"):
            order_map[metric_name] = direction == "desc"

    if store is None:
        return RankingResponse(providers=[], units=_UNITS, metrics={m: [] for m in ranking_svc.METRICS})

    # Fechas disponibles + principal; la fecha objetivo es UNA sola (no mezcla).
    days_available, main_day = store.day_options(providers=prov_filter, country=country_filter)
    target_day = requested_day if (requested_day and requested_day in days_available) else main_day

    metrics_out = {}
    seen_providers: set = set()
    for metric in ranking_svc.METRICS:
        rows = store.top(
            metric,
            providers=prov_filter,
            country=country_filter,
            day=target_day,
            exclude_countries=exclude_set,
            limit=limit,
            descending=order_map.get(metric),
        )
        metrics_out[metric] = [
            RankingEntry(
                rank=i + 1,
                station_id=r.station_id,
                name=r.name,
                locality=r.locality,
                provider=r.provider,
                country=r.country,
                local_time=r.local_time,
                value=round(float(r.value(metric)), 1),
                lat=r.lat,
                lon=r.lon,
            )
            for i, r in enumerate(rows)
        ]
        seen_providers.update(r.provider for r in rows)

    if country_filter:
        # Con filtro de país, los proveedores presentes salen de los resultados.
        included = sorted(seen_providers)
    else:
        with_data = set(store.providers())
        included = (
            [p for p in prov_filter if p in with_data] if prov_filter else sorted(with_data)
        )
    return RankingResponse(
        providers=included,
        updated_at=store.updated_at.isoformat() if store.updated_at else None,
        day=target_day or "",
        days=days_available,
        units=_UNITS,
        metrics=metrics_out,
    )
