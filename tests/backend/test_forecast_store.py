from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace

from server.services.forecast_store import (
    LATEST_MANIFEST_KEY,
    PERSISTED_FORECAST_PRODUCTS,
    RUN_SLOTS_KEY,
    LocalObjectStore,
    augment_catalog_with_manifest,
    frame_key,
    grid_metadata,
    mark_available,
    new_manifest,
    register_run_slot,
    run_manifest_key,
    retained_manifests,
    read_compressed_grid,
    read_grid,
    read_json,
    write_grid,
    write_json,
)
from scripts.forecast_worker import pending_hours
from scripts import forecast_worker
from server.services import arome_forecast as server_arome
from tabs import arome_forecast


RUN = "2026-08-24T12:00:00Z"
H1 = "2026-08-24T13:00:00Z"
H2 = "2026-08-24T14:00:00Z"


def _grid() -> bytes:
    metadata = {
        "product": "ship",
        "width": 1,
        "height": 1,
        "unit": "",
        "run": RUN,
        "valid_time": H1,
        "maximum": 1.25,
    }
    header = json.dumps(metadata).encode()
    return struct.pack("<I", len(header)) + header + struct.pack("<f", 1.25)


def test_local_store_roundtrips_compressed_grid_and_manifest(tmp_path: Path):
    store = LocalObjectStore(tmp_path)
    key = frame_key(RUN, "ship", H1)
    write_grid(store, key, _grid())
    compressed = read_compressed_grid(store, key)
    assert compressed is not None
    assert compressed.startswith(b"\x1f\x8b")
    assert compressed != _grid()
    restored = read_grid(store, key)
    assert restored == _grid()
    assert grid_metadata(restored)["maximum"] == 1.25

    manifest = new_manifest(RUN, [H1, H2])
    mark_available(manifest, "ship", H1)
    write_json(store, LATEST_MANIFEST_KEY, manifest)
    assert read_json(store, LATEST_MANIFEST_KEY)["products"]["ship"]["available_times"] == [H1]


def test_manifest_augments_catalog_without_hiding_provider_hours():
    manifest = new_manifest(RUN, [H1, H2])
    mark_available(manifest, "ship", H1)
    catalog = {"products": {"ship": {"run": RUN, "valid_times": [H1, H2]}}}
    result = augment_catalog_with_manifest(catalog, manifest, precomputed_only=True)
    assert result["products"]["ship"]["valid_times"] == [H1, H2]
    assert result["products"]["ship"]["available_times"] == [H1]
    assert result["products"]["ship"]["available_until"] == H1
    assert result["publication"]["precomputed_only"] is True
    assert result["publication"]["progress"]["current_job"] is None


def test_pending_hours_returns_only_unpublished_frames():
    products = {
        "ship": {"run": RUN, "valid_times": [H1, H2]},
        "dcape": {"run": RUN, "valid_times": [H1]},
    }
    manifest = new_manifest(RUN, [H1, H2])
    mark_available(manifest, "ship", H1)
    mark_available(manifest, "dcape", H1)
    assert pending_hours({"products": products}, manifest, RUN) == [H2]


def test_incremental_worker_is_idempotent(monkeypatch, tmp_path: Path):
    store = LocalObjectStore(tmp_path)
    catalog = {
        "products": {
            product: {"run": RUN, "valid_times": [H1]}
            for product in PERSISTED_FORECAST_PRODUCTS
        }
    }

    def fake_grid(_token, product, valid_time, *, run_iso=""):
        metadata = {
            "product": product, "width": 1, "height": 1, "unit": "",
            "run": RUN, "valid_time": valid_time, "maximum": 1.0,
        }
        header = json.dumps(metadata).encode()
        return struct.pack("<I", len(header)) + header + struct.pack("<f", 1.0), {}

    def fake_series(_token, valid_times, run_iso=""):
        # El acumulado no pasa por frame_grid: resuelve la pasada de una vez.
        for valid_time in valid_times:
            content, _ = fake_grid(
                _token, "accumulated-precip", valid_time, run_iso=run_iso
            )
            yield valid_time, content, {}

    monkeypatch.setattr(forecast_worker, "get_settings", lambda: SimpleNamespace(arome_api_key="token"))
    monkeypatch.setattr(forecast_worker, "get_forecast_store", lambda: store)
    monkeypatch.setattr(forecast_worker, "catalog_payload", lambda _token: catalog)
    monkeypatch.setattr(forecast_worker, "frame_grid", fake_grid)
    monkeypatch.setattr(forecast_worker, "accumulated_precip_series", fake_series)
    monkeypatch.setattr(forecast_worker, "forecast_calculation_scope", lambda: "catalonia")

    first = forecast_worker.run_incremental_cycle(max_hours=1)
    second = forecast_worker.run_incremental_cycle(max_hours=1)
    assert first["frames_completed"] == len(PERSISTED_FORECAST_PRODUCTS)
    assert first["failures"] == 0
    assert first["calculation_scope"] == "catalonia"
    assert second["frames_completed"] == 0
    manifest = read_json(store, LATEST_MANIFEST_KEY)
    assert manifest["calculation_scope"] == "catalonia"
    assert manifest["products"]["ship"]["available_times"] == [H1]


