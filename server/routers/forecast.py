"""Endpoints de predicción AROME para el visor Svelte."""

from __future__ import annotations

from copy import deepcopy
import gzip
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from server.config import Settings, get_settings
from server.services.arome_forecast import (
    catalog_payload,
    domain_boundaries,
    frame_grid,
    frame_png,
)
from server.services.forecast_store import (
    PERSISTED_FORECAST_PRODUCTS,
    LATEST_MANIFEST_KEY,
    augment_catalog_with_manifest,
    frame_key,
    get_forecast_store,
    grid_metadata,
    read_compressed_grid,
    read_json,
    retained_manifests,
    run_manifest_key,
    write_grid,
)
import re

from tabs.arome_forecast import AromeError


router = APIRouter(prefix="/forecast/arome", tags=["forecast"])
# Se genera del catálogo, no a mano: una lista paralela se queda atrás al
# añadir un mapa y la API responde 422, cuyo detalle es una lista de objetos
# que el visor no sabe enseñar.
FORECAST_PRODUCT_PATTERN = (
    "^(" + "|".join(re.escape(product) for product in PERSISTED_FORECAST_PRODUCTS) + ")$"
)


def _token(settings: Settings) -> str:
    token = str(settings.arome_api_key or "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="La clave de AROME no está configurada en el servidor.",
        )
    return token


def _http_headers(headers: dict[str, str]) -> dict[str, str]:
    """Mantiene las cabeceras HTTP en ASCII; las unidades completas van en la rejilla."""
    return {key: str(value).replace("°", "deg ") for key, value in headers.items()}


def _gzip_headers(headers: dict[str, str], *, immutable: bool) -> dict[str, str]:
    cache_control = "public, max-age=31536000, immutable" if immutable else "public, max-age=900"
    return _http_headers({
        **headers,
        "Content-Encoding": "gzip",
        "Cache-Control": cache_control,
        "Vary": "Accept-Encoding",
    })


@router.get("/progress", summary="Progreso de las pasadas AROME persistidas")
def get_progress() -> dict:
    """Devuelve solo los manifiestos locales, sin consultar Météo-France."""
    store = get_forecast_store()
    latest = read_json(store, LATEST_MANIFEST_KEY)
    runs = retained_manifests(store)
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
                "worker_heartbeat_at": manifest.get("worker_heartbeat_at"),
                "progress": dict(manifest.get("progress") or {}),
            }
            for manifest in runs
        ],
    }


@router.get("/catalog", summary="Catálogo de diagnósticos AROME conectados")
def get_catalog(settings: Settings = Depends(get_settings)) -> dict:
    store = get_forecast_store()
    manifest = read_json(store, LATEST_MANIFEST_KEY)
    try:
        persisted_products = deepcopy((manifest or {}).get("catalog_products") or {})
        if persisted_products:
            # El visor publicado no debe depender de una llamada en vivo a
            # Météo-France: el worker ya dejó en el volumen el catálogo exacto
            # de este RUN junto con sus horas disponibles.
            payload = {
                "model": "AROME France",
                "resolution": "0,025°",
                "domain": {},
                "products": persisted_products,
            }
        else:
            payload = deepcopy(catalog_payload(_token(settings)))
        payload = augment_catalog_with_manifest(
            payload,
            manifest,
            precomputed_only=settings.forecast_precomputed_only,
        )
        runs = []
        for retained in retained_manifests(store):
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
            first_product = next(iter(products.values()), {})
            runs.append({
                "run": retained.get("run"),
                "run_local": first_product.get("run_local"),
                "status": retained.get("status"),
                "products": run_payload["products"],
                "publication": run_payload["publication"],
            })
        if not runs:
            current_runs = sorted(
                {item.get("run") for item in payload.get("products", {}).values() if item.get("run")},
                reverse=True,
            )
            if current_runs:
                current_run = current_runs[0]
                runs.append({
                    "run": current_run,
                    "run_local": next(
                        (item.get("run_local") for item in payload["products"].values() if item.get("run") == current_run),
                        None,
                    ),
                    "status": "direct",
                    "products": payload["products"],
                    "publication": payload["publication"],
                })
        payload["runs"] = runs
        return payload
    except AromeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/boundaries", summary="Contornos del dominio AROME")
