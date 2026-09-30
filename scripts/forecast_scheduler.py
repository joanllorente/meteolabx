"""Persistent AROME watch scheduler. Only its main thread owns manifests.

Catalog I/O, prefetch and ECMWF have independent lifetimes. One-shot runs keep
forecast_worker's bounded-cycle contract. No data retention runs with live jobs.
"""
from __future__ import annotations

import collections
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
# Aparte de `time`: los tests sustituyen ese módulo por un reloj virtual, y la
# marca que leen los otros procesos tiene que ser la hora real.
from time import time as _wall_clock

from server.services import arome_packages as packages
from server.services.arome_forecast import _packages_available

logger = logging.getLogger("meteolabx.forecast_worker")


def claims(job):
    """Also exclude overlapping accumulated jobs after catalog expansion."""
    return {(job.run, product, valid) for product in job.products for valid in job.covered_times}


class Readiness:
    def __init__(self, worker):
        self.w = worker
        self.seen = {}
        self.progress = {}
        self.wait = max(0, float(os.getenv("METEOLABX_AROME_PACKAGE_WAIT_S", "180")))
        self.sp1_wait = max(0, float(os.getenv("METEOLABX_AROME_SP1_WAIT_S", "45")))
        self.stall = max(0, float(os.getenv("METEOLABX_AROME_PACKAGE_STALL_S", "60")))

    def _native_packages(self, job):
        """Paquetes aprovechables por todos los productos de este trabajo."""
        from server.services.arome_forecast import (
            IP1_BACKED_PRODUCTS, PACKAGE_DERIVED_PRODUCTS, SP1_BACKED_PRODUCTS,
        )

        products = set(job.products)
        # Derivados que salen enteros de paquetes, como el CAPPI: se esperan
        # igual que un nativo de IP1, porque su respaldo por el WCS es caro.
        if products and products <= set(PACKAGE_DERIVED_PRODUCTS):
            return set().union(*(PACKAGE_DERIVED_PRODUCTS[p] for p in products))
        if not products or not products <= IP1_BACKED_PRODUCTS | SP1_BACKED_PRODUCTS:
            return set()
        wanted = set()
        if products & IP1_BACKED_PRODUCTS:
            wanted.add("IP1")
        if products & SP1_BACKED_PRODUCTS:
            wanted.add("SP1")
        if "mslp-theta-e-850" in products:
            wanted.update(("SP1", "SP2"))
        return wanted

    def observe(self, manifest, now):
        # First catalog observation, even if earlier tiers still have work.
        for valid in manifest.get("expected_times", ()):
            self.seen.setdefault((manifest["run"], valid), now)

    def downloading(self, package, run, valid, now):
        path = packages._package_path(package, run, packages.block_range(run, valid))
        sizes = packages._partial_sizes(path)
        previous, changed = self.progress.get(path, ({}, now))
        if any(key not in previous or size > previous[key] for key, size in sizes.items()):
            changed = now
        self.progress[path] = (sizes, changed)
        # A new empty partial gets the same inactivity grace as HTTP headers.
        return bool(sizes) and now - changed < self.stall

    def mode(self, job, now):
        native_packages = self._native_packages(job) if job.tier < 2 else set()
        sp1_only = native_packages == {"SP1"}
        if job.tier < 2 and not native_packages:
            return "ready"
        if not _packages_available():
            return "wcs_parallel" if sp1_only else "wcs"
        run, valid = self.w._parse_iso(job.run), self.w._parse_iso(job.valid_time)
        if job.tier < 2:
            # Espera acotada para aprovechar IP1/SP1 antes de recurrir al WCS.
            missing = [(p, valid) for p in sorted(native_packages)
                       if not packages.package_ready(p, run, valid)]
            # La primera hora de un bloque necesita el acumulado anterior
            # para restar lluvia/radiación; puede estar en otro fichero.
            if set(job.products) & {"precip-1h", "shortwave-down"}:
                from datetime import timedelta
                previous = valid - timedelta(hours=1)
                if previous > run and not packages.package_ready("SP1", run, previous):
                    missing.append(("SP1", previous))
            if not missing:
                return "ready"
            first = self.seen.setdefault((job.run, job.valid_time), now)
            # SP1 no retiene los huecos al final de la pasada, ni siquiera
            # si la descarga sigue avanzando. El lector sigue siendo local
            # y oportunista: aprovechará el paquete si llega antes de leerlo.
            if sp1_only and now - first >= self.sp1_wait:
                return "wcs_parallel"
            if any(self.downloading(p, run, instant, now) for p, instant in missing):
                return "downloading"
            return "publication" if sp1_only or now - first < self.wait else "wcs"
        # IP3 supplies vertical velocity for profiles, dewpoint for DCAPE.
        missing = [p for p in ("IP1", "IP3") if not packages.package_ready(p, run, valid)]
        if not missing:
            return "ready"
        growing = [self.downloading(p, run, valid, now) for p in missing]
        if any(growing):
            return "downloading"
        first = self.seen.setdefault((job.run, job.valid_time), now)
        if job.tier >= 3 or now - first < self.wait:
            return "publication"
        return "wcs"