def test_worker_prioritizes_native_frames_and_groups_convective_diagnostics():
    catalog_products = {
        product: {"run": RUN, "valid_times": [H1, H2]}
        for product in PERSISTED_FORECAST_PRODUCTS
    }
    manifest = new_manifest(
        RUN,
        [H1, H2],
        catalog_products=catalog_products,
    )

    jobs = forecast_worker._jobs_for_manifest(manifest)

    assert jobs[0].tier == 0
    assert all(job.tier == 0 for job in jobs[: len(forecast_worker.NATIVE_PRODUCTS) * 2])
    convective = [job for job in jobs if job.tier == 2]
    assert len(convective) == 2
    # DCAPE sale aparte: es el unico que exige el rocio del WCS.
    assert set(convective[0].products) == set(forecast_worker.PROFILE_PRODUCTS)
    assert "dcape" not in convective[0].products
    assert convective[0].products[0] == "mucape-muli"
    assert convective[0].products[-1] == "ship"

    dcape = [job for job in jobs if job.tier == 3]
    assert len(dcape) == 2, "una hora por trabajo, en su propio nivel"
    assert all(job.products == ("dcape",) for job in dcape)


def test_shear_products_share_one_job_per_hour():
    """Las tres cizalladuras van juntas para compartir el viento a 10 m.

    Cada trabajo se ejecuta en su propio proceso, así que separarlas obligaba a
    descargar el mismo campo base una vez por producto.
    """
    catalog_products = {
        product: {"run": RUN, "valid_times": [H1, H2]}
        for product in PERSISTED_FORECAST_PRODUCTS
    }
    manifest = new_manifest(RUN, [H1, H2], catalog_products=catalog_products)

    jobs = forecast_worker._jobs_for_manifest(manifest)
    shear_jobs = [
        job for job in jobs if any(p.startswith("shear-") for p in job.products)
    ]

    assert len(shear_jobs) == 2  # una por hora, no una por producto
    for job in shear_jobs:
        assert set(job.products) == set(forecast_worker.SHEAR_PRODUCTS)
        assert job.tier == 1
    # El acumulado va en un único trabajo que cubre todas las horas: cada una
    # depende de los incrementos anteriores.
    accumulated = [job for job in jobs if job.products == ("accumulated-precip",)]
    assert len(accumulated) == 1
    assert accumulated[0].covered_times == (H1, H2)


def test_shear_set_downloads_surface_wind_once(monkeypatch):
    """El viento a 10 m se descarga una vez para las tres profundidades."""
    calls: list[float] = []

    def fake_uv(_client, _catalog, _prefixes, _run, _valid, height_m):
        calls.append(height_m)
        field = arome_forecast.RasterField(
            __import__("numpy").zeros((2, 2)), None, None, (0, 0, 1, 1), "m/s"
        )
        return field, field

    monkeypatch.setattr(server_arome, "_get_uv_height", fake_uv)
    server_arome._SURFACE_WIND_CACHE.clear()
    run = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
    valid = datetime(2026, 8, 24, 13, tzinfo=timezone.utc)

    for _ in range(3):
        server_arome._surface_wind_10m(None, None, {}, run, valid)

    assert calls == [10.0]

    # Al cambiar de hora se descarta la anterior: no se acumulan rejillas.
    server_arome._surface_wind_10m(
        None, None, {}, run, datetime(2026, 8, 24, 14, tzinfo=timezone.utc)
    )
    assert calls == [10.0, 10.0]
    assert len(server_arome._SURFACE_WIND_CACHE) == 1


