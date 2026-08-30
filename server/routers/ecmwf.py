"""Endpoints del modelo ECMWF IFS 0,25° para el visor Svelte.

Misma forma que los de AROME —catálogo, fronteras y rejilla— para que el visor
solo tenga que cambiar de modelo en la ruta. Lo que cambia por debajo es el
namespace del almacén: los frames y el manifiesto de ECMWF viven bajo
`forecast/models/ecmwf/`, así que ninguna pasada puede pisar a la otra.
"""

from __future__ import annotations

from copy import deepcopy
import gzip
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from server.config import Settings, get_settings
from server.services.ecmwf_forecast import (
    FORECAST_MODEL,
    MODEL_LABEL,
    PRODUCTS,
    RESOLUTION_LABEL,
    EcmwfError,
    catalog_payload,
    domain_boundaries,
    frame_payload,
    latest_run,
    parse_run,
    step_of,
)
from server.services.forecast_store import (
    augment_catalog_with_manifest,
    frame_key,
    get_forecast_store,
    latest_manifest_key,
    read_compressed_grid,
    read_json,
    retained_manifests,
    run_manifest_key,
    write_grid,
)


router = APIRouter(prefix="/forecast/ecmwf", tags=["forecast"])
PRODUCT_PATTERN = "^(" + "|".join(re.escape(product) for product in PRODUCTS) + ")$"


def _http_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key: str(value).replace("°", "deg ") for key, value in headers.items()}


def _gzip_headers(headers: dict[str, str], *, immutable: bool) -> dict[str, str]:
    cache_control = (
        "public, max-age=31536000, immutable" if immutable else "public, max-age=900"
    )
    return _http_headers({
        **headers,
        "Content-Encoding": "gzip",
        "Cache-Control": cache_control,
        "Vary": "Accept-Encoding",
    })


@router.get("/progress", summary="Progreso de las pasadas ECMWF persistidas")
def get_progress() -> dict:
    store = get_forecast_store()
    latest = read_json(store, latest_manifest_key(FORECAST_MODEL))
    return {
        "run": latest.get("run") if latest else None,
        "status": latest.get("status") if latest else "idle",
        "worker_heartbeat_at": latest.get("worker_heartbeat_at") if latest else None,
        "progress": dict(latest.get("progress") or {}) if latest else None,
        "runs": [
            {
                "run": manifest.get("run"),
                "status": manifest.get("status"),
                "updated_at": manifest.get("updated_at"),
                "progress": dict(manifest.get("progress") or {}),
            }
            for manifest in retained_manifests(store, model=FORECAST_MODEL)
        ],
    }


@router.get("/catalog", summary="Catálogo de mapas ECMWF conectados")
def get_catalog(settings: Settings = Depends(get_settings)) -> dict:
    store = get_forecast_store()
    manifest = read_json(store, latest_manifest_key(FORECAST_MODEL))
    try:
        persisted = deepcopy((manifest or {}).get("catalog_products") or {})
        if persisted:
            # Igual que en AROME: si el worker ya dejó el catálogo del RUN en el
            # volumen, el visor no depende de que ECMWF conteste ahora mismo.
            payload = {
                "model": MODEL_LABEL,
                "resolution": RESOLUTION_LABEL,
                "domain": {},
                "products": persisted,
            }
        else:
            payload = deepcopy(catalog_payload())
        payload = augment_catalog_with_manifest(
            payload, manifest, precomputed_only=settings.forecast_precomputed_only
        )
        runs = []
        for retained in retained_manifests(store, model=FORECAST_MODEL):
            products = deepcopy(retained.get("catalog_products") or {})
            if not products:
                continue
            run_payload = augment_catalog_with_manifest(
                {
                    "model": payload.get("model"),
                    "resolution": payload.get("resolution"),
                    "domain": deepcopy(payload.get("domain") or {}),
                    "products": products,
                },
                retained,
                precomputed_only=settings.forecast_precomputed_only,
            )
            runs.append({
                "run": retained.get("run"),
                "status": retained.get("status"),
                "products": run_payload["products"],
                "publication": run_payload["publication"],
            })
        if not runs:
            current = sorted(
                {
                    item.get("run")
                    for item in payload.get("products", {}).values()
                    if item.get("run")
                },
                reverse=True,
            )
            if current:
                runs.append({
                    "run": current[0],
                    "status": "direct",
                    "products": payload["products"],
                    "publication": payload["publication"],
                })
        payload["runs"] = runs
        return payload
    except EcmwfError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/boundaries", summary="Contornos del dominio ECMWF")
def get_boundaries() -> Response:
    payload = json.dumps(
        {"boundaries": domain_boundaries()}, separators=(",", ":")
    ).encode("utf-8")
    return Response(
        content=gzip.compress(payload, compresslevel=6),
        media_type="application/json",
        headers=_http_headers({
            "Content-Encoding": "gzip",
            "Cache-Control": "public, max-age=86400",
            "Vary": "Accept-Encoding",
        }),
    )


@router.get("/frames.grid", summary="Rejilla Float32 interactiva ECMWF")
def get_grid(
    product: str = Query(pattern=PRODUCT_PATTERN),
    valid_time: str = Query(min_length=10, max_length=40),
    run: str = Query(default="", max_length=40),
    settings: Settings = Depends(get_settings),
) -> Response:
    store = get_forecast_store()
    try:
        manifest = (
            read_json(store, run_manifest_key(run, model=FORECAST_MODEL))
            if run
            else read_json(store, latest_manifest_key(FORECAST_MODEL))
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="El RUN no tiene un formato ISO 8601 válido."
        ) from exc
    stored_run = run or (str(manifest.get("run")) if manifest else "")
    if stored_run:
        content = read_compressed_grid(
            store, frame_key(stored_run, product, valid_time, model=FORECAST_MODEL)
        )
        if content is not None:
            return Response(
                content=content,
                media_type="application/vnd.meteolabx.arome-grid",
                headers=_gzip_headers(
                    {
                        "X-MeteoLabX-Model": FORECAST_MODEL,
                        "X-MeteoLabX-Run": stored_run,
                        "X-MeteoLabX-Valid-Time": valid_time,
                        "X-MeteoLabX-Precomputed": "1",
                    },
                    immutable=True,
                ),
            )
    if settings.forecast_precomputed_only:
        raise HTTPException(
            status_code=425,
            detail="La hora está publicada por ECMWF pero el mapa aún se está preparando.",
        )
    # Sin worker todavía, un frame se calcula al vuelo: son dos peticiones
    # parciales y unos segundos, no los minutos de un diagnóstico convectivo.
    try:
        pasada = parse_run(stored_run) if stored_run else latest_run()
        content, headers = frame_payload(product, pasada, step_of(pasada, valid_time))
    except EcmwfError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    write_grid(
        store,
        frame_key(
            pasada.isoformat().replace("+00:00", "Z"),
            product,
            valid_time,
            model=FORECAST_MODEL,
        ),
        content,
    )
    return Response(
        content=gzip.compress(content, compresslevel=5),
        media_type="application/vnd.meteolabx.arome-grid",
        headers=_gzip_headers(headers, immutable=False),
    )