def select_ready(worker, pending, active, readiness, now, heavy_launches, workers, heavy_workers,
                 why=None):
    """Highest priority work; serialize expensive WCS fallback, not SP1.

    ``heavy_launches`` son los instantes en que salieron los últimos pesados:
    los que aún crecen reservan su memoria aparte.

    Con ``why`` (una lista), si no sale nada se apunta por qué no pudo salir el
    trabajo más prioritario: es lo que tiene parados los huecos libres.
    """
    if len(active) >= workers:
        return None
    occupied = set().union(*(claims(job) for _, job, _ in active.values())) if active else set()
    heavy = sum(job.tier >= 2 for _, job, _ in active.values())
    wcs = any(mode == "wcs" for _, _, mode in active.values())

    def skip(reason):
        if why is not None and not why:
            why.append(reason)

    for index, (_, job) in enumerate(pending):
        if claims(job) & occupied:
            skip("solapado")
            continue
        if job.tier >= 2:
            if heavy >= worker.tier_capacity_for(2, workers, heavy_workers):
                skip("tope_pesados")
                continue
            if not worker._room_for_another_profile():
                skip("memoria")
                continue
            growing = worker._growing_profiles(heavy_launches, now) if heavy else 0
            if growing and not worker._room_for_profiles(growing):
                skip("espaciado")
                continue
        mode = readiness.mode(job, now)
        if mode in {"ready", "wcs_parallel"} or (mode == "wcs" and not wcs):
            return index, mode
        skip("wcs" if mode == "wcs" else "paquete")
    return None


def account_idle(manifest, reason, free_slots, seconds):
    """Suma a la pasada los huecos que estuvieron libres y por qué.

    Solo cuenta entre el primer trabajo de la pasada y su cierre, que es el
    tramo sobre el que el informe calcula la ocupación: fuera de él, esperar a
    la pasada siguiente no es tiempo perdido de esta.
    """
    if not manifest or free_slots <= 0 or seconds <= 0:
        return
    if manifest.get("status") == "complete" or not manifest.get("tier_timing"):
        return
    parados = manifest.setdefault("idle_slot_seconds", {})
    parados[reason] = float(parados.get(reason, 0.0)) + free_slots * seconds


class Manifests:
    def __init__(self, worker, store, scope, diagnostic_max_hours):
        self.w, self.store, self.scope = worker, store, scope
        self.diagnostic_max_hours = diagnostic_max_hours
        self.items = {}
        self.latest = None
        self.needs_retention = False
        self.retired = {}
        # Load once, never replace a live object by its disk representation.
        for manifest in worker.retained_manifests(store):
            if manifest.get("calculation_scope", "model") == scope:
                manifest.setdefault("progress", {}).update(current_job=None, active_jobs=[])
                # Las pasadas retenidas no vuelven a prepararse: se reparan al cargarlas.
                worker._forget_missing_accumulated(store, manifest)
                self.items[str(manifest["run"])] = manifest

    def merge(self, catalog):
        run = self.w._latest_persisted_run(catalog)
        if self.latest and self.w._parse_iso(run) < self.w._parse_iso(self.latest):
            return  # A stale catalog must not roll the latest pointer backwards.
        manifest = self.w._prepare_latest_manifest(
            self.store, catalog, self.scope, self.diagnostic_max_hours,
            existing=self.items.get(run), maintain_retention=False,
        )
        self.items[run] = manifest
        self.latest = run
        # Publish the new slot immediately, but defer deleting its predecessor.
        previous = self.w.register_run_slot(self.store, manifest)
        if previous:
            self.retired[previous] = self.items.get(previous) or self.w.read_json(
                self.store, self.w.run_manifest_key(previous)) or {}
        self.needs_retention = True

    def persist(self, manifest):
        manifest["worker_heartbeat_at"] = self.w._utc_now()
        self.w._finish_status(manifest, self.store)
        self.w._persist_manifest(self.store, manifest, latest_run=self.latest)

    def retain_when_idle(self):
        if not self.needs_retention or not self.latest:
            return
        for run, manifest in list(self.retired.items()):
            self.w.delete_run(self.store, run, scope=str(manifest.get("calculation_scope", "model")))
            if manifest and manifest.get("status") != "complete":
                self.w._emit_run_report(self.store, manifest)
            self.items.pop(run, None)
            self.retired.pop(run, None)
        self.w._prune_old_runs(self.store, self.latest)
        retained = {str(m["run"]) for m in self.w.retained_manifests(self.store)}
        self.items = {run: m for run, m in self.items.items() if run in retained}
        self.needs_retention = False