def test_work_order_completes_latest_run_before_previous_ones():
    """El RUN vigente se agota entero —todos sus niveles— antes que el anterior.

    Con la prioridad por nivel, los campos nativos de las pasadas retenidas se
    colaban delante de los diagnósticos del RUN actual y ninguna terminaba.
    """
    previous_run = "2026-08-24T06:00:00Z"
    manifests = [{"run": RUN}, {"run": previous_run}]
    queues = {
        run: [
            forecast_worker.ForecastJob(run, H1, ("temperature-2m",), "model", 0),
            forecast_worker.ForecastJob(run, H1, ("shear-01",), "model", 1),
            forecast_worker.ForecastJob(
                run, H1, tuple(forecast_worker.CONVECTIVE_FORECAST_PRODUCTS), "model", 2
            ),
        ]
        for run in (RUN, previous_run)
    }

    order = forecast_worker._parallel_work_order(manifests, queues)
    runs = [job.run for _manifest, job in order]

    assert runs == [RUN] * 3 + [previous_run] * 3
    # Dentro de cada pasada se conserva el orden por dependencias.
    assert [job.tier for _manifest, job in order] == [0, 1, 2, 0, 1, 2]


def test_parallel_worker_respects_tiers_and_heavy_capacity(monkeypatch, tmp_path: Path):
    store = LocalObjectStore(tmp_path)
    catalog_products = {
        product: {"run": RUN, "valid_times": [H1, H2]}
        for product in PERSISTED_FORECAST_PRODUCTS
    }
    manifest = new_manifest(RUN, [H1, H2], catalog_products=catalog_products)
    native_jobs = [
        forecast_worker.ForecastJob(RUN, H1, ("temperature-2m",), "model", 0),
        forecast_worker.ForecastJob(RUN, H1, ("temperature-850",), "model", 0),
        forecast_worker.ForecastJob(RUN, H1, ("temperature-500",), "model", 0),
    ]
    heavy_jobs = [
        forecast_worker.ForecastJob(
            RUN, valid_time, tuple(forecast_worker.CONVECTIVE_FORECAST_PRODUCTS), "model", 2
        )
        for valid_time in (H1, H2)
    ]
    lock = threading.Lock()
    active = {0: 0, 2: 0}
    maximum = {0: 0, 2: 0}
    events = []

    def fake_isolated(job, _timeout):
        with lock:
            active[job.tier] += 1
            maximum[job.tier] = max(maximum[job.tier], active[job.tier])
            events.append(("start", job.tier))
        time.sleep(0.04)
        with lock:
            events.append(("end", job.tier))
            active[job.tier] -= 1

    monkeypatch.setattr(forecast_worker, "_run_isolated_job", fake_isolated)
    completed, frames, failures = forecast_worker._run_parallel_work(
        store=store,
        manifests=[manifest],
        queues={RUN: [*heavy_jobs, *native_jobs]},
        latest_run=RUN,
        workers=3,
        heavy_workers=1,
        max_tasks=0,
        cycle_budget_s=0,
        native_timeout_s=30,
        derived_timeout_s=30,
    )

    assert (completed, frames, failures) == (5, 17, 0)
    assert maximum[0] >= 2
    assert maximum[2] == 1
    first_heavy = events.index(("start", 2))
    assert all(tier == 0 for event, tier in events[:first_heavy] if event == "start")
    assert manifest["progress"]["active_jobs"] == []
    assert manifest["progress"]["current_job"] is None


def test_isolated_worker_reuses_an_existing_frame(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("METEOLABX_FORECAST_STORE_PATH", str(tmp_path))
    monkeypatch.setenv("METEOLABX_AROME_API_KEY", "test-token")
    store = LocalObjectStore(tmp_path)
    job = forecast_worker.ForecastJob(
        RUN, H1, ("temperature-2m",), "model", 0
    )
    write_grid(store, forecast_worker._frame_path(store, job, "temperature-2m"), _grid())

    # Usa un proceso spawn real. Si la coordinación o la configuración no son
    # serializables, esta llamada falla aunque no sea necesario consultar la API.
    forecast_worker._run_isolated_job(job, 15)


def test_arome_request_throttle_is_shared_between_threads(monkeypatch, tmp_path: Path):
    throttle_file = tmp_path / "request-throttle"
    monkeypatch.setenv("METEOLABX_AROME_REQUEST_THROTTLE_FILE", str(throttle_file))
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(arome_forecast._wait_for_api_request_slot, 0.05)
            for _ in range(2)
        ]
        for future in futures:
            future.result()
    assert time.monotonic() - started >= 0.045


