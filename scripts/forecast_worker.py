#!/usr/bin/env python3
"""Worker reanudable que precalcula y publica las pasadas AROME."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import multiprocessing
import threading
import os
from pathlib import Path
from queue import Empty
import signal
import time
from typing import Any, Iterator, Sequence

from server.config import get_settings
from server.services.arome_forecast import (
    accumulated_precip_series,
    catalog_payload,
    frame_grid,
)
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
    prune_retained_runs,
    register_run_slot,
    retained_manifests,
    run_manifest_key,
    write_grid,
    write_json,
)
from server.services.arome_packages import (
    AromePackageError,
    block_range,
    discard_packages_before,
    ensure_package,
)
from server.services.meteofrance_auth import MeteoFranceAuthError
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
# Las tres cizalladuras arrancan del mismo viento a 10 m. Se calculan en un
# único trabajo para que compartan proceso y, con él, ese campo base.
SHEAR_PRODUCTS = tuple(
    product for product in FAST_DERIVED_PRODUCTS if product.startswith("shear-")
)
ACCUMULATED_PRECIP_PRODUCT = "accumulated-precip"
# DCAPE sale del grupo convectivo: es el unico que exige el rocio del WCS, y
# esperarlo retrasaria media hora a los otros trece. Va en su propio nivel,
# detras de ellos, para que no bloquee la pasada.
DCAPE_PRODUCT = "dcape"
PROFILE_PRODUCTS = tuple(
    product for product in CONVECTIVE_FORECAST_PRODUCTS if product != DCAPE_PRODUCT
)
# El acumulado se resuelve de una vez para toda la pasada: cada hora depende de
# los incrementos anteriores, así que publicarlas por separado los descargaba
# una y otra vez.
STANDALONE_FAST_PRODUCTS = tuple(
    product
    for product in FAST_DERIVED_PRODUCTS
    if product not in SHEAR_PRODUCTS and product != ACCUMULATED_PRECIP_PRODUCT
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
    # Horas adicionales que resuelve el mismo trabajo. Solo la usa el acumulado
    # de precipitación, que recorre la pasada entera para no volver a descargar
    # los incrementos anteriores en cada hora.
    valid_times: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return ",".join(self.products)

    @property
    def covered_times(self) -> tuple[str, ...]:
        return self.valid_times or (self.valid_time,)


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


# Horizonte que AROME publica en cada pasada. Fijarlo permite que el progreso
# vaya de 0 a 100 sin sobresaltos: contando solo las horas ya publicadas, el
# denominador crecía durante la publicación y el porcentaje retrocedía.
EXPECTED_NATIVE_HOURS = int(os.getenv("METEOLABX_FORECAST_EXPECTED_HOURS", "52"))


def _expected_hours(manifest: dict[str, Any], product: str) -> int:
    """Horas que se esperan de un producto en una pasada completa."""
    limits = manifest.get("expected_hours") or {}
    native = int(limits.get("native") or EXPECTED_NATIVE_HOURS)
    diagnostic = int(limits.get("diagnostic") or 0) or native
    caros = set(SHEAR_PRODUCTS) | set(CONVECTIVE_FORECAST_PRODUCTS)
    return diagnostic if product in caros else native


def _expected_frames(manifest: dict[str, Any], product: str, published: int) -> int:
    """Frames que cuentan en el denominador de ese producto.

    De los recortados solo se calculan las horas del límite, así que contar las
    demás sería contar trabajo que nunca se va a hacer. De los demás manda lo
    publicado si supera lo previsto.
    """
    expected = _expected_hours(manifest, product)
    caros = set(SHEAR_PRODUCTS) | set(CONVECTIVE_FORECAST_PRODUCTS)
    if product in caros:
        return expected
    return max(published, expected)


def _refresh_progress(manifest: dict[str, Any]) -> dict[str, Any]:
    total = 0
    available = 0
    errors = 0
    for product in PERSISTED_FORECAST_PRODUCTS:
        expected = set(_product_times(manifest, product))
        state = (manifest.get("products") or {}).get(product) or {}
        # El denominador es el horizonte completo, no lo publicado hasta ahora.
        total += _expected_frames(manifest, product, len(expected))
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
    progress.setdefault("active_jobs", [])
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


def _prune_old_runs(store, latest_run: str) -> None:
    """Libera el volumen y el disco temporal de lo que ya no se usa."""
    for run_iso in prune_retained_runs(store):
        logger.info("RUN %s eliminado del volumen por antigüedad", run_iso)
    # Los paquetes GRIB viven en el disco del contenedor, no en el volumen, y
    # cada bloque ocupa cientos de megas: solo interesan los del RUN vigente.
    for path in discard_packages_before(_parse_iso(latest_run)):
        logger.info("Paquete %s descartado", path.name)


def _merge_catalog_products(
    stored: dict[str, Any], live: dict[str, Any]
) -> dict[str, Any]:
    """Conserva las horas ya conocidas de cada producto de la pasada.

    Las coberturas del WCS aparecen y desaparecen mientras se publica un RUN, y
    copiar el catálogo en vivo tal cual encogía el total: el progreso retrocedía
    y las horas ya calculadas dejaban de contarse. Una hora publicada no se
    despublica, así que se unen.
    """
    merged: dict[str, Any] = {}
    for product in PERSISTED_FORECAST_PRODUCTS:
        current = live.get(product)
        previous = stored.get(product)
        if current and previous:
            item = dict(current)
            item["valid_times"] = sorted(
                set(current.get("valid_times", ()))
                | set(previous.get("valid_times", ())),
                key=_parse_iso,
            )
            merged[product] = item
        elif current:
            merged[product] = dict(current)
        elif previous:
            merged[product] = dict(previous)
    return merged


def _prepare_latest_manifest(
    store,
    catalog: dict[str, Any],
    calculation_scope: str,
    diagnostic_max_hours: int = 0,
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
        stored = manifest.get("catalog_products") or {}
        manifest["catalog_products"] = _merge_catalog_products(stored, catalog_products)
        manifest["expected_times"] = sorted(
            set(expected)
            | {
                valid
                for item in manifest["catalog_products"].values()
                for valid in item.get("valid_times", ())
            },
            key=_parse_iso,
        )
        manifest["status"] = "publishing"
        # Un contenedor anterior pudo morir con una tarea marcada como activa.
        manifest.setdefault("progress", {})["current_job"] = None
        manifest["progress"]["active_jobs"] = []
    # Con los horizontes guardados, el progreso conoce su denominador desde el
    # primer ciclo en vez de deducirlo de lo publicado hasta ese momento.
    manifest["expected_hours"] = {
        "native": EXPECTED_NATIVE_HOURS,
        "diagnostic": diagnostic_max_hours or EXPECTED_NATIVE_HOURS,
    }
    manifest["worker_heartbeat_at"] = _utc_now()
    _persist_manifest(store, manifest, latest_run=run_iso)
    _publish_run_slot(store, manifest)
    _prune_old_runs(store, run_iso)
    return manifest


def _retry_is_due(state: dict[str, Any], valid_time: str, now: datetime) -> bool:
    value = (state.get("retry_after") or {}).get(valid_time)
    if not value:
        return True
    try:
        return _parse_iso(str(value)) <= now
    except (TypeError, ValueError):
        return True


def _grouped_jobs(
    manifest: dict[str, Any],
    products: tuple[str, ...],
    allowed_times: set[str],
    now: datetime,
    *,
    tier: int,
) -> list[ForecastJob]:
    """Un trabajo por hora con los productos del grupo que sigan pendientes.

    Los productos de un grupo comparten un cálculo intermedio caro, así que
    interesa que se resuelvan en el mismo proceso.
    """
    run_iso = str(manifest["run"])
    scope = str(manifest.get("calculation_scope", "model"))
    times = sorted(
        {
            valid
            for product in products
            for valid in _product_times(manifest, product)
            if valid in allowed_times
        },
        key=_parse_iso,
    )
    jobs: list[ForecastJob] = []
    for valid_time in times:
        pending_products = []
        for product in products:
            if valid_time not in _product_times(manifest, product):
                continue
            state = (manifest.get("products") or {}).get(product) or {}
            if valid_time in set(state.get("available_times", ())):
                continue
            if _retry_is_due(state, valid_time, now):
                pending_products.append(product)
        if pending_products:
            jobs.append(
                ForecastJob(run_iso, valid_time, tuple(pending_products), scope, tier)
            )
    return jobs


def _jobs_for_manifest(
    manifest: dict[str, Any],
    *,
    max_hours: int = 0,
    diagnostic_max_hours: int = 0,
    now: datetime | None = None,
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
    # Un campo nativo cuesta segundos y una hora convectiva varios minutos, así
    # que el horizonte se recorta solo donde duele: cizalladuras y diagnósticos
    # convectivos. Los nativos siguen cubriendo la pasada entera.
    diagnostic_times = allowed_times
    if diagnostic_max_hours > 0:
        diagnostic_times = allowed_times & set(all_times[:diagnostic_max_hours])
    jobs: list[ForecastJob] = []

    for tier, products in ((0, NATIVE_PRODUCTS), (1, STANDALONE_FAST_PRODUCTS)):
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

    jobs.extend(
        _grouped_jobs(manifest, SHEAR_PRODUCTS, diagnostic_times, now, tier=1)
    )

    accumulated_state = (manifest.get("products") or {}).get(
        ACCUMULATED_PRECIP_PRODUCT
    ) or {}
    accumulated_available = set(accumulated_state.get("available_times", ()))
    accumulated_pending = tuple(
        valid_time
        for valid_time in _product_times(manifest, ACCUMULATED_PRECIP_PRODUCT)
        if valid_time in allowed_times
        and valid_time not in accumulated_available
        and _retry_is_due(accumulated_state, valid_time, now)
    )
    if accumulated_pending:
        jobs.append(
            ForecastJob(
                run_iso,
                # Lo representa su primera hora pendiente: la cola ordena por
                # hora, y con la última el acumulado quedaba detrás de todas
                # las cizalladuras, desplazándose según se publican horas.
                accumulated_pending[0],
                (ACCUMULATED_PRECIP_PRODUCT,),
                scope,
                1,
                accumulated_pending,
            )
        )

    jobs.extend(
        _grouped_jobs(manifest, PROFILE_PRODUCTS, diagnostic_times, now, tier=2)
    )
    jobs.extend(
        _grouped_jobs(manifest, (DCAPE_PRODUCT,), diagnostic_times, now, tier=3)
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
    if job.products == (ACCUMULATED_PRECIP_PRODUCT,) and job.valid_times:
        _store_accumulated_precip_series(token, store, job)
        return
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


def _store_accumulated_precip_series(token: str, store, job: ForecastJob) -> None:
    """Publica todas las horas del acumulado con una descarga por hora."""
    pending = tuple(
        valid_time
        for valid_time in job.valid_times
        if not store.exists(
            frame_key(
                job.run, ACCUMULATED_PRECIP_PRODUCT, valid_time, scope=job.scope
            )
        )
    )
    if not pending:
        return
    for valid_time, content, _headers in accumulated_precip_series(
        token, pending, run_iso=job.run
    ):
        write_grid(
            store,
            frame_key(
                job.run, ACCUMULATED_PRECIP_PRODUCT, valid_time, scope=job.scope
            ),
            content,
        )


# Bloques GRIB que se adelantan mientras el resto trabaja. Una pasada de 52
# horas son ocho bloques de siete, unos 5 GB en el temporal del contenedor,
# que tiene cientos de gigas libres. Adelantarlos todos mientras los niveles 0
# y 1 calculan hace que los perfiles convectivos, que empiezan mucho después,
# se encuentren el trabajo hecho en vez de descargar entre hora y hora.
PREFETCH_BLOCKS = max(
    0, int(os.getenv("METEOLABX_FORECAST_PREFETCH_BLOCKS", "8"))
)


def _blocks_ahead(jobs: Sequence[ForecastJob], limit: int) -> list[tuple[datetime, datetime]]:
    """Primeras horas de los bloques que vendrán después del que se usa ahora.

    Devuelve un (run, hora) por bloque, que es cuanto necesita ensure_package
    para saber qué fichero pedir.
    """
    vistos: dict[tuple[str, str], tuple[datetime, datetime]] = {}
    for job in jobs:
        if job.tier < 1:
            continue
        try:
            run = _parse_iso(job.run)
            valid_time = _parse_iso(job.valid_time)
            clave = (job.run, block_range(run, valid_time))
        except (AromePackageError, ValueError):
            continue
        if clave not in vistos:
            vistos[clave] = (run, valid_time)
        if len(vistos) > limit:
            break
    # El primero es el bloque en uso: ese ya lo está bajando quien lo necesita.
    return list(vistos.values())[1 : limit + 1]


def _start_package_prefetch(
    jobs: Sequence[ForecastJob], stop: threading.Event
) -> threading.Thread | None:
    """Baja por adelantado los bloques siguientes, en segundo plano.

    Mientras una hora se diagnostica no se está usando la red para nada, y la
    siguiente acabará necesitando su bloque. Adelantarlo convierte una espera
    en tiempo aprovechado. El cerrojo de ensure_package hace el resto: si el
    trabajo llega antes de que termine, espera a este en vez de bajar su copia.
    """
    from server.services.arome_forecast import _packages_available

    if PREFETCH_BLOCKS <= 0 or not _packages_available():
        return None
    objetivos = _blocks_ahead(jobs, PREFETCH_BLOCKS)
    if not objetivos:
        return None

    def adelantar() -> None:
        inicio = time.monotonic()
        bajados = 0
        for run, valid_time in objetivos:
            for paquete in ("IP1", "SP1", "SP2"):
                if stop.is_set():
                    return
                try:
                    ensure_package(paquete, run, valid_time)
                    bajados += 1
                except (AromePackageError, MeteoFranceAuthError) as exc:
                    # Que falle uno no cancela el resto: puede ser un bloque
                    # que Météo-France todavía no ha publicado, y los de más
                    # atrás sí están. Quien lo necesite reintentará por su
                    # cuenta cuando le toque.
                    logger.info("No se pudo adelantar %s: %s", paquete, exc)
                    continue
        logger.info(
            "Adelantados %d paquetes de %d bloques en %.0f s; los perfiles "
            "convectivos no deberían esperar descargas.",
            bajados, len(objetivos), time.monotonic() - inicio,
        )

    hilo = threading.Thread(target=adelantar, name="arome-prefetch", daemon=True)
    hilo.start()
    return hilo


def _configure_logging() -> None:
    """Formato y nivel de log, para el proceso padre y para cada hijo.

    Los trabajos se aíslan con «spawn», que arranca un intérprete limpio: sin
    volver a configurarlo, el hijo se queda en WARNING y todo lo que cuenta el
    trabajo de verdad —qué tarda cada fase, qué paquetes se bajan, cuándo se
    cae al WCS— se pierde sin dejar rastro.
    """
    logging.basicConfig(
        level=os.getenv("METEOLABX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _isolated_job_entry(result_queue, payload: dict[str, Any]) -> None:
    _configure_logging()
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
                # Sin esto el subproceso reconstruye el trabajo sin las horas
                # que cubre, y el padre daría por publicadas horas que nadie
                # ha calculado.
                "valid_times": job.valid_times,
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
    entry = {
        "id": _job_id(job),
        "run": job.run,
        "valid_time": job.valid_time,
        "products": list(job.products),
        "product": job.products[0] if len(job.products) == 1 else "convective-group",
        "started_at": now,
        "timeout_seconds": timeout_s,
    }
    progress = manifest.setdefault("progress", {})
    active = [
        item for item in progress.get("active_jobs", ())
        if item.get("id") != entry["id"]
    ]
    active.append(entry)
    progress["active_jobs"] = active
    progress["current_job"] = active[0]


def _job_id(job: ForecastJob) -> str:
    return f"{job.run}|{job.valid_time}|{job.label}"


def _clear_active_job(manifest: dict[str, Any], job: ForecastJob) -> None:
    progress = manifest.setdefault("progress", {})
    active = [
        item for item in progress.get("active_jobs", ())
        if item.get("id") != _job_id(job)
    ]
    progress["active_jobs"] = active
    progress["current_job"] = active[0] if active else None


def _mark_job_finished(manifest: dict[str, Any], job: ForecastJob) -> int:
    completed = 0
    for product in job.products:
        state = manifest.setdefault("products", {}).setdefault(
            product, {"available_times": [], "errors": {}}
        )
        available = set(state.get("available_times", ()))
        for valid_time in job.covered_times:
            if valid_time not in available:
                mark_available(manifest, product, valid_time)
                completed += 1
    _clear_active_job(manifest, job)
    manifest.setdefault("progress", {})["last_completed"] = {
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
        for valid_time in job.covered_times:
            mark_error(
                manifest,
                product,
                valid_time,
                message,
                retry_after=retry_after,
            )
    _clear_active_job(manifest, job)


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


def _job_group(job: ForecastJob) -> tuple[str, int]:
    """Identifica el bloque (pasada, nivel) al que pertenece un trabajo."""
    return (job.run, job.tier)


def _group_rank(group: tuple[str, int]) -> tuple[float, int]:
    """Ordena los bloques: primero la pasada más reciente, luego por nivel."""
    return (-_parse_iso(group[0]).timestamp(), group[1])


def _parallel_work_order(
    manifests: list[dict[str, Any]], queues: dict[str, list[ForecastJob]]
) -> list[tuple[dict[str, Any], ForecastJob]]:
    """Ordena el trabajo por pasada y, dentro de ella, por dependencias.

    La pasada manda sobre el nivel: así el RUN vigente se publica entero antes
    de invertir tiempo en las pasadas anteriores. Con el criterio inverso, los
    campos nativos de las tres pasadas retenidas se adelantaban a los
    diagnósticos del RUN actual y ninguna llegaba a completarse.
    """
    work = [
        (manifest, job)
        for manifest in manifests
        for job in queues.get(str(manifest["run"]), ())
    ]
    return sorted(
        work,
        key=lambda item: (
            *_group_rank(_job_group(item[1])),
            _parse_iso(item[1].valid_time),
            min(PRODUCT_ORDER.get(product, 999) for product in item[1].products),
        ),
    )


# Fracción de memoria del contenedor por encima de la cual no se admite un
# segundo perfil a la vez. Es un freno de emergencia, no una garantía: mide
# antes de lanzar, y el perfil recién admitido crece durante los minutos
# siguientes. Medido en Railway, cada perfil cuesta ~2,9 GB sobre una base de
# 2,3, así que dos no caben en 8 GB por mucho que el instante del lanzamiento
# parezca despejado.
HEAVY_MEMORY_CEILING = max(
    0.1, min(0.95, float(os.getenv("METEOLABX_FORECAST_HEAVY_MEMORY_CEILING", "0.55")))
)


def _cgroup_anonymous_bytes() -> int | None:
    """Memoria anónima del cgroup, la que el núcleo no puede recuperar.

    memory.current incluye el page cache, y desde que los perfiles se sirven
    desde un fichero mapeado buena parte de ese cache es nuestro: memoria que
    el núcleo suelta en cuanto aprieta, en vez de invocar al OOM. Contarla como
    ocupada hacía rechazar un segundo perfil que sí cabía.
    """
    try:
        for linea in Path("/sys/fs/cgroup/memory.stat").read_text().splitlines():
            campo, _, valor = linea.partition(" ")
            if campo == "anon":
                return int(valor)
    except (FileNotFoundError, OSError, ValueError):
        pass
    return None


def _container_memory_ratio() -> float | None:
    """Uso de memoria del cgroup; permite no lanzar un segundo perfil si no cabe."""
    candidates = (
        (Path("/sys/fs/cgroup/memory.current"), Path("/sys/fs/cgroup/memory.max")),
        (
            Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
            Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
        ),
    )
    for current_path, limit_path in candidates:
        try:
            current = int(current_path.read_text().strip())
            raw_limit = limit_path.read_text().strip()
            if raw_limit == "max":
                continue
            limit = int(raw_limit)
            if limit <= 0:
                continue
            # La anónima cuando se puede leer; si no, el total, que es lo que
            # había antes y peca de conservador.
            anonima = _cgroup_anonymous_bytes()
            return (anonima if anonima is not None else current) / limit
        except (FileNotFoundError, OSError, ValueError):
            continue
    return None


def _run_parallel_work(
    *,
    store,
    manifests: list[dict[str, Any]],
    queues: dict[str, list[ForecastJob]],
    latest_run: str,
    workers: int,
    heavy_workers: int,
    max_tasks: int,
    cycle_budget_s: int,
    native_timeout_s: int,
    derived_timeout_s: int,
) -> tuple[int, int, int]:
    """Calcula frames en paralelo; solo el padre modifica los manifiestos."""
    pending = _parallel_work_order(manifests, queues)
    prefetch_stop = threading.Event()
    prefetch = _start_package_prefetch([job for _, job in pending], prefetch_stop)
    active: dict[Future[None], tuple[dict[str, Any], ForecastJob]] = {}
    started_at = time.monotonic()
    tasks_started = 0
    tasks_completed = 0
    frames_completed = 0
    failures = 0
    last_heavy_launch = 0.0

    def tier_capacity(tier: int) -> int:
        return min(workers, heavy_workers) if tier >= 2 else workers

    def persist_result(manifest: dict[str, Any], run_iso: str) -> None:
        manifest["worker_heartbeat_at"] = _utc_now()
        _finish_status(manifest)
        _persist_manifest(store, manifest, latest_run=latest_run)
        write_json(
            store,
            WORKER_STATE_KEY,
            {
                "version": 2,
                "last_run": run_iso,
                "heartbeat_at": manifest["worker_heartbeat_at"],
                "workers": workers,
            },
        )

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="arome-job") as executor:
        while pending or active:
            budget_reached = (
                cycle_budget_s > 0
                and time.monotonic() - started_at >= cycle_budget_s
            )
            task_limit_reached = max_tasks > 0 and tasks_started >= max_tasks

            # No mezclamos pasadas ni niveles: así los campos base terminan
            # antes de los derivados, los perfiles convectivos no compiten por
            # la API y el RUN vigente se completa antes de tocar los anteriores.
            active_groups = {_job_group(job) for _manifest, job in active.values()}
            launch_group = (
                min(active_groups, key=_group_rank)
                if active_groups
                else (_job_group(pending[0][1]) if pending else None)
            )
            launch_tier = launch_group[1] if launch_group is not None else None
            capacity = tier_capacity(launch_tier) if launch_tier is not None else 0
            while (
                pending
                and not budget_reached
                and not task_limit_reached
                and len(active) < capacity
                and _job_group(pending[0][1]) == launch_group
            ):
                if launch_tier == 2 and active:
                    # El primer perfil ya está aumentando su memoria. Esperar
                    # permite medir el cgroup antes de admitir el segundo.
                    if time.monotonic() - last_heavy_launch < 15.0:
                        break
                    memory_ratio = _container_memory_ratio()
                    if memory_ratio is not None and memory_ratio >= HEAVY_MEMORY_CEILING:
                        # Sin esta traza, un segundo worker configurado pero
                        # nunca admitido parece que no hace nada.
                        logger.info(
                            "Segundo perfil en espera: memoria del cgroup al "
                            "%.0f %% (tope %.0f %%).",
                            memory_ratio * 100.0,
                            HEAVY_MEMORY_CEILING * 100.0,
                        )
                        break
                manifest, job = pending.pop(0)
                timeout_s = derived_timeout_s if job.tier > 0 else native_timeout_s
                _mark_job_started(manifest, job, timeout_s)
                _persist_manifest(store, manifest, latest_run=latest_run)
                logger.info(
                    "Procesando en paralelo RUN %s · %s · %s (nivel %d)",
                    job.run,
                    job.valid_time,
                    job.label,
                    job.tier,
                )
                future = executor.submit(_run_isolated_job, job, timeout_s)
                active[future] = (manifest, job)
                if job.tier == 2:
                    last_heavy_launch = time.monotonic()
                tasks_started += 1
                task_limit_reached = max_tasks > 0 and tasks_started >= max_tasks

            completed_futures = [future for future in active if future.done()]
            if not completed_futures:
                if active:
                    time.sleep(0.1)
                    continue
                # El presupuesto o el límite impiden lanzar más trabajos.
                break

            for future in completed_futures:
                manifest, job = active.pop(future)
                try:
                    future.result()
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
                    persist_result(manifest, job.run)

    prefetch_stop.set()
    if prefetch is not None:
        # No se espera: es trabajo adelantado, y el fichero a medias queda como
        # .part sin que nadie lo confunda con uno completo.
        prefetch.join(timeout=0.1)
    return tasks_completed, frames_completed, failures


def run_incremental_cycle(
    *,
    max_hours: int = 0,
    diagnostic_max_hours: int = 0,
    max_tasks: int = 0,
    cycle_budget_s: int = 0,
    native_timeout_s: int = 300,
    derived_timeout_s: int = 1_800,
    isolate_tasks: bool = False,
    workers: int = 1,
    heavy_workers: int = 1,
) -> dict[str, Any]:
    settings = get_settings()
    token = str(settings.arome_api_key or "").strip()
    if not token:
        raise RuntimeError("METEOLABX_AROME_API_KEY no está configurada.")

    store = get_forecast_store()
    catalog = catalog_payload(token)
    calculation_scope = forecast_calculation_scope()
    latest_manifest = _prepare_latest_manifest(
        store, catalog, calculation_scope, diagnostic_max_hours=diagnostic_max_hours
    )
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
            manifest,
            max_hours=max(0, max_hours),
            diagnostic_max_hours=max(0, diagnostic_max_hours),
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
        "RUN actual %s: %d tareas pendientes en %d pasadas retenidas · %d workers",
        latest_run,
        pending_count,
        len(manifests),
        max(1, workers),
    )

    if workers > 1:
        tasks_completed, frames_completed, failures = _run_parallel_work(
            store=store,
            manifests=manifests,
            queues=queues,
            latest_run=latest_run,
            workers=max(2, workers),
            heavy_workers=max(1, min(heavy_workers, workers)),
            max_tasks=max_tasks,
            cycle_budget_s=cycle_budget_s,
            native_timeout_s=native_timeout_s,
            derived_timeout_s=derived_timeout_s,
        )
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
            "workers": workers,
            "progress": _refresh_progress(latest_manifest),
        }

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
        "--diagnostic-max-hours",
        type=int,
        default=int(os.getenv("METEOLABX_FORECAST_DIAGNOSTIC_MAX_HOURS", "0")),
        help=(
            "Limita el horizonte de cizalladuras y diagnósticos convectivos, "
            "que son los caros; 0 usa el mismo que el resto."
        ),
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
        "--workers",
        type=int,
        default=int(os.getenv("METEOLABX_FORECAST_WORKERS", "6")),
        help="Número de cálculos simultáneos para campos base y derivados rápidos.",
    )
    parser.add_argument(
        "--heavy-workers",
        type=int,
        default=int(os.getenv("METEOLABX_FORECAST_HEAVY_WORKERS", "1")),
        help="Máximo de perfiles convectivos simultáneos para limitar RAM y cuota API.",
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
    _configure_logging()

    def run_cycle() -> dict[str, Any]:
        return run_incremental_cycle(
            max_hours=max(0, args.max_hours),
            diagnostic_max_hours=max(0, args.diagnostic_max_hours),
            max_tasks=max(0, args.max_tasks),
            cycle_budget_s=max(0, args.cycle_budget),
            native_timeout_s=max(1, args.native_timeout),
            derived_timeout_s=max(1, args.derived_timeout),
            isolate_tasks=args.isolate_tasks,
            workers=max(1, args.workers),
            heavy_workers=max(1, args.heavy_workers),
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