class SlotLending:
    """Huecos que un worker de AROME toma prestados cuando los demás paran.

    AROME y AROME-IFS corren en procesos aparte del mismo contenedor, cada uno
    con sus huecos fijos, y sus pasadas llegan con horas de diferencia: el
    29/09 AROME-IFS pasó una hora a 2/2 con cientos de trabajos en cola
    mientras los 7 huecos de AROME estaban parados y el contenedor usaba
    2,6 GB de 30. Cada proceso deja en un fichero local cuántos trabajos lleva
    y cuántos esperan; mientras todos los demás estén parados del todo, uno
    puede subir de ``workers`` a ``max_workers``. En cuanto otro tiene trabajo,
    el prestatario deja de admitir por encima de lo suyo y los prestados
    terminan sin cortarse; el dueño arranca con sus huecos desde el principio.

    Los perfiles convectivos suben lo mismo que los huecos si hay tope propio;
    el freno por memoria de cada perfil sigue aplicándose igual.
    """

    # Un proceso que no reescribe su estado en este tiempo ha muerto o se ha
    # colgado: no debe bloquear el préstamo para siempre.
    STALE_S = 90.0
    REWRITE_S = 20.0
    READ_EVERY_S = 2.0

    def __init__(self, name, workers, max_workers, heavy_workers, directory=None):
        self.workers = workers
        self.max_workers = max(workers, max_workers)
        self.heavy_workers = heavy_workers
        base = directory or os.getenv("METEOLABX_FORECAST_SLOTS_DIR", "").strip()
        self.dir = Path(base) if base else Path(tempfile.gettempdir()) / "meteolabx-forecast-slots"
        self.path = self.dir / f"{name}.json"
        self._written = None
        self._written_at = 0.0
        self._idle = (float("-inf"), False)
        self.borrowing = False

    @property
    def capacity(self):
        return self.max_workers

    def publish(self, active, pending):
        """Deja el estado propio, solo si cambia o para renovar la marca."""
        estado = (int(active), int(pending) > 0)
        ahora = _wall_clock()
        if estado == self._written and ahora - self._written_at < self.REWRITE_S:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            temporal = self.path.with_suffix(".tmp")
            temporal.write_text(json.dumps(
                {"active": estado[0], "pending": estado[1], "at": ahora, "pid": os.getpid()}))
            temporal.replace(self.path)
        except OSError:
            logger.warning("No se pudo publicar el estado de huecos en %s", self.path, exc_info=True)
            return
        self._written, self._written_at = estado, ahora

    def others_idle(self):
        ahora = _wall_clock()
        leido, parados = self._idle
        if ahora - leido < self.READ_EVERY_S:
            return parados
        parados = True
        try:
            ficheros = [f for f in self.dir.glob("*.json") if f != self.path]
        except OSError:
            ficheros = []
        for fichero in ficheros:
            try:
                estado = json.loads(fichero.read_text())
            except (OSError, ValueError):
                continue  # A medio escribir o borrado: cuenta en la vuelta siguiente.
            if ahora - float(estado.get("at", 0)) > self.STALE_S:
                continue
            if int(estado.get("active", 0)) > 0 or estado.get("pending"):
                parados = False
                break
        self._idle = (ahora, parados)
        return parados

    def limits(self):
        """Huecos y tope de perfiles que se pueden usar ahora."""
        prestar = self.max_workers > self.workers and self.others_idle()
        if prestar != self.borrowing:
            self.borrowing = prestar
            if prestar:
                logger.info("Préstamo de huecos: los demás workers están parados; se sube de %d a %d.",
                            self.workers, self.max_workers)
            else:
                logger.info("Préstamo de huecos: otro worker tiene trabajo; se vuelve a %d.", self.workers)
        if not prestar:
            return self.workers, self.heavy_workers
        extra = self.max_workers - self.workers
        return self.max_workers, self.heavy_workers + extra if self.heavy_workers > 0 else 0