def test_worker_continues_after_one_frame_failure(monkeypatch, tmp_path: Path):
    store = LocalObjectStore(tmp_path)
    products = ("temperature-2m", "temperature-850")
    catalog = {
        "products": {
            product: {"run": RUN, "valid_times": [H1]}
            for product in products
        }
    }

    def fake_grid(_token, product, valid_time, *, run_iso=""):
        del run_iso
        if product == "temperature-2m":
            raise RuntimeError("fallo controlado")
        metadata = {
            "product": product, "width": 1, "height": 1, "unit": "",
            "run": RUN, "valid_time": valid_time, "maximum": 1.0,
        }
        header = json.dumps(metadata).encode()
        return struct.pack("<I", len(header)) + header + struct.pack("<f", 1.0), {}

    monkeypatch.setattr(forecast_worker, "get_settings", lambda: SimpleNamespace(arome_api_key="token"))
    monkeypatch.setattr(forecast_worker, "get_forecast_store", lambda: store)
    monkeypatch.setattr(forecast_worker, "catalog_payload", lambda _token: catalog)
    monkeypatch.setattr(forecast_worker, "frame_grid", fake_grid)
    monkeypatch.setattr(forecast_worker, "forecast_calculation_scope", lambda: "model")

    result = forecast_worker.run_incremental_cycle(max_hours=1)
    manifest = read_json(store, LATEST_MANIFEST_KEY)

    assert result["failures"] == 1
    assert result["frames_completed"] == 1
    assert manifest["products"]["temperature-850"]["available_times"] == [H1]
    assert H1 in manifest["products"]["temperature-2m"]["errors"]
    assert manifest["progress"]["current_job"] is None
    assert manifest["progress"]["frames_available"] == 1


def test_local_scope_uses_a_separate_frame_namespace():
    model = frame_key(RUN, "ship", H1)
    local = frame_key(RUN, "ship", H1, scope="catalonia")
    assert model != local
    assert "/scopes/catalonia/" in local


def test_wind_levels_use_distinct_persistent_keys():
    height = frame_key(RUN, "wind-level", H1, vertical_kind="height", level=10)
    pressure = frame_key(RUN, "wind-level", H1, vertical_kind="isobaric", level=850)
    assert height != pressure
    assert "wind-level--height--10" in height
    assert "wind-level--isobaric--850" in pressure


def test_every_connected_product_is_persisted():
    from server.services.arome_forecast import PRODUCTS

    assert set(PERSISTED_FORECAST_PRODUCTS) == set(PRODUCTS)


def test_run_slots_keep_one_run_per_main_cycle(tmp_path: Path):
    store = LocalObjectStore(tmp_path)
    runs = [
        "2026-08-24T00:00:00Z",
        "2026-08-24T06:00:00Z",
        "2026-08-24T12:00:00Z",
        "2026-08-24T18:00:00Z",
    ]
    for run in runs:
        manifest = new_manifest(run, [H1], catalog_products={"temperature-2m": {"run": run}})
        write_json(store, forecast_worker.run_manifest_key(run), manifest)
        assert register_run_slot(store, manifest) is None

    replacement = "2026-08-25T00:00:00Z"
    manifest = new_manifest(replacement, [H2], catalog_products={"temperature-2m": {"run": replacement}})
    write_json(store, forecast_worker.run_manifest_key(replacement), manifest)
    assert register_run_slot(store, manifest) == runs[0]
    index = read_json(store, RUN_SLOTS_KEY)
    assert set(index["slots"]) == {"00", "06", "12", "18"}
    assert index["slots"]["00"]["run"] == replacement
    assert [item["run"] for item in retained_manifests(store)][0] == replacement


