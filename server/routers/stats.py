"""
Router de estadísticas internas de uso.

``POST /v1/stats/visit`` lo llama el frontend en cada conexión a una estación.
``POST /v1/stats/seo-view`` registra la apertura del HTML de una ficha SEO y
``POST /v1/stats/panel-click`` que esa ficha ha abierto el panel completo.
``POST /v1/stats/error`` registra fallos y ``POST /v1/stats/section`` las
transiciones reales de navegación (todos fire-and-forget). ``GET
/v1/stats/stations`` alimenta el panel interno y ``GET /v1/stats/station`` el
detalle de una sola (visitas y errores recientes, con fecha y tipo); ambos
exigen la contraseña de administración (``METEOLABX_STATS_ADMIN_PASSWORD``) en
el header ``X-Stats-Password``.

Ninguno de los cinco POST registra nada cuando quien llama se declara
rastreador: son estadísticas de uso, y el paso de un bot no es uso.

El backend no está expuesto públicamente (escucha en 127.0.0.1; solo el
frontend lo alcanza), pero la contraseña se comprueba igualmente: defensa
en profundidad por si algún día se publica la API.
"""

from __future__ import annotations

import hmac
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from server.config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stats", tags=["stats"])


def _is_crawler(request: Request) -> bool:
    """El paso de un rastreador no es uso: no se registra en ninguna tabla.

    Googlebot renderiza la ficha, así que ejecutaba estos mismos avisos y se
    mezclaba con las visitas de personas. Se descarta al entrar, antes de
    tocar la base de datos, para que ni engorde ni desvíe los recuentos.
    """
    from server.services import usage_stats

    return usage_stats.is_crawler(request.headers.get("user-agent", ""))


class VisitRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    station_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=200)
    source: Literal["app", "seo"] = "app"
    language: str = Field(default="", max_length=8)
    browser_languages: str = Field(default="", max_length=200)
    page_request_languages: str = Field(default="", max_length=200)
    language_reason: Literal["", "saved", "browser", "url", "fallback"] = ""
    url_language: str = Field(default="", max_length=8)
    # De dónde llegó. Lo decide el navegador, que es quien ve el referente.
    entry: Literal["", "search", "external", "internal", "direct"] = ""
    referrer_domain: str = Field(default="", max_length=120)
    device: Literal["", "mobile", "tablet", "desktop"] = ""


@router.post("/visit", status_code=204, summary="Registrar una conexión a estación")
def post_visit(body: VisitRequest, request: Request, settings: Settings = Depends(get_settings)) -> Response:
    from server.services import usage_stats

    if _is_crawler(request):
        return Response(status_code=204)
    try:
        usage_stats.record_visit(
            body.provider, body.station_id, body.name,
            source=body.source, language=body.language,
            entry=body.entry, referrer_domain=body.referrer_domain,
            device=body.device,
            browser_languages=body.browser_languages,
            page_request_languages=body.page_request_languages,
            language_reason=body.language_reason,
            request_client=usage_stats.request_client(request.headers.get("user-agent", "")),
            url_language=body.url_language,
            request_languages=request.headers.get("accept-language", ""),
            saved_language=request.cookies.get("meteolabx_language", ""),
            settings=settings,
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


class PwaEventRequest(BaseModel):
    event: str = Field(min_length=1, max_length=30)
    os: str = Field(default="", max_length=20)
    device: str = Field(default="", max_length=20)
    browser: str = Field(default="", max_length=20)
    method: str = Field(default="", max_length=30)


class ForecastMapViewRequest(BaseModel):
    model: str = Field(min_length=1, max_length=20)
    product: str = Field(min_length=1, max_length=60)
    label: str = Field(default="", max_length=200)
    category: str = Field(default="", max_length=60)


class SectionVisitRequest(BaseModel):
    section: Literal[
        "observation",
        "trends",
        "historical",
        "map.stations",
        "map.temperature",
        "map.wind",
        "map.precipitation",
        "forecast.app",
        # Ya no se emite (la app de Streamlit está fuera de uso); se acepta
        # para que un enlace guardado de entonces no dé 422.
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
    body: ConnectionErrorRequest, request: Request, settings: Settings = Depends(get_settings)
) -> Response:
    from server.services import usage_stats

    if _is_crawler(request):
        return Response(status_code=204)
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
    body: SectionVisitRequest, request: Request, settings: Settings = Depends(get_settings)
) -> Response:
    from server.services import usage_stats

    if _is_crawler(request):
        return Response(status_code=204)
    try:
        usage_stats.record_section_visit(body.section, settings=settings)
    except Exception:
        logger.warning("stats: no se pudo registrar la sección", exc_info=True)
    return Response(status_code=204)


@router.post("/pwa", status_code=204, summary="Registrar un evento de instalación de la app")
def post_pwa_event(
    body: PwaEventRequest, request: Request, settings: Settings = Depends(get_settings)
) -> Response:
    from server.services import usage_stats

    if _is_crawler(request):
        return Response(status_code=204)
    try:
        usage_stats.record_pwa_event(
            body.event,
            os=body.os,
            device=body.device,
            browser=body.browser,
            method=body.method,
            settings=settings,
        )
    except Exception:
        logger.warning("stats: no se pudo registrar el evento de instalación", exc_info=True)
    return Response(status_code=204)