def _ecmwf_loop(max_frames, stop, interval):
    from server.services.ecmwf_forecast import run_cycle
    while not stop.is_set():
        espera = interval
        try:
            resultado = run_cycle(max_frames=max(0, max_frames))
            logger.info("Ciclo ECMWF terminado: %s", resultado)
            # Con trabajo en marcha no se espera: el tope de frames acota cada
            # ciclo, no el ritmo. Con los 60 s entre ciclos, la 12Z del 29/09
            # tardó 33 min, de los que ~25 fueron esperas entre plazos que se
            # calculaban en 2-4 s.
            if resultado.get("frames_published") and not resultado.get("waiting_reason"):
                espera = 1
        except Exception:
            logger.exception("ECMWF falló; AROME continúa.")
        stop.wait(espera)


def run_watch(w, args, stop):
    token = str(w.get_settings().arome_api_key or "").strip()
    if not token:
        raise RuntimeError("METEOLABX_AROME_API_KEY no está configurada.")
    store = w.get_forecast_store()
    registry = Manifests(w, store, w.forecast_calculation_scope(), max(0, args.diagnostic_max_hours))
    readiness = Readiness(w)
    workers = max(1, args.workers)
    lending = SlotLending(w.current_model(), workers, max(0, getattr(args, "max_workers", 0) or 0),
                          max(0, args.heavy_workers))
    interval = max(30, args.interval)
    budget = max(0, args.cycle_budget) or interval
    active, pending = {}, []
    catalog_future = None
    next_catalog = 0.0
    admitted = 0
    # (pasada, motivo, huecos libres, instante) de la vuelta anterior.
    parado = None
    heavy_launches = collections.deque(maxlen=64)
    prefetch = None
    next_prefetch = 0.0
    background_stop = threading.Event()
    ecmwf = None
    next_maintenance = 0.0
    next_heartbeat = 0.0
    if args.ecmwf_max_frames >= 0:
        ecmwf = threading.Thread(target=_ecmwf_loop,
            args=(args.ecmwf_max_frames, background_stop, interval), name="ecmwf-watch", daemon=True)
        ecmwf.start()

    def rebuild():
        nonlocal pending
        manifests = [m for run, m in registry.items.items() if run not in registry.retired]
        queues = {m["run"]: w._jobs_for_manifest(m, max_hours=max(0, args.max_hours),
            diagnostic_max_hours=max(0, args.diagnostic_max_hours)) for m in manifests}
        pending = w._parallel_work_order(manifests, queues)
        occupied = set().union(*(claims(job) for _, job, _ in active.values())) if active else set()
        pending = [(m, j) for m, j in pending if not claims(j) & occupied]

    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="arome-catalog") as catalogs, \
             ThreadPoolExecutor(max_workers=lending.capacity, thread_name_prefix="arome-job") as jobs:
            while not stop.is_set() or active:
                now = time.monotonic()
                if stop.is_set():
                    background_stop.set()
                if parado is not None:
                    account_idle(parado[0], parado[1], parado[2], now - parado[3])
                    parado = None
                for future in [f for f in active if f.done()]:
                    manifest, job, mode = active.pop(future)
                    try:
                        future.result()
                        count = w._mark_job_finished(manifest, job)
                        logger.info("Completado %s %s: +%d frames", job.label, job.valid_time, count)
                    except Exception as exc:
                        w._mark_job_failed(manifest, job, str(exc))
                        logger.exception("No se pudo calcular %s %s", job.label, job.valid_time)
                    registry.persist(manifest)
                    rebuild()
                if not stop.is_set() and catalog_future is not None and catalog_future.done():
                    try:
                        registry.merge(catalog_future.result())
                        for manifest in registry.items.values():
                            readiness.observe(manifest, now)
                        admitted = 0
                        rebuild()
                        logger.info("Catálogo renovado sin vaciar: %d activos, %d pendientes", len(active), len(pending))
                    except Exception:
                        logger.exception("No se pudo renovar el catálogo; continúa la cola conocida.")
                        # Failed refresh must not starve known work at max_tasks.
                        admitted = 0
                    catalog_future = None
                    next_catalog = now + (budget if pending or active else interval)
                limit = args.max_tasks > 0 and admitted >= args.max_tasks
                if not stop.is_set() and catalog_future is None and (now >= next_catalog or limit):
                    catalog_future = catalogs.submit(w.catalog_payload, token)
                limit_workers, limit_heavy = lending.limits()
                if not stop.is_set():
                    if (prefetch is None or not prefetch.is_alive()) and now >= next_prefetch and pending:
                        prefetch = w._start_package_prefetch([j for _, j in pending], background_stop)
                        next_prefetch = now + 45
                    # Refresh does not drain running tasks or block dispatch.
                    # max_tasks is an admission quota per refreshed catalog.
                    why = []
                    while not limit and not stop.is_set():
                        why = []
                        selected = select_ready(w, pending, active, readiness, now, heavy_launches,
                                                limit_workers, limit_heavy, why)
                        if selected is None:
                            break
                        index, mode = selected
                        manifest, job = pending.pop(index)
                        timeout = max(1, args.derived_timeout if job.tier else args.native_timeout)
                        w._mark_job_started(manifest, job, timeout, slots=limit_workers, mode=mode)
                        w._persist_manifest(store, manifest, latest_run=registry.latest)
                        future = jobs.submit(w._run_isolated_job, job, timeout, scheduled=True)
                        active[future] = (manifest, job, mode)
                        logger.info("Procesando RUN %s %s nivel=%d vía=%s activos=%d/%d", job.run,
                                    job.valid_time, job.tier, mode, len(active), limit_workers)
                        if job.tier >= 2:
                            heavy_launches.append(now)
                        admitted += 1
                        limit = args.max_tasks > 0 and admitted >= args.max_tasks
                    libres = limit_workers - len(active)
                    if libres > 0 and registry.latest:
                        # Sin nada en cola es que Météo-France no ha publicado
                        # más horas (o el catálogo aún no las ha visto); con
                        # cola, lo que frena al trabajo más prioritario.
                        motivo = ("sin_trabajo" if not pending else "cuota" if limit
                                  else why[0] if why else "otro")
                        pasada = pending[0][0] if pending else registry.items.get(registry.latest)
                        parado = (pasada, motivo, libres, now)
                lending.publish(len(active), 0 if stop.is_set() else len(pending))
                if not active and registry.latest and now >= next_maintenance:
                    # Prefetch may still be reading/writing these same packages.
                    if prefetch is None or not prefetch.is_alive():
                        try:
                            registry.retain_when_idle()
                            rebuild()  # Do not resurrect jobs from pruned runs.
                            # Sin condicionarlo a la cola: entre pasadas casi
                            # siempre queda alguna hora esperando publicación, y
                            # la caché de páginas se quedaba sin liberar.
                            from server.services.grib_page_cache import release_completed_grib_cache
                            w._report_grib_release(release_completed_grib_cache())
                            w._trim_worker_memory()
                        except Exception:
                            logger.exception("No se pudo completar el mantenimiento; continúa AROME.")
                        next_maintenance = now + interval
                if registry.latest and now >= next_heartbeat:
                    w.write_json(store, w.worker_state_key(), {"version": 2, "last_run": registry.latest,
                        "heartbeat_at": w._utc_now(), "workers": limit_workers})
                    logger.info("Planificador: %d/%d activos, %d pendientes, catálogo_en_curso=%s",
                                len(active), limit_workers, len(pending), catalog_future is not None)
                    next_heartbeat = now + 30
                # Never busy-spin while all hours are waiting for publication.
                if stop.is_set():
                    time.sleep(0.1)
                else:
                    stop.wait(0.25)
    finally:
        background_stop.set()
        lending.publish(0, 0)
        # Transfers have finite HTTP timeouts; do not delete files under them.
        if prefetch is not None:
            prefetch.join(timeout=0.1)
        if ecmwf is not None:
            ecmwf.join()  # Finish the current bounded ECMWF cycle before exiting.