def get_boundaries() -> Response:
    """Fronteras compartidas por todos los frames.

    Antes viajaban dentro de cada rejilla: los mismos contornos repetidos en
    miles de ficheros. Se sirven una vez y el visor los reutiliza.
    """
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


@router.get("/frames.png", summary="Frame PNG de cizalladura o SHIP")
def get_frame(
    product: str = Query(pattern=FORECAST_PRODUCT_PATTERN),
    valid_time: str = Query(min_length=10, max_length=40),
    vertical_kind: str = Query(default="height", pattern="^(height|isobaric)$"),
    level: float = Query(default=10.0, ge=10, le=3000),
    run: str = Query(default="", max_length=40),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        arguments = (_token(settings), product, valid_time, vertical_kind, level)
        content, headers = frame_png(*arguments, run_iso=run) if run else frame_png(*arguments)
    except AromeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="image/png",
        headers=_http_headers({**headers, "Cache-Control": "public, max-age=900"}),
    )


@router.get("/frames.grid", summary="Rejilla Float32 interactiva AROME")
def get_grid(
    product: str = Query(pattern=FORECAST_PRODUCT_PATTERN),
    valid_time: str = Query(min_length=10, max_length=40),
    vertical_kind: str = Query(default="height", pattern="^(height|isobaric)$"),
    level: float = Query(default=10.0, ge=10, le=3000),
    run: str = Query(default="", max_length=40),
    settings: Settings = Depends(get_settings),
) -> Response:
    store = get_forecast_store()
    try:
        manifest = read_json(store, run_manifest_key(run)) if run else read_json(store, LATEST_MANIFEST_KEY)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="El RUN no tiene un formato ISO 8601 válido.") from exc
    stored_run = run or (str(manifest.get("run")) if manifest else "")
    scope = str(manifest.get("calculation_scope", "model")) if manifest else "model"
    if stored_run and product in PERSISTED_FORECAST_PRODUCTS:
        content = read_compressed_grid(
            store,
            frame_key(
                stored_run,
                product,
                valid_time,
                scope=scope,
                vertical_kind=vertical_kind,
                level=level,
            ),
        )
        if content is not None:
            headers = {
                "X-AROME-Run": stored_run,
                "X-AROME-Valid-Time": valid_time,
                "X-MeteoLabX-Precomputed": "1",
            }
            return Response(
                content=content,
                media_type="application/vnd.meteolabx.arome-grid",
                headers=_gzip_headers(headers, immutable=True),
            )
    if settings.forecast_precomputed_only and product in PERSISTED_FORECAST_PRODUCTS and product != "wind-level":
        raise HTTPException(
            status_code=425,
            detail="La hora está publicada por AROME pero el mapa persistente aún se está preparando.",
        )
    try:
        arguments = (_token(settings), product, valid_time, vertical_kind, level)
        content, headers = frame_grid(*arguments, run_iso=run) if run else frame_grid(*arguments)
    except AromeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if product in PERSISTED_FORECAST_PRODUCTS:
        metadata = grid_metadata(content)
        if metadata.get("run"):
            actual_run = str(metadata["run"])
            actual_scope = str(metadata.get("calculation_scope", scope))
            write_grid(
                store,
                frame_key(
                    actual_run,
                    product,
                    valid_time,
                    scope=actual_scope,
                    vertical_kind=vertical_kind,
                    level=level,
                ),
                content,
            )
    return Response(
        content=gzip.compress(content, compresslevel=5),
        media_type="application/vnd.meteolabx.arome-grid",
        headers=_gzip_headers(headers, immutable=False),
    )
