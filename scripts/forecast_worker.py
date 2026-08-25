#!/usr/bin/env python3
"""Worker reanudable que precalcula y publica las pasadas AROME."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import multiprocessing
import os
from queue import Empty
import signal
import time
from typing import Any, Iterator

from server.config import get_settings
from server.services.arome_forecast import catalog_payload, frame_grid
from server.services.forecast_store import (
    CONVECTIVE_FORECAST_PRODUCTS,
    DERIVED_FORECAST_PRODUCTS,
    LATEST_MANIFEST_KEY,
    PERSISTED_FORECAST_PRODUCTS,
    delete_run,
    frame_key,
    get_forecast_store,
    mark_available,
    mark_error,
    new_manifest,
    read_json,
    register_run_slot,
    retained_manifests,
    run_manifest_key,
    write_grid,
    write_json,
)
from tabs.arome_forecast import forecast_calculation_scope


logger = logging.getLogger("meteolabx.forecast_worker")
WORKER_STATE_KEY = "forecast/worker/state.json"

NATIVE_PRODUCTS = tuple(
    product
    for product in PERSISTED_FORECAST_PRODUCTS
    if product not in DERIVED_FORECAST_PRODUCTS
)
FAST_DERIVED_PRODUCTS = tuple(
    product
    for product in DERIVED_FORECAST_PRODUCTS
    if product not in CONVECTIVE_FORECAST_PRODUCTS
)
PRODUCT_ORDER = {
    product: index
    for index, product in enumerate(
        (*NATIVE_PRODUCTS, *FAST_DERIVED_PRODUCTS, *CONVECTIVE_FORECAST_PRODUCTS)
    )
}


class FrameTaskTimeout(TimeoutError):
    """Una tarea individual superó el límite y el worker debe continuar."""


@dataclass(frozen=True)
class ForecastJob:
    run: str
    valid_time: str
    products: tuple[str, ...]
    scope: str
    tier: int

    @property
    def label(self) -> str:
        return ",".join(self.products)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _latest_persisted_run(catalog: dict[str, Any]) -> str:
    runs = [
        item["run"]
        for product in PERSISTED_FORECAST_PRODUCTS
        if (item := catalog.get("products", {}).get(product)) and item.get("run")
    ]
    if not runs:
        raise RuntimeError("El catálogo no contiene productos AROME persistibles.")
    return max(runs, key=_parse_iso)


def _product_times_from_catalog(
    catalog: dict[str, Any], run_iso: str, product: str
) -> list[str]:
    item = catalog.get("products", {}).get(product) or {}
    if item.get("run") != run_iso:
        return []
    return sorted(set(item.get("valid_times", ())), key=_parse_iso)


def _product_times(manifest: dict[str, Any], product: str) -> list[str]:
    item = (manifest.get("catalog_products") or {}).get(product) or {}
    return sorted(set(item.get("valid_times", ())), key=_parse_iso)


def pending_hours(
    catalog: dict[str, Any], manifest: dict[str, Any], run_iso: str
) -> list[str]:
    """Compatibilidad: horas que todavía contienen algún frame pendiente."""
    pending: set[str] = set()
    for product in PERSISTED_FORECAST_PRODUCTS:
        available = set(
            manifest.get("products", {}).get(product, {}).get("available_times", ())
        )
        pending.update(
            set(_product_times_from_catalog(catalog, run_iso, product)) - available
        )
    return sorted(pending, key=_parse_iso)


def _refresh_progress(manifest: dict[str, Any]) -> dict[str, Any]:
    total = 0
    available = 0
    errors = 0
    for product in PERSISTED_FORECAST_PRODUCTS:
        expected = set(_product_times(manifest, product))
        state = (manifest.get("products") or {}).get(product) or {}
        total += len(expected)
        available += len(expected & set(state.get("available_times", ())))
        errors += len(set(state.get("errors", {})) & expected)
    progress = manifest.setdefault("progress", {})
    progress.update(
        {
            "frames_available": available,
            "frames_total": total,
            "percent": round((available * 100.0 / total), 2) if total else 0.0,
            "error_count": errors,
        }
    )
    progress.setdefault("current_job", None)
    progress.setdefault("last_completed", None)
    return progress


def _persist_manifest(
    store, manifest: dict[str, Any], *, latest_run: str | None = None
) -> None:
    _refresh_progress(manifest)
    write_json(store, run_manifest_key(str(manifest["run"])), manifest)
    if latest_run is None or str(manifest["run"]) == latest_run:
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


def _prepare_latest_manifest(
    store, catalog: dict[str, Any], calculation_scope: str
) -> dict[str, Any]:
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
            for valid in _product_times_from_catalog(catalog, run_iso, product)
        },
        key=_parse_iso,
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
        # Un contenedor anterior pudo morir con una tarea marcada como activa.
        manifest.setdefault("progress", {})["current_job"] = None
    manifest["worker_heartbeat_at"] = _utc_now()
    _persist_manifest(store, manifest, latest_run=run_iso)
    _publish_run_slot(store, manifest)
    return manifest


def _retry_is_due(state: dict[str, Any], valid_time: str, now: datetime) -> bool:
    value = (state.get("retry_after") or {}).get(valid_time)
    if not value:
        return True
    try:
        return _parse_iso(str(value)) <= now
    except (TypeError, ValueError):
        return True


def _jobs_for_manifest(
    manifest: dict[str, Any], *, max_hours: int = 0, now: datetime | None = None
) -> list[ForecastJob]:
    now = now or datetime.now(timezone.utc)
    run_iso = str(manifest["run"])
    scope = str(manifest.get("calculation_scope", "model"))
    all_times = sorted(
        {
            valid
            for product in PERSISTED_FORECAST_PRODUCTS
            for valid in _product_times(manifest, product)
        },
        key=_parse_iso,
    )
    allowed_times = set(all_times[:max_hours] if max_hours > 0 else all_times)
    jobs: list[ForecastJob] = []

    for tier, products in ((0, NATIVE_PRODUCTS), (1, FAST_DERIVED_PRODUCTS)):
        for product in products:
            state = (manifest.get("products") or {}).get(product) or {}
            available = set(state.get("available_times", ()))
            for valid_time in _product_times(manifest, product):
                if (
                    valid_time in allowed_times
                    and valid_time not in available
                    and _retry_is_due(state, valid_time, now)
                ):
                    jobs.append(
                        ForecastJob(run_iso, valid_time, (product,), scope, tier)
                    )

    convective_times = sorted(
        {
            valid
            for product in CONVECTIVE_FORECAST_PRODUCTS
            for valid in _product_times(manifest, product)
            if valid in allowed_times
        },
        key=_parse_iso,
    )
    for valid_time in convective_times:
        pending_products = []
        for product in CONVECTIVE_FORECAST_PRODUCTS:
            if valid_time not in _product_times(manifest, product):
                continue
            state = (manifest.get("products") or {}).get(product) or {}
            if valid_time in set(state.get("available_times", ())):
                continue
            if _retry_is_due(state, valid_time, now):
                pending_products.append(product)
        if pending_products:
            jobs.append(
                ForecastJob(
                    run_iso,
                    valid_time,
                    tuple(pending_products),
                    scope,
                    2,
                )
            )

    return sorted(
        jobs,
        key=lambda job: (
            job.tier,
            _parse_iso(job.valid_time),
            min(PRODUCT_ORDER.get(product, 999) for product in job.products),
        ),
    )


def _frame_path(store, job: ForecastJob, product: str) -> str:
    del store
    return frame_key(
        job.run,
        product,
        job.valid_time,
        scope=job.scope,
        vertical_kind="height" if product == "wind-level" else None,
        level=10.0 if product == "wind-level" else None,
    )


def _calculate_and_store_job(token: str, store, job: ForecastJob) -> None:
    """Calcula los productos del trabajo reutilizando el perfil convectivo."""
    for product in job.products:
        key = _frame_path(store, job, product)
        if store.exists(key):
            continue
        content, _headers = frame_grid(
            token,
            product,
            job.valid_time,
            run_iso=job.run,
        )
        write_grid(store, key, content)


def _isolated_job_entry(result_queue, payload: dict[str, Any]) -> None:
    try:
        settings = get_settings()
        token = str(settings.arome_api_key or "").strip()
        if not token:
            raise RuntimeError("METEOLABX_AROME_API_KEY no está configurada.")
        job = ForecastJob(**payload)
        _calculate_and_store_job(token, get_forecast_store(), job)
        result_queue.put(("ok", ""))
    except BaseException as exc:  # pragma: no cover - se valida desde el padre
        result_queue.put(("error", f"{type(exc).__name__}: {exc}"[:500]))


def _run_isolated_job(job: ForecastJob, timeout_s: int) -> None:
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_isolated_job_entry,
        args=(
            result_queue,
            {
                "run": job.run,
                "valid_time": job.valid_time,
                "products": job.products,
                "scope": job.scope,
                "tier": job.tier,
            },
        ),
        name=f"arome-{job.products[0]}-{job.valid_time[11:13]}",
    )
    process.start()
    process.join(timeout=max(1, timeout_s))
    if process.is_alive():
        process.terminate()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join(5)
        raise FrameTaskTimeout(
            f"{job.label} {job.valid_time} superó {timeout_s} s"
        )
    try:
        status, message = result_queue.get(timeout=2)
    except Empty:
        if process.exitcode == 0:
            return
        raise RuntimeError(
            f"El subproceso terminó con código {process.exitcode} sin resultado."
        )
    finally:
        result_queue.close()
    if status != "ok":
        raise RuntimeError(message)


@contextmanager
def _direct_timeout(seconds: int) -> Iterator[None]:
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def expired(_signum, _frame):
        raise FrameTaskTimeout(f"La tarea superó {seconds} s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _run_job(
    token: str,
    store,
    job: ForecastJob,
    *,
    isolate_tasks: bool,
    native_timeout_s: int,
    derived_timeout_s: int,
) -> None:
    timeout_s = derived_timeout_s if job.tier > 0 else native_timeout_s
    if isolate_tasks and job.tier > 0:
        _run_isolated_job(job, timeout_s)
        return
    with _direct_timeout(timeout_s):
        _calculate_and_store_job(token, store, job)


def _mark_job_started(manifest: dict[str, Any], job: ForecastJob, timeout_s: int) -> None:
    now = _utc_now()
    manifest["worker_heartbeat_at"] = now
    manifest["updated_at"] = now
    manifest.setdefault("progress", {})["current_job"] = {
        "run": job.run,
        "valid_time": job.valid_time,
        "products": list(job.products),
        "product": job.products[0] if len(job.products) == 1 else "convective-group",
        "started_at": now,
        "timeout_seconds": timeout_s,
    }


def _mark_job_finished(manifest: dict[str, Any], job: ForecastJob) -> int:
    completed = 0
    for product in job.products:
        state = manifest.setdefault("products", {}).setdefault(
            product, {"available_times": [], "errors": {}}
        )
        if job.valid_time not in set(state.get("available_times", ())):
            mark_available(manifest, product, job.valid_time)
            completed += 1
    manifest.setdefault("progress", {})["current_job"] = None
    manifest["progress"]["last_completed"] = {
        "run": job.run,
        "valid_time": job.valid_time,
        "products": list(job.products),
        "completed_at": _utc_now(),
    }
    return completed


def _mark_job_failed(
    manifest: dict[str, Any], job: ForecastJob, message: str
) -> None:
    attempts = max(
        (
            int(
                ((manifest.get("products") or {}).get(product) or {})
                .get("attempts", {})
                .get(job.valid_time, 0)
            )
            for product in job.products
        ),
        default=0,
    )
    delay_s = min(3_600, 300 * (2 ** min(attempts, 3)))
    retry_after = (
        datetime.now(timezone.utc) + timedelta(seconds=delay_s)
    ).isoformat().replace("+00:00", "Z")
    for product in job.products:
        mark_error(
            manifest,
            product,
            job.valid_time,
            message,
            retry_after=retry_after,
        )
    manifest.setdefault("progress", {})["current_job"] = None


def _finish_status(manifest: dict[str, Any]) -> None:
    all_complete = True
    for product in PERSISTED_FORECAST_PRODUCTS:
        expected_product = set(_product_times(manifest, product))
        available_product = set(
            ((manifest.get("products") or {}).get(product) or {}).get(
                "available_times", ()
            )
        )
        if not expected_product.issubset(available_product):
            all_complete = False
            break
    run_time = _parse_iso(str(manifest["run"]))
    expected = manifest.get("expected_times", ())
    final_horizon = max(
        (
            int((_parse_iso(value) - run_time).total_seconds() // 3600)
            for value in expected
        ),
        default=-1,
    )
    manifest["status"] = (
        "complete" if all_complete and final_horizon >= 51 else "publishing"
    )


def _rotated_manifests(store, manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(manifests) < 2:
        return manifests
    state = read_json(store, WORKER_STATE_KEY) or {}
    last_run = state.get("last_run")
    runs = [str(manifest.get("run")) for manifest in manifests]
    if last_run not in runs:
        return manifests
    start = (runs.index(last_run) + 1) % len(manifests)
    return manifests[start:] + manifests[:start]


def run_incremental_cycle(
    *,
    max_hours: int = 0,
    max_tasks: int = 0,
    cycle_budget_s: int = 0,
    native_timeout_s: int = 300,
    derived_timeout_s: int = 1_800,
    isolate_tasks: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    token = str(settings.arome_api_key or "").strip()
    if not token:
        raise RuntimeError("METEOLABX_AROME_API_KEY no está configurada.")

    store = get_forecast_store()
    catalog = catalog_payload(token)
    calculation_scope = forecast_calculation_scope()
    latest_manifest = _prepare_latest_manifest(store, catalog, calculation_scope)
    latest_run = str(latest_manifest["run"])
    manifests = [
        manifest
        for manifest in retained_manifests(store)
        if str(manifest.get("calculation_scope", "model")) == calculation_scope
    ]
    manifests = _rotated_manifests(store, manifests)
    latest_manifest = next(
        (
            manifest
            for manifest in manifests
            if str(manifest.get("run")) == latest_run
        ),
        latest_manifest,
    )
    if _refresh_progress(latest_manifest)["frames_available"] == 0:
        manifests.sort(key=lambda item: str(item.get("run")) != latest_run)

    queues = {
        str(manifest["run"]): _jobs_for_manifest(
            manifest, max_hours=max(0, max_hours)
        )
        for manifest in manifests
    }
    started = time.monotonic()
    tasks_completed = 0
    frames_completed = 0
    failures = 0
    stop_cycle = False
    pending_count = sum(len(queue) for queue in queues.values())
    logger.info(
        "RUN actual %s: %d tareas pendientes en %d pasadas retenidas",
        latest_run,
        pending_count,
        len(manifests),
    )

    while not stop_cycle and any(queues.values()):
        made_progress = False
        for manifest in manifests:
            run_iso = str(manifest["run"])
            queue = queues.get(run_iso) or []
            if not queue:
                continue
            if max_tasks > 0 and tasks_completed >= max_tasks:
                stop_cycle = True
                break
            if cycle_budget_s > 0 and time.monotonic() - started >= cycle_budget_s:
                stop_cycle = True
                break

            job = queue.pop(0)
            made_progress = True
            timeout_s = derived_timeout_s if job.tier > 0 else native_timeout_s
            _mark_job_started(manifest, job, timeout_s)
            _persist_manifest(store, manifest, latest_run=latest_run)
            logger.info(
                "Procesando RUN %s · %s · %s (nivel %d)",
                job.run,
                job.valid_time,
                job.label,
                job.tier,
            )
            try:
                _run_job(
                    token,
                    store,
                    job,
                    isolate_tasks=isolate_tasks,
                    native_timeout_s=native_timeout_s,
                    derived_timeout_s=derived_timeout_s,
                )
                completed = _mark_job_finished(manifest, job)
                frames_completed += completed
                logger.info(
                    "Completado %s %s: +%d frames",
                    job.label,
                    job.valid_time,
                    completed,
                )
            except Exception as exc:
                failures += 1
                _mark_job_failed(manifest, job, str(exc))
                logger.exception(
                    "No se pudo calcular %s %s; continuará con la cola",
                    job.label,
                    job.valid_time,
                )
            finally:
                tasks_completed += 1
                manifest["worker_heartbeat_at"] = _utc_now()
                _finish_status(manifest)
                _persist_manifest(store, manifest, latest_run=latest_run)
                write_json(
                    store,
                    WORKER_STATE_KEY,
                    {
                        "version": 1,
                        "last_run": run_iso,
                        "heartbeat_at": manifest["worker_heartbeat_at"],
                    },
                )
        if not made_progress:
            break

    for manifest in manifests:
        _finish_status(manifest)
        _persist_manifest(store, manifest, latest_run=latest_run)

    return {
        "run": latest_run,
        "tasks_seen": tasks_completed,
        "frames_completed": frames_completed,
        "failures": failures,
        "status": latest_manifest["status"],
        "calculation_scope": calculation_scope,
        "progress": _refresh_progress(latest_manifest),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-hours",
        type=int,
        default=int(os.getenv("METEOLABX_FORECAST_WORKER_MAX_HOURS", "0")),
        help="Limita las horas consideradas por pasada; 0 usa todas.",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=int(os.getenv("METEOLABX_FORECAST_WORKER_MAX_TASKS", "48")),
        help="Máximo de tareas por ciclo; 0 no aplica límite.",
    )
    parser.add_argument(
        "--cycle-budget",
        type=int,
        default=int(os.getenv("METEOLABX_FORECAST_WORKER_CYCLE_BUDGET_S", "240")),
        help="Tiempo objetivo de cada ciclo antes de refrescar el catálogo.",
    )
    parser.add_argument(
        "--native-timeout",
        type=int,
        default=int(os.getenv("METEOLABX_FORECAST_NATIVE_TIMEOUT_S", "300")),
        help="Límite por campo nativo en segundos.",
    )
    parser.add_argument(
        "--derived-timeout",
        type=int,
        default=int(os.getenv("METEOLABX_FORECAST_DERIVED_TIMEOUT_S", "1800")),
        help="Límite por diagnóstico aislado en segundos.",
    )
    parser.add_argument(
        "--isolate-tasks",
        action="store_true",
        default=os.getenv("METEOLABX_FORECAST_ISOLATE_TASKS", "").lower()
        in {"1", "true", "yes"},
        help="Aísla diagnósticos derivados para poder terminarlos si se bloquean.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        default=os.getenv("METEOLABX_FORECAST_WORKER_WATCH", "").lower()
        in {"1", "true", "yes"},
        help="Mantiene el worker activo y consulta nuevas publicaciones.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("METEOLABX_FORECAST_WORKER_INTERVAL_S", "60")),
        help="Segundos entre ciclos cuando --watch está activo.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("METEOLABX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    def run_cycle() -> dict[str, Any]:
        return run_incremental_cycle(
            max_hours=max(0, args.max_hours),
            max_tasks=max(0, args.max_tasks),
            cycle_budget_s=max(0, args.cycle_budget),
            native_timeout_s=max(1, args.native_timeout),
            derived_timeout_s=max(1, args.derived_timeout),
            isolate_tasks=args.isolate_tasks,
        )

    if not args.watch:
        result = run_cycle()
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
        cycle_started = time.monotonic()
        try:
            result = run_cycle()
            logger.info("Ciclo terminado: %s", result)
        except Exception:
            logger.exception("El ciclo incremental ha fallado; se reintentará.")
        remaining = max(0.0, interval - (time.monotonic() - cycle_started))
        while remaining > 0 and not stopping:
            sleep_for = min(1.0, remaining)
            time.sleep(sleep_for)
            remaining -= sleep_for
    logger.info("Worker detenido correctamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
