#!/usr/bin/env python3
"""Worker incremental: precalcula cada nueva hora AROME y la publica."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import os
import signal
import time
from typing import Any

from server.config import get_settings
from server.services.arome_forecast import catalog_payload, frame_grid
from server.services.forecast_store import (
    PERSISTED_FORECAST_PRODUCTS,
    LATEST_MANIFEST_KEY,
    delete_run,
    frame_key,
    get_forecast_store,
    mark_available,
    mark_error,
    new_manifest,
    read_json,
    register_run_slot,
    run_manifest_key,
    write_grid,
    write_json,
)
from tabs.arome_forecast import forecast_calculation_scope


logger = logging.getLogger("meteolabx.forecast_worker")


def _iso_sort_key(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _latest_persisted_run(catalog: dict[str, Any]) -> str:
    runs = [
        item["run"]
        for product in PERSISTED_FORECAST_PRODUCTS
        if (item := catalog.get("products", {}).get(product)) and item.get("run")
    ]
    if not runs:
        raise RuntimeError("El catálogo no contiene productos AROME persistibles.")
    return max(runs, key=_iso_sort_key)


def _product_times(catalog: dict[str, Any], run_iso: str, product: str) -> list[str]:
    item = catalog.get("products", {}).get(product) or {}
    if item.get("run") != run_iso:
        return []
    return sorted(set(item.get("valid_times", ())), key=_iso_sort_key)


def pending_hours(
    catalog: dict[str, Any], manifest: dict[str, Any], run_iso: str
) -> list[str]:
    pending: set[str] = set()
    for product in PERSISTED_FORECAST_PRODUCTS:
        available = set(
            manifest.get("products", {}).get(product, {}).get("available_times", ())
        )
        pending.update(set(_product_times(catalog, run_iso, product)) - available)
    return sorted(pending, key=_iso_sort_key)


def _persist_manifest(store, manifest: dict[str, Any]) -> None:
    write_json(store, run_manifest_key(str(manifest["run"])), manifest)
    write_json(store, LATEST_MANIFEST_KEY, manifest)


def _publish_run_slot(store, manifest: dict[str, Any]) -> None:
    previous_run = register_run_slot(store, manifest)
    if not previous_run:
        return
    previous_manifest = read_json(store, run_manifest_key(previous_run)) or {}
    delete_run(
        store,
        previous_run,
        scope=str(previous_manifest.get("calculation_scope", "model")),
    )
    logger.info("RUN %s sustituido en el turno %sZ", previous_run, previous_run[11:13])


def run_incremental_cycle(*, max_hours: int = 0) -> dict[str, Any]:
    settings = get_settings()
    token = str(settings.arome_api_key or "").strip()
    if not token:
        raise RuntimeError("METEOLABX_AROME_API_KEY no está configurada.")

    store = get_forecast_store()
    catalog = catalog_payload(token)
    calculation_scope = forecast_calculation_scope()
    run_iso = _latest_persisted_run(catalog)
    catalog_products = {
        product: dict(item)
        for product in PERSISTED_FORECAST_PRODUCTS
        if (item := catalog.get("products", {}).get(product))
        and item.get("run") == run_iso
    }
    expected = sorted(
        {
            valid
            for product in PERSISTED_FORECAST_PRODUCTS
            for valid in _product_times(catalog, run_iso, product)
        },
        key=_iso_sort_key,
    )
    manifest = read_json(store, run_manifest_key(run_iso))
    if (
        not manifest
        or manifest.get("run") != run_iso
        or manifest.get("calculation_scope", "model") != calculation_scope
    ):
        manifest = new_manifest(
            run_iso,
            expected,
            scope=calculation_scope,
            catalog_products=catalog_products,
        )
    else:
        manifest["expected_times"] = expected
        manifest["catalog_products"] = catalog_products
        manifest["status"] = "publishing"
    _persist_manifest(store, manifest)
    _publish_run_slot(store, manifest)

    hours = pending_hours(catalog, manifest, run_iso)
    if max_hours > 0:
        hours = hours[:max_hours]
    completed = 0
    failures = 0
    logger.info("RUN %s: %d horas pendientes", run_iso, len(hours))

    for valid_iso in hours:
        logger.info("Procesando %s", valid_iso)
        for product in PERSISTED_FORECAST_PRODUCTS:
            if valid_iso not in _product_times(catalog, run_iso, product):
                continue
            state = manifest["products"][product]
            if valid_iso in state.get("available_times", ()):
                continue
            key = frame_key(run_iso, product, valid_iso, scope=calculation_scope)
            try:
                if not store.exists(key):
                    content, _headers = frame_grid(
                        token,
                        product,
                        valid_iso,
                        run_iso=run_iso,
                    )
                    write_grid(store, key, content)
                mark_available(manifest, product, valid_iso)
                completed += 1
            except Exception as exc:  # el siguiente ciclo reintenta solo este frame
                failures += 1
                mark_error(manifest, product, valid_iso, str(exc))
                logger.exception("No se pudo calcular %s %s", product, valid_iso)
            finally:
                _persist_manifest(store, manifest)

    all_complete = True
    for product in PERSISTED_FORECAST_PRODUCTS:
        expected_product = set(_product_times(catalog, run_iso, product))
        available_product = set(
            manifest["products"][product].get("available_times", ())
        )
        if not expected_product.issubset(available_product):
            all_complete = False
            break
    run_time = _iso_sort_key(run_iso)
    final_horizon = max(
        (int((_iso_sort_key(value) - run_time).total_seconds() // 3600) for value in expected),
        default=-1,
    )
    # AROME 0,025° publica H+00..H+51. Haber calculado todo lo que aparece en
    # un catálogo parcial no significa que la pasada haya terminado.
    manifest["status"] = "complete" if all_complete and final_horizon >= 51 else "publishing"
    _persist_manifest(store, manifest)
    return {
        "run": run_iso,
        "hours_seen": len(hours),
        "frames_completed": completed,
        "failures": failures,
        "status": manifest["status"],
        "calculation_scope": calculation_scope,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-hours",
        type=int,
        default=int(os.getenv("METEOLABX_FORECAST_WORKER_MAX_HOURS", "0")),
        help="Limita horas nuevas por ejecución; 0 procesa todo lo publicado.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        default=os.getenv("METEOLABX_FORECAST_WORKER_WATCH", "").lower() in {"1", "true", "yes"},
        help="Mantiene el worker activo y consulta nuevas publicaciones periódicamente.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("METEOLABX_FORECAST_WORKER_INTERVAL_S", "300")),
        help="Segundos entre ciclos cuando --watch está activo.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("METEOLABX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.watch:
        result = run_incremental_cycle(max_hours=max(0, args.max_hours))
        logger.info("Ciclo terminado: %s", result)
        return 0 if result["failures"] == 0 else 2

    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    interval = max(30, args.interval)
    while not stopping:
        started = time.monotonic()
        try:
            result = run_incremental_cycle(max_hours=max(0, args.max_hours))
            logger.info("Ciclo terminado: %s", result)
        except Exception:
            logger.exception("El ciclo incremental ha fallado; se reintentará.")
        remaining = max(0.0, interval - (time.monotonic() - started))
        while remaining > 0 and not stopping:
            sleep_for = min(1.0, remaining)
            time.sleep(sleep_for)
            remaining -= sleep_for
    logger.info("Worker detenido correctamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