def test_frame_grid_v2_roundtrips_through_the_viewer_decoder(monkeypatch):
    """Recorre el formato v2 de extremo a extremo: cabecera, escalas y cuerpo.

    Reproduce el decodificador del visor para detectar cualquier desajuste de
    orden de bytes, de escala o de matrices omitidas antes de publicar.
    """
    import numpy as np
    from server.services import arome_forecast as sa

    height, width = 24, 32
    rng = np.random.default_rng(5)
    u = rng.uniform(-18.0, 18.0, (height, width))
    v = rng.uniform(-18.0, 18.0, (height, width))
    speed = np.hypot(u, v)
    # Un hueco sin dato para comprobar que la máscara sobrevive al viaje.
    speed[0, 0] = u[0, 0] = v[0, 0] = np.nan

    field = arome_forecast.RasterField(
        speed, None, None, (-1.0, 40.0, 3.0, 43.0), "m/s", vector_u=u, vector_v=v
    )
    config = {"vmin": 0.0, "vmax": 55.0, "unit": "m/s"}
    headers = {
        "X-AROME-Run": RUN,
        "X-AROME-Valid-Time": H1,
        "X-AROME-Max": "42.0",
    }
    monkeypatch.setattr(
        sa, "_computed_frame", lambda *a, **k: (field, config, headers)
    )
    monkeypatch.setattr(sa, "_model_boundary_geojson", lambda *a, **k: {"features": []})
    monkeypatch.setattr(sa, "_load_forecast_regions_geojson", lambda: {"features": []})
    monkeypatch.setattr(sa, "forecast_calculation_scope", lambda: "model")
    sa.frame_grid.cache_clear()

    content, _ = sa.frame_grid("token", "wind-level", H1, "height", 10.0)

    header_length = struct.unpack("<I", content[:4])[0]
    header = json.loads(content[4:4 + header_length])
    assert header["version"] >= 2
    # Desde el formato 3 las fronteras se sirven aparte.
    assert "boundaries" not in header
    # El escalar es el módulo del vector: debe viajar solo u y v.
    assert header["value_source"] == "hypot"
    assert [item["name"] for item in header["arrays"]] == ["u", "v"]

    body = content[4 + header_length:]
    cells = width * height
    assert len(body) == cells * 2 * len(header["arrays"])

    decoded = {}
    offset = 0
    for item in header["arrays"]:
        raw = np.frombuffer(body, dtype="u1", count=cells * 2, offset=offset)
        codes = (raw[:cells].astype(np.uint32) << 8) | raw[cells:].astype(np.uint32)
        decoded[item["name"]] = np.where(
            codes == 0,
            np.nan,
            item["offset"] + (codes.astype(float) - 1) * item["step"],
        ).reshape(height, width)
        offset += cells * 2

    valid = np.isfinite(speed)
    assert (np.isfinite(decoded["u"]) == valid).all()
    for name, original in (("u", u), ("v", v)):
        step = next(i["step"] for i in header["arrays"] if i["name"] == name)
        assert np.abs(decoded[name][valid] - original[valid]).max() <= step / 2 + 1e-9

    rebuilt = np.hypot(decoded["u"], decoded["v"])
    assert np.abs(rebuilt[valid] - speed[valid]).max() < 0.05