@router.post("/forecast-map", status_code=204, summary="Registrar la apertura de un mapa de predicción")
def post_forecast_map_view(
    body: ForecastMapViewRequest, request: Request, settings: Settings = Depends(get_settings)
) -> Response:
    from server.services import usage_stats

    if _is_crawler(request):
        return Response(status_code=204)
    try:
        usage_stats.record_forecast_map_view(
            body.model,
            body.product,
            label=body.label,
            category=body.category,
            settings=settings,
        )
    except Exception:
        logger.warning("stats: no se pudo registrar el mapa de predicción", exc_info=True)
    return Response(status_code=204)


@router.post("/panel-click", status_code=204, summary="Registrar apertura del panel desde una ficha SEO")
def post_seo_panel_click(
    body: SeoPanelClickRequest, request: Request, settings: Settings = Depends(get_settings)
) -> Response:
    from server.services import usage_stats

    if _is_crawler(request):
        return Response(status_code=204)
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
    body: SeoPageViewRequest, request: Request, settings: Settings = Depends(get_settings)
) -> Response:
    from server.services import usage_stats

    if _is_crawler(request):
        return Response(status_code=204)
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


def _check_password(settings: Settings, given: str) -> None:
    expected = str(getattr(settings, "stats_admin_password", "") or "")
    if not expected:
        raise HTTPException(status_code=404, detail="stats disabled")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="bad password")


@router.get("/stations", summary="Visitas y errores agregados por estación (panel interno)")
def get_station_stats(
    settings: Settings = Depends(get_settings),
    x_stats_password: str = Header(default=""),
) -> dict:
    from server.services import usage_stats

    _check_password(settings, x_stats_password)
    return usage_stats.visit_summary(settings=settings)


@router.get("/pwa", summary="Instalaciones de la app por aparato (panel interno)")
def get_pwa_stats(
    settings: Settings = Depends(get_settings),
    x_stats_password: str = Header(default=""),
) -> dict:
    from server.services import usage_stats

    _check_password(settings, x_stats_password)
    return usage_stats.pwa_summary(settings=settings)


@router.get("/forecast-maps", summary="Mapas de predicción más vistos (panel interno)")
def get_forecast_map_stats(
    settings: Settings = Depends(get_settings),
    x_stats_password: str = Header(default=""),
) -> dict:
    from server.services import usage_stats

    _check_password(settings, x_stats_password)
    return usage_stats.forecast_map_summary(settings=settings)


@router.get("/quarantine", summary="Estaciones con variables en cuarentena (panel interno)")
def get_quarantine(
    request: Request,
    settings: Settings = Depends(get_settings),
    x_stats_password: str = Header(default=""),
) -> dict:
    """Qué hay en cuarentena hoy y cuánto lleva cada cosa.

    Sirve para distinguir el sensor que falló una tarde del que lleva semanas
    roto: ``days_total`` cuenta los días locales en que esa variable estuvo
    marcada, y ``first_seen`` dice desde cuándo.
    """
    from datetime import datetime, timezone

    from server.services import suspect_data

    _check_password(settings, x_stats_password)
    store = getattr(request.app.state, "ranking_store", None)
    # El día que enseña el panel es el del ranking; sin store, el día UTC.
    hoy = datetime.now(tz=timezone.utc).date().isoformat()
    dias = {hoy}
    if store is not None:
        dias.update({clave[1] for clave in getattr(store, "_daily", {})})
    # Se consultan dos días para cubrir simultáneamente todos los husos, pero
    # una variable puede seguir marcada al cruzar medianoche y aparecer en
    # ambos. En el panel es un solo sensor en cuarentena: gana el día más
    # reciente (los días ya están recorridos en orden descendente).
    activas_unicas = {}
    for dia in sorted(dias, reverse=True)[:2]:
        for fila in suspect_data.active(dia):
            clave = (fila["provider"], fila["station_id"], fila["variable"])
            activas_unicas.setdefault(clave, fila)
    activas = list(activas_unicas.values())
    historial = suspect_data.history()
    # El registro guarda identidades, no nombres: sin esto la tabla es una
    # lista de identificadores y hay que ir a buscar a mano de qué estación
    # habla cada fila.
    _stamp_station_names(activas + historial)
    return {"day": hoy, "active": activas, "history": historial}


def _stamp_station_names(filas: list) -> None:
    """Añade el nombre del catálogo a cada fila, si lo hay."""
    from server.services import stations as stations_svc

    cache: dict = {}
    for fila in filas:
        clave = (fila.get("provider"), fila.get("station_id"))
        if clave not in cache:
            try:
                registro = stations_svc.get_station(*clave) or {}
            except Exception:  # noqa: BLE001 — el panel no cae por el catálogo
                registro = {}
            cache[clave] = str(registro.get("name") or "").strip()
        fila["name"] = cache[clave] or None


@router.get("/station", summary="Detalle de una estación (panel interno)")
def get_station_detail(
    provider: str,
    station_id: str,
    settings: Settings = Depends(get_settings),
    x_stats_password: str = Header(default=""),
) -> dict:
    """Visitas y errores recientes de una estación, con fecha y tipo.

    El resumen dice cuántos errores hay; esto dice cuándo y de qué clase.
    """
    from server.services import usage_stats

    _check_password(settings, x_stats_password)
    detail = usage_stats.station_detail(provider, station_id, settings=settings)
    if detail is None:
        raise HTTPException(status_code=404, detail="unknown station")
    return detail
