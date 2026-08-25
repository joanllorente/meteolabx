"""Endpoints de predicción AROME para el visor Svelte."""

from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from server.config import Settings, get_settings
from server.services.arome_forecast import catalog_payload, frame_grid, frame_png
from server.services.forecast_store import (
    PERSISTED_FORECAST_PRODUCTS,
    LATEST_MANIFEST_KEY,
    augment_catalog_with_manifest,
    frame_key,
    get_forecast_store,
    grid_metadata,
    read_grid,
    read_json,
    retained_manifests,
    run_manifest_key,
    write_grid,
)
from tabs.arome_forecast import AromeError


router = APIRouter(prefix="/forecast/arome", tags=["forecast"])
FORECAST_PRODUCT_PATTERN = "^(temperature-2m|temperature-850|temperature-500|wind-level|wind-gust|shear-01|shear-03|shear-06|ebwd|precip-1h|accumulated-precip|relative-humidity-700|shortwave-down|cloud-cover|ship|mucape-muli|mlcape-mlli|sbcape-sbli|dcape|ordinary-cell-motion|mu-ecape|ml-ecape)$"


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
    try:
        payload = deepcopy(catalog_payload(_token(settings)))
        store = get_forecast_store()
        manifest = read_json(store, LATEST_MANIFEST_KEY)
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
        content = read_grid(
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
            metadata = grid_metadata(content)
            headers = {
                "X-AROME-Run": str(metadata["run"]),
                "X-AROME-Valid-Time": str(metadata["valid_time"]),
                "X-AROME-Max": f"{float(metadata['maximum']):.3f}",
                "X-AROME-Unit": str(metadata["unit"]),
                "X-MeteoLabX-Precomputed": "1",
            }
            return Response(
                content=content,
                media_type="application/vnd.meteolabx.arome-grid",
                    headers=_http_headers({**headers, "Cache-Control": "public, max-age=31536000, immutable"}),
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
        content=content,
        media_type="application/vnd.meteolabx.arome-grid",
        headers=_http_headers({**headers, "Cache-Control": "public, max-age=900"}),
    )