def test_accumulated_precip_series_matches_per_hour_path(monkeypatch):
    """Acumular en una pasada da el mismo mapa que rehacer cada hora.

    Resolver hora a hora volvía a descargar todos los incrementos anteriores
    (300 peticiones para 24 horas en lugar de 24). La suma es la misma: solo
    cambia cuándo se hace.
    """
    import numpy as np
    from datetime import timedelta
    from server.services import arome_forecast as sa

    run = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
    hours = [run + timedelta(hours=h) for h in range(1, 13)]
    rng = np.random.default_rng(4)
    increments = {hour: rng.normal(0.4, 1.2, (12, 16)) for hour in hours}
    increments[hours[0]][0, 0] = np.nan  # hueco sin dato
    downloads: list[datetime] = []

    class FakeClient:
        def get_field(self, _catalog, _prefix, _run, valid_time, *a, **k):
            downloads.append(valid_time)
            return arome_forecast.RasterField(
                increments[valid_time].copy(), None, None, (0.0, 40.0, 1.0, 41.0), "mm"
            )

    config = sa.PRODUCTS["accumulated-precip"]
    monkeypatch.setattr(
        sa,
        "_product_context",
        lambda *a, **k: (config, FakeClient(), None, {"field": "P"}, run, hours),
    )
    monkeypatch.setattr(sa, "_align", lambda _ref, field: field.data)
    monkeypatch.setattr(sa, "_load_forecast_regions_geojson", lambda: {"features": []})
    monkeypatch.setattr(sa, "_model_boundary_geojson", lambda *a, **k: {"features": []})
    monkeypatch.setattr(sa, "forecast_calculation_scope", lambda: "model")

    targets = tuple(h.isoformat().replace("+00:00", "Z") for h in hours)
    produced = {
        valid_time: content
        for valid_time, content, _ in sa.accumulated_precip_series(
            "token", targets, run_iso=run.isoformat()
        )
    }

    assert len(produced) == len(hours)
    # Una descarga por hora, no una por cada par (hora, hora anterior).
    assert len(downloads) == len(hours)

    for index, hour in enumerate(hours):
        expected = np.maximum(increments[hours[0]].astype(float), 0.0)
        for previous in hours[1:index + 1]:
            expected = expected + np.maximum(increments[previous], 0.0)

        content = produced[hour.isoformat().replace("+00:00", "Z")]
        header_length = struct.unpack("<I", content[:4])[0]
        header = json.loads(content[4:4 + header_length])
        body = content[4 + header_length:]
        cells = header["width"] * header["height"]
        scale = header["arrays"][0]
        raw = np.frombuffer(body, dtype="u1", count=cells * 2)
        codes = (raw[:cells].astype(np.uint32) << 8) | raw[cells:].astype(np.uint32)
        decoded = np.where(
            codes == 0, np.nan, scale["offset"] + (codes.astype(float) - 1) * scale["step"]
        ).reshape(header["height"], header["width"])

        finite = np.isfinite(expected)
        assert (np.isfinite(decoded) == finite).all()
        # Solo debe separarlos el redondeo del formato, nunca la acumulación.
        assert np.abs(expected[finite] - decoded[finite]).max() <= scale["step"] / 2 + 1e-9


def test_progress_total_never_shrinks_when_a_product_disappears():
    """El total no encoge si el WCS deja de listar una cobertura.

    Las coberturas aparecen y desaparecen mientras se publica una pasada, y
    copiar el catálogo en vivo tal cual hacía retroceder el porcentaje: las
    horas ya calculadas dejaban de contarse en el denominador.
    """
    live = {
        product: {"run": RUN, "valid_times": [H1, H2]}
        for product in PERSISTED_FORECAST_PRODUCTS
    }
    completo = forecast_worker._merge_catalog_products({}, live)
    assert sum(len(item["valid_times"]) for item in completo.values()) == (
        len(PERSISTED_FORECAST_PRODUCTS) * 2
    )

    # El WCS deja de publicar un producto y otro pierde una hora.
    degradado = {k: dict(v) for k, v in live.items()}
    del degradado["wind-gust"]
    degradado["ship"]["valid_times"] = [H1]

    fusionado = forecast_worker._merge_catalog_products(completo, degradado)
    assert "wind-gust" in fusionado
    assert fusionado["wind-gust"]["valid_times"] == [H1, H2]
    assert fusionado["ship"]["valid_times"] == [H1, H2]
    assert sum(len(item["valid_times"]) for item in fusionado.values()) == (
        len(PERSISTED_FORECAST_PRODUCTS) * 2
    )


def test_progress_total_grows_as_the_run_publishes_more_hours():
    """Las horas nuevas de la pasada sí se incorporan al total."""
    inicial = forecast_worker._merge_catalog_products(
        {}, {"ship": {"run": RUN, "valid_times": [H1]}}
    )
    ampliado = forecast_worker._merge_catalog_products(
        inicial, {"ship": {"run": RUN, "valid_times": [H1, H2]}}
    )
    assert ampliado["ship"]["valid_times"] == [H1, H2]


def test_isolated_job_payload_keeps_every_covered_hour(monkeypatch):
    """El trabajo debe cruzar al subproceso con todas sus horas.

    El padre marca como publicadas las horas de `covered_times`; si el hijo
    recibe el trabajo sin ellas, calcula una sola y el manifiesto da por buenas
    horas que no existen.
    """
    job = forecast_worker.ForecastJob(
        RUN, H2, ("accumulated-precip",), "model", 1, (H1, H2)
    )
    capturado = {}

    class FakeProcess:
        def __init__(self, target, args, name):
            capturado["payload"] = args[1]
            self.exitcode = 0

        def start(self): pass
        def join(self, timeout=None): pass
        def is_alive(self): return False

    class FakeContext:
        def Queue(self, maxsize=0):
            class Q:
                def get(self, timeout=None): return ("ok", "")
                def close(self): pass
            return Q()

        def Process(self, target, args, name):
            return FakeProcess(target, args, name)

    monkeypatch.setattr(
        forecast_worker.multiprocessing, "get_context", lambda _kind: FakeContext()
    )
    forecast_worker._run_isolated_job(job, 60)

    payload = capturado["payload"]
    assert payload["valid_times"] == (H1, H2)
    # Y el trabajo reconstruido en el hijo debe ser equivalente al del padre.
    assert forecast_worker.ForecastJob(**payload).covered_times == job.covered_times


def test_accumulated_precip_is_not_queued_behind_every_shear():
    """El acumulado compite con las cizalladuras, no va detrás de todas.

    La cola ordena por hora dentro de cada nivel. Al representar el trabajo
    por la última hora que cubre quedaba siempre el último de su nivel, y como
    abarca la pasada entera esa hora crece según se publica: no le llegaba
    nunca el turno.
    """
    horas = [f"2026-08-24T{hora:02d}:00:00Z" for hora in range(13, 21)]
    catalog_products = {
        product: {"run": RUN, "valid_times": horas}
        for product in PERSISTED_FORECAST_PRODUCTS
    }
    manifest = new_manifest(RUN, horas, catalog_products=catalog_products)

    order = forecast_worker._parallel_work_order(
        [manifest], {RUN: forecast_worker._jobs_for_manifest(manifest)}
    )
    nivel1 = [job for _m, job in order if job.tier == 1]
    posicion = next(
        i for i, job in enumerate(nivel1) if job.products == ("accumulated-precip",)
    )

    # Arranca con la primera hora pendiente, no detrás de toda la cola. Las
    # cizalladuras de esa misma hora van antes por desempate de producto.
    assert nivel1[posicion].valid_time == horas[0]
    assert posicion <= 1, "el acumulado no debe quedar al final de su nivel"
    assert nivel1[posicion].covered_times == tuple(horas)


def test_only_the_most_recent_runs_stay_in_the_volume(tmp_path: Path):
    """Al entrar una pasada nueva se borra la más antigua.

    Cada pasada ocupa más de un gigabyte: retener las cuatro del día desbordaba
    el volumen de 5 GB y el worker se quedaba sin poder escribir.
    """
    from server.services.forecast_store import prune_retained_runs

    store = LocalObjectStore(tmp_path)
    pasadas = [
        "2026-08-24T00:00:00Z",
        "2026-08-24T06:00:00Z",
        "2026-08-24T12:00:00Z",
        "2026-08-24T18:00:00Z",
    ]
    for run in pasadas:
        manifest = new_manifest(run, [H1])
        write_json(store, run_manifest_key(run), manifest)
        write_grid(store, frame_key(run, "ship", H1), _grid())
        register_run_slot(store, manifest)

    assert len(retained_manifests(store)) == 4

    eliminadas = prune_retained_runs(store, keep=3)

    assert eliminadas == ["2026-08-24T00:00:00Z"]
    quedan = [str(m["run"]) for m in retained_manifests(store)]
    assert quedan == pasadas[:0:-1]  # 18Z, 12Z, 06Z
    # Y sus frames dejan de ocupar sitio.
    assert read_compressed_grid(store, frame_key(pasadas[0], "ship", H1)) is None
    assert read_compressed_grid(store, frame_key(pasadas[-1], "ship", H1)) is not None


def test_pruning_keeps_everything_when_there_is_room():
    """Con menos pasadas que el límite no se borra nada."""
    from server.services.forecast_store import prune_retained_runs, retained_run_limit

    assert retained_run_limit() >= 1


def test_diagnostic_horizon_only_trims_the_expensive_products():
    """El recorte afecta a cizalladuras y convectivos, no a los nativos.

    Un campo nativo cuesta segundos y una hora convectiva varios minutos, así
    que limitar el horizonte de todos por igual sacrificaba cobertura barata.
    """
    horas = [f"2026-08-24T{hora:02d}:00:00Z" for hora in range(13, 21)]  # 8 horas
    catalog_products = {
        product: {"run": RUN, "valid_times": horas}
        for product in PERSISTED_FORECAST_PRODUCTS
    }
    manifest = new_manifest(RUN, horas, catalog_products=catalog_products)

    jobs = forecast_worker._jobs_for_manifest(manifest, diagnostic_max_hours=4)

    def horas_de(predicado):
        return {
            valid
            for job in jobs
            if predicado(job)
            for valid in job.covered_times
        }

    nativos = horas_de(lambda job: job.tier == 0)
    cizalladuras = horas_de(
        lambda job: any(p.startswith("shear-") for p in job.products)
    )
    convectivos = horas_de(lambda job: job.tier == 2)

    assert nativos == set(horas), "los nativos cubren la pasada entera"
    assert cizalladuras == set(horas[:4])
    assert convectivos == set(horas[:4])


def test_without_diagnostic_limit_everything_keeps_the_same_horizon():
    horas = [f"2026-08-24T{hora:02d}:00:00Z" for hora in range(13, 19)]
    catalog_products = {
        product: {"run": RUN, "valid_times": horas}
        for product in PERSISTED_FORECAST_PRODUCTS
    }
    manifest = new_manifest(RUN, horas, catalog_products=catalog_products)

    jobs = forecast_worker._jobs_for_manifest(manifest)
    convectivos = {
        valid for job in jobs if job.tier == 2 for valid in job.covered_times
    }

    assert convectivos == set(horas)


def test_progress_denominator_is_the_full_horizon_not_what_is_published():
    """El porcentaje no debe bajar porque AROME publique más horas.

    Contando solo lo publicado, un producto al 100 % con 20 horas caía al 62 %
    en cuanto aparecían 12 más, sin haber perdido nada.
    """
    publicadas = [f"2026-08-24T{hora:02d}:00:00Z" for hora in range(12, 20)]
    catalog_products = {
        product: {"run": RUN, "valid_times": publicadas}
        for product in PERSISTED_FORECAST_PRODUCTS
    }
    manifest = new_manifest(RUN, publicadas, catalog_products=catalog_products)
    manifest["expected_hours"] = {"native": 52, "diagnostic": 36}
    for product in PERSISTED_FORECAST_PRODUCTS:
        for hora in publicadas:
            mark_available(manifest, product, hora)

    progreso = forecast_worker._refresh_progress(manifest)

    # Ocho horas hechas de un horizonte de 52/36, no ocho de ocho.
    assert progreso["percent"] < 100.0
    esperado = sum(
        forecast_worker._expected_hours(manifest, product)
        for product in PERSISTED_FORECAST_PRODUCTS
    )
    assert progreso["frames_total"] == esperado


def test_expensive_products_use_the_shorter_horizon():
    manifest = {"expected_hours": {"native": 52, "diagnostic": 36}}
    assert forecast_worker._expected_hours(manifest, "temperature-2m") == 52
    assert forecast_worker._expected_hours(manifest, "shear-06") == 36
    assert forecast_worker._expected_hours(manifest, "dcape") == 36


def test_denominator_follows_reality_only_for_uncapped_products():
    """Un producto sin recorte cuenta lo publicado si supera lo previsto.

    Los recortados no: de ellos solo se calculan las horas del límite, así que
    contar las demás sería contar trabajo que nunca se va a hacer.
    """
    manifest = {"expected_hours": {"native": 10, "diagnostic": 6}}

    # 'temperature-2m' no está recortado: si hay 24 horas publicadas, cuentan.
    assert forecast_worker._expected_frames(manifest, "temperature-2m", 24) == 24
    assert forecast_worker._expected_frames(manifest, "temperature-2m", 3) == 10

    # 'shear-06' y 'dcape' se quedan en su límite pase lo que pase.
    assert forecast_worker._expected_frames(manifest, "shear-06", 24) == 6
    assert forecast_worker._expected_frames(manifest, "dcape", 24) == 6
    assert forecast_worker._expected_frames(manifest, "dcape", 2) == 6


def test_full_run_denominator_is_stable_while_the_model_publishes():
    """El total no cambia entre el principio y el final de la publicación."""
    manifest = {"expected_hours": {"native": 52, "diagnostic": 36}}
    al_principio = sum(
        forecast_worker._expected_frames(manifest, product, 6)
        for product in PERSISTED_FORECAST_PRODUCTS
    )
    al_final = sum(
        forecast_worker._expected_frames(manifest, product, 52)
        for product in PERSISTED_FORECAST_PRODUCTS
    )
    assert al_principio == al_final == 984
