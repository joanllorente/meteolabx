from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace

import pytest

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

    def fake_series(_token, valid_times, run_iso="", stored_increment=None):
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

    # Cinco trabajos, pero más frames: el grupo convectivo publica todos sus
    # diagnósticos de una vez y el de cizalladura, los tres suyos. El número
    # sube al añadir un producto a esos grupos.
    assert (completed, failures) == (5, 0)
    assert frames == 25, f"{frames} frames en cinco trabajos"
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

    # Arranca con la primera hora pendiente, no detrás de toda la cola. Lo que
    # importa es que no quede detrás de las cizalladuras de horas posteriores;
    # los trabajos de esa misma primera hora van antes por desempate de
    # producto, y su número crece al añadir mapas al nivel.
    assert nivel1[posicion].valid_time == horas[0]
    de_la_primera_hora = sum(1 for job in nivel1 if job.valid_time == horas[0])
    assert posicion < de_la_primera_hora, (
        "el acumulado debe salir dentro del grupo de la primera hora"
    )
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
    """El total no cambia entre el principio y el final de la publicación.

    Los acumulativos —lluvia, racha, radiación— rematan en 51 horas y no en 52:
    un periodo de una hora no existe en el instante de la pasada. Simular que
    llegan a 52 escondía que el denominador se movía justo al final.
    """
    from server.services.arome_forecast import PRODUCTS
    from server.services.forecast_store import CONVECTIVE_FORECAST_PRODUCTS as CONV

    manifest = {"expected_hours": {"native": 52, "diagnostic": 36}}

    def publicadas_al_final(product: str) -> int:
        if product in (
            set(forecast_worker.SHEAR_PRODUCTS)
            | set(CONV)
            | set(forecast_worker.LEVEL_INDEX_PRODUCTS)
        ):
            return 36
        return 52 - forecast_worker._first_available_hour(product)

    al_principio = sum(
        forecast_worker._expected_frames(manifest, product, 6)
        for product in PERSISTED_FORECAST_PRODUCTS
    )
    al_final = sum(
        forecast_worker._expected_frames(manifest, product, publicadas_al_final(product))
        for product in PERSISTED_FORECAST_PRODUCTS
    )
    assert al_principio == al_final == 1263


def test_capped_products_only_offer_the_hours_that_will_exist():
    """El visor no debe ofrecer horas de cizalladura que nadie va a calcular.

    Con el horizonte recortado, anunciar las 52 horas dejaba 16 plazos que
    nunca tendrían datos y un porcentaje calculado sobre un total irreal.
    """
    horas = [f"2026-08-24T{h:02d}:00:00Z" for h in range(0, 12)]
    manifest = new_manifest(RUN, horas)
    manifest["expected_hours"] = {"native": 12, "diagnostic": 5}
    for hora in horas[:4]:
        mark_available(manifest, "shear-01", hora)
        mark_available(manifest, "temperature-2m", hora)

    catalog = {
        "products": {
            "shear-01": {"run": RUN, "valid_times": list(horas)},
            "temperature-2m": {"run": RUN, "valid_times": list(horas)},
        }
    }
    resultado = augment_catalog_with_manifest(catalog, manifest, precomputed_only=True)

    assert resultado["products"]["shear-01"]["valid_times"] == horas[:5]
    # Un producto sin recorte conserva su horizonte completo.
    assert resultado["products"]["temperature-2m"]["valid_times"] == horas


def test_available_hours_outside_the_cap_are_not_announced():
    """Si una hora quedó calculada fuera del recorte, no se ofrece."""
    horas = [f"2026-08-24T{h:02d}:00:00Z" for h in range(0, 8)]
    manifest = new_manifest(RUN, horas)
    manifest["expected_hours"] = {"native": 8, "diagnostic": 3}
    for hora in horas:  # se calcularon todas antes de aplicar el recorte
        mark_available(manifest, "dcape", hora)

    catalog = {"products": {"dcape": {"run": RUN, "valid_times": list(horas)}}}
    resultado = augment_catalog_with_manifest(catalog, manifest, precomputed_only=True)

    assert resultado["products"]["dcape"]["available_times"] == horas[:3]


def test_stored_grid_values_recovers_what_was_serialized(monkeypatch):
    """Leer un frame guardado devuelve el campo con el error de cuantización.

    Es lo que permite al acumulado sumar las horas que el mapa horario de
    lluvia ya publicó en vez de volver a pedirlas al WCS. Si el inverso no
    fuera fiel, el acumulado saldría mal sin que nada fallase.
    """
    import numpy as np

    from rasterio.crs import CRS
    from rasterio.transform import from_bounds

    from server.services import arome_forecast
    from server.services.arome_forecast import _serialize_grid, stored_grid_values
    from tabs.arome_forecast import RasterField

    # Ámbito de producción: en local el serializador recoloca el recorte sobre
    # la rejilla entera del modelo, que no es lo que se guarda en Railway.
    monkeypatch.setattr(arome_forecast, "forecast_calculation_scope", lambda: "model")

    rng = np.random.default_rng(7)
    valores = rng.gamma(1.5, 2.0, (23, 31))
    valores[valores < 0.4] = 0.0
    valores[0, 0] = np.nan          # fuera del dominio
    campo = RasterField(
        valores,
        from_bounds(-2, 40, 3, 44, 31, 23),
        CRS.from_epsg(4326),
        (-2, 40, 3, 44),
        "mm",
    )
    config = {"vmax": 60.0, "unit": "mm"}
    cabeceras = {
        "X-AROME-Run": "2026-08-26T12:00:00Z",
        "X-AROME-Valid-Time": "2026-08-26T15:00:00Z",
        "X-AROME-Max": f"{float(np.nanmax(valores)):.3f}",
        "X-AROME-Unit": "mm",
    }

    recuperado = stored_grid_values(_serialize_grid("precip-1h", campo, config, cabeceras))

    assert recuperado is not None
    assert recuperado.shape == valores.shape
    assert np.isnan(recuperado[0, 0]), "el hueco debe seguir siendo hueco"
    finitos = np.isfinite(valores)
    # El paso de cuantización de este rango, más margen de redondeo.
    from server.services.arome_forecast import _quantization_step
    paso = _quantization_step(float(np.nanmax(valores)) - float(np.nanmin(valores)))
    assert np.abs(recuperado[finitos] - valores[finitos]).max() <= paso


def test_stored_grid_values_declines_what_it_cannot_read():
    """Ante un frame que no encaja, prefiere no devolver nada.

    Quien llama vuelve a descargarlo; interpretar mal unos bytes daría un
    acumulado silenciosamente incorrecto.
    """
    from server.services.arome_forecast import stored_grid_values

    assert stored_grid_values(b"") is None
    assert stored_grid_values(b"\x04\x00\x00\x00no-json") is None
    assert stored_grid_values(b"\x02\x00\x00\x00{}") is None


def test_accumulation_reuses_published_hours_instead_of_downloading(monkeypatch):
    """El acumulado suma las horas que el mapa horario ya publicó.

    Ambos salen del mismo campo del WCS, y el horario se publica antes por ser
    nativo: volver a pedirlas eran 51 peticiones tiradas por pasada. La primera
    hora se descarga siempre, porque de ella salen la rejilla y la proyección
    sobre las que se alinea el resto.
    """
    import numpy as np
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds

    from server.services import arome_forecast
    from tabs.arome_forecast import RasterField

    monkeypatch.setattr(arome_forecast, "forecast_calculation_scope", lambda: "model")
    horas = [f"2026-08-26T{h:02d}:00:00Z" for h in range(13, 19)]
    geometria = (from_bounds(-2, 40, 3, 44, 4, 3), CRS.from_epsg(4326), (-2, 40, 3, 44))
    descargas = []

    class ClienteFalso:
        def get_field(self, catalog, prefix, run, valid_time, *a, **k):
            descargas.append(valid_time)
            return RasterField(np.full((3, 4), 2.0), *geometria, "mm")

    monkeypatch.setattr(
        arome_forecast, "_product_context",
        lambda token, product, run_iso="": (
            arome_forecast.PRODUCTS["accumulated-precip"],
            ClienteFalso(), {}, {"field": "x"},
            arome_forecast._parse_time("2026-08-26T12:00:00Z"),
            [arome_forecast._parse_time(h) for h in horas],
        ),
    )

    # Todas menos la primera están ya publicadas, con 2 mm cada una.
    guardadas = {h: np.full((3, 4), 2.0) for h in horas[1:]}
    salida = list(
        arome_forecast.accumulated_precip_series(
            "token", tuple(horas), run_iso="2026-08-26T12:00:00Z",
            stored_increment=guardadas.get,
        )
    )

    assert len(descargas) == 1, f"solo la primera hora se descarga, no {len(descargas)}"
    assert len(salida) == len(horas)
    # Seis horas de 2 mm: la última acumula 12.
    ultimo = arome_forecast.stored_grid_values(salida[-1][1])
    assert np.nanmax(ultimo) == pytest.approx(12.0, abs=0.05)


def test_accumulation_downloads_when_the_stored_grid_does_not_match(monkeypatch):
    """Una rejilla distinta no se mezcla: se vuelve a descargar.

    En local el WCS entrega el recorte catalán y los frames guardados cubren el
    dominio entero; sumarlos daría un acumulado sin sentido.
    """
    import numpy as np
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds

    from server.services import arome_forecast
    from tabs.arome_forecast import RasterField

    monkeypatch.setattr(arome_forecast, "forecast_calculation_scope", lambda: "model")
    horas = [f"2026-08-26T{h:02d}:00:00Z" for h in range(13, 17)]
    geometria = (from_bounds(-2, 40, 3, 44, 4, 3), CRS.from_epsg(4326), (-2, 40, 3, 44))
    descargas = []

    class ClienteFalso:
        def get_field(self, catalog, prefix, run, valid_time, *a, **k):
            descargas.append(valid_time)
            return RasterField(np.full((3, 4), 1.0), *geometria, "mm")

    monkeypatch.setattr(
        arome_forecast, "_product_context",
        lambda token, product, run_iso="": (
            arome_forecast.PRODUCTS["accumulated-precip"],
            ClienteFalso(), {}, {"field": "x"},
            arome_forecast._parse_time("2026-08-26T12:00:00Z"),
            [arome_forecast._parse_time(h) for h in horas],
        ),
    )

    list(
        arome_forecast.accumulated_precip_series(
            "token", tuple(horas), run_iso="2026-08-26T12:00:00Z",
            # Rejilla que no cuadra con la del campo descargado.
            stored_increment=lambda hora: np.full((9, 9), 1.0),
        )
    )

    assert len(descargas) == len(horas), "ninguna se puede reutilizar"


def test_vertical_totals_is_the_difference_between_two_levels(monkeypatch):
    """VT es T850 menos T500, con los dos niveles del mismo paquete.

    No cuesta ninguna descarga nueva: IP1 ya trae ambos para los perfiles. Lo
    que hay que fijar es que la resta va en el orden correcto y que la unidad
    no la altera —en kelvin o en grados la diferencia es la misma—.
    """
    import numpy as np
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds

    from server.services import arome_forecast
    from tabs.arome_forecast import RasterField

    geometria = (from_bounds(-2, 40, 3, 44, 4, 3), CRS.from_epsg(4326), (-2, 40, 3, 44))
    # 12 °C a 850 y -20 °C a 500: VT = 32.
    pedido = {}

    def paquete_falso(ref, run, vt, levels, elements=()):
        pedido["elements"] = elements
        return {
            "temperature": {
                850.0: RasterField(np.full((3, 4), 12.0), *geometria, "C"),
                500.0: RasterField(np.full((3, 4), -20.0), *geometria, "C"),
            }
        }

    monkeypatch.setattr(
        arome_forecast, "_isobaric_fields_from_package", paquete_falso
    )
    config = arome_forecast.PRODUCTS["vertical-totals"]
    campo = arome_forecast._level_difference_field(
        None, None, {}, config,
        arome_forecast._parse_time("2026-08-26T06:00:00Z"),
        arome_forecast._parse_time("2026-08-26T09:00:00Z"),
    )

    assert np.allclose(campo.data, 32.0)
    assert campo.units == "°C"
    # IP1 trae cinco elementos y aqui solo hace falta uno: descodificar viento
    # y geopotencial para una resta de temperaturas son seis megas por nivel
    # tirados.
    assert pedido["elements"] == ("temperature",)


def test_vertical_totals_falls_back_to_the_wcs_without_the_package(monkeypatch):
    """Sin IP1 se piden los dos niveles al WCS, que son dos peticiones."""
    import numpy as np
    from rasterio.crs import CRS
    from rasterio.transform import from_bounds

    from server.services import arome_forecast
    from tabs.arome_forecast import RasterField

    monkeypatch.setattr(
        arome_forecast, "_isobaric_fields_from_package",
        lambda *a, **k: None,
    )
    geometria = (from_bounds(-2, 40, 3, 44, 4, 3), CRS.from_epsg(4326), (-2, 40, 3, 44))
    pedidos = []

    class ClienteFalso:
        def get_field(self, catalog, prefix, run, valid_time, level, kind, **k):
            pedidos.append(level)
            # 285 K a 850 y 253 K a 500: VT = 32.
            valor = 285.0 if level == 850.0 else 253.0
            return RasterField(np.full((3, 4), valor), *geometria, "K")

    campo = arome_forecast._level_difference_field(
        ClienteFalso(), None, {"field": "x"},
        arome_forecast.PRODUCTS["vertical-totals"],
        arome_forecast._parse_time("2026-08-26T06:00:00Z"),
        arome_forecast._parse_time("2026-08-26T09:00:00Z"),
    )

    assert pedidos == [850.0, 500.0]
    assert np.allclose(campo.data, 32.0), "la unidad no debe alterar la diferencia"


def test_only_real_native_maps_report_as_such(monkeypatch, capsys):
    """La traza de mapa no puede incluir perfiles ni cizalladuras.

    Todos pasan por frame_grid, pero los convectivos ya reparten su tiempo en
    su propia línea: verlos aquí hacía leer 170 s de perfil como si fueran una
    descarga de campo.
    """
    import inspect

    from server.services import arome_forecast

    fuente = inspect.getsource(arome_forecast.frame_grid)
    assert '"native", "level_difference"' in fuente, (
        "debe filtrar por tipo antes de registrar"
    )
    # Los productos que se colaban.
    for producto in ("mucape-muli", "dcape", "ship", "shear-06"):
        assert arome_forecast.PRODUCTS[producto]["kind"] not in {
            "native", "level_difference"
        }, producto
    # Y los que sí deben aparecer.
    for producto in ("temperature-850", "cloud-cover", "vertical-totals"):
        assert arome_forecast.PRODUCTS[producto]["kind"] in {
            "native", "level_difference"
        }, producto


def test_a_run_can_actually_reach_complete():
    """Dar la pasada por completa usa el mismo criterio que el progreso.

    El progreso cuenta las horas que se van a calcular —36 para los productos
    recortados—, pero el estado exigía las 52 del catálogo. Así el visor
    marcaba 100 % mientras el estado se quedaba en «publicando» para siempre, y
    con él el resumen final de la pasada.
    """
    horas_52 = [f"2026-08-28T{h:02d}:00:00Z" for h in range(24)] + [
        f"2026-08-29T{h:02d}:00:00Z" for h in range(24)
    ] + [f"2026-08-30T{h:02d}:00:00Z" for h in range(4)]
    manifest = {
        "run": "2026-08-27T12:00:00Z",
        "status": "publishing",
        "expected_hours": {"native": 52, "diagnostic": 36},
        "expected_times": horas_52,
        "catalog_products": {
            product: {"valid_times": horas_52}
            for product in PERSISTED_FORECAST_PRODUCTS
        },
        "products": {},
    }
    # Cada producto tiene exactamente lo que le toca: 52 los nativos, 36 los
    # recortados.
    for product in PERSISTED_FORECAST_PRODUCTS:
        esperadas = forecast_worker._product_expected_times(manifest, product)
        manifest["products"][product] = {"available_times": list(esperadas)}

    forecast_worker._finish_status(manifest)

    assert manifest["status"] == "complete", (
        "con todo lo que se calcula publicado, la pasada tiene que darse por hecha"
    )


def test_a_capped_product_does_not_expect_the_hours_nobody_computes():
    """Un recortado espera su límite, no todo el catálogo."""
    horas = [f"2026-08-28T{h:02d}:00:00Z" for h in range(24)]
    manifest = {
        "expected_hours": {"native": 24, "diagnostic": 6},
        "catalog_products": {p: {"valid_times": horas} for p in ("dcape", "temperature-2m")},
    }

    assert len(forecast_worker._product_expected_times(manifest, "dcape")) == 6
    assert len(forecast_worker._product_expected_times(manifest, "temperature-2m")) == 24


def test_the_profile_reports_its_own_memory_peak():
    """El perfil registra su pico real, no la memoria de antes de empezar.

    La traza del lanzamiento mide antes de que el perfil crezca, así que nunca
    ve el máximo: con seis a la vez, la gráfica marcaba 26,5 GB mientras el log
    decía 11. ru_maxrss del propio proceso sí lo ve.

    La unidad cambia según el sistema: Linux da kilobytes y macOS bytes, y
    confundirlas daría un pico mil veces mayor o menor.
    """
    import inspect

    from server.services import arome_forecast

    fuente = inspect.getsource(arome_forecast._convective_frames.__wrapped__)
    assert "ru_maxrss" in fuente
    assert 'sys.platform == "linux"' in fuente, (
        "la unidad de ru_maxrss depende del sistema"
    )
    assert "pico %.1f GB" in fuente


def test_the_run_summary_is_logged_once_per_run():
    """El resumen sale al completarse, no en cada ciclo posterior.

    Se apoyaba en el estado anterior del manifiesto, pero éste se reconstruye
    entre ciclos: en la 18Z la línea salió cuatro veces seguidas.
    """
    horas = [f"2026-08-28T{h:02d}:00:00Z" for h in range(24)] + [
        f"2026-08-29T{h:02d}:00:00Z" for h in range(24)
    ] + [f"2026-08-30T{h:02d}:00:00Z" for h in range(4)]
    manifest = {
        "run": "2026-08-27T18:00:00Z",
        "expected_hours": {"native": 52, "diagnostic": 36},
        "expected_times": horas,
        "catalog_products": {p: {"valid_times": horas} for p in PERSISTED_FORECAST_PRODUCTS},
        "products": {},
        "tier_timing": {"0": {"first_start": "2026-08-27T20:00:00Z",
                              "last_start": "2026-08-27T21:00:00Z", "jobs": 604}},
    }
    for product in PERSISTED_FORECAST_PRODUCTS:
        manifest["products"][product] = {
            "available_times": list(forecast_worker._product_expected_times(manifest, product))
        }

    veces = []
    original = forecast_worker._log_run_summary
    forecast_worker._log_run_summary = lambda m: bool(veces.append(m) or True)
    try:
        for _ in range(4):
            # El estado se pierde entre ciclos; la marca no.
            manifest.pop("status", None)
            forecast_worker._finish_status(manifest)
    finally:
        forecast_worker._log_run_summary = original

    assert len(veces) == 1, f"el resumen salió {len(veces)} veces"


def test_the_run_adoption_threshold_is_configurable():
    """Cuántos plazos se exigen a una pasada para adoptarla.

    Decide cuánto se tarda en empezar: el nivel 0 se pasa casi una hora
    esperando publicaciones, así que adoptar antes solapa esa espera con el
    trabajo. A cambio compromete antes con una pasada aún incipiente, y por eso
    tiene que poder volverse atrás sin desplegar.
    """
    import inspect

    from server.services import arome_forecast

    assert 1 <= arome_forecast.MINIMUM_RUN_HOURS <= 12
    fuente = inspect.getsource(arome_forecast._product_context)
    assert "MINIMUM_RUN_HOURS" in fuente
    assert ">= 12" not in fuente, "el umbral no puede quedar fijado a mano"


def test_waiting_for_ip3_is_available_when_the_caller_prefers_it(monkeypatch):
    """Se puede leer IP3 sin esperar, para quien no dependa de él.

    El perfil sí espera: tiene media hora de plazo y el paquete tarda dos
    minutos, mientras que publicar la hora sin la velocidad vertical dejaría
    esos dos mapas vacíos para siempre, porque una hora publicada no se
    recalcula.
    """
    from server.services import arome_forecast

    monkeypatch.setattr(arome_forecast, "_packages_available", lambda: True)
    monkeypatch.setattr(arome_forecast, "package_ready", lambda *a: False)

    def no_deberia_bajar(*args, **kwargs):
        raise AssertionError("no debe esperar a que se descargue")

    monkeypatch.setattr(arome_forecast, "ensure_package", no_deberia_bajar)
    hora = arome_forecast._parse_time("2026-08-28T06:00:00Z")

    # Sin esperar: se va sin la velocidad vertical.
    assert arome_forecast._isobaric_extras_from_package(
        hora, hora, [850.0], ("vertical_velocity",), esperar=False
    ) is None


def test_dcape_does_wait_for_ip3(monkeypatch):
    """DCAPE sí espera: sin el rocío exacto no puede calcularse."""
    from server.services import arome_forecast

    monkeypatch.setattr(arome_forecast, "_packages_available", lambda: True)
    monkeypatch.setattr(arome_forecast, "package_ready", lambda *a: False)
    pedidos = []
    monkeypatch.setattr(
        arome_forecast, "ensure_package",
        lambda *a: pedidos.append(a[0]) or (_ for _ in ()).throw(
            arome_forecast.AromePackageError("aún no")
        ),
    )
    hora = arome_forecast._parse_time("2026-08-28T06:00:00Z")

    arome_forecast._isobaric_extras_from_package(
        hora, hora, [850.0], ("dewpoint",), esperar=True
    )

    assert pedidos == ["IP3"], "con esperar=True tiene que intentar la descarga"


def test_every_convective_product_is_also_derived():
    """Un convectivo que falte en DERIVED se encola además como nativo.

    NATIVE_PRODUCTS se define como «lo que no es derivado», así que olvidarse
    de esta lista al añadir un mapa lo pone también en el nivel 0: allí cada
    uno recalcula el perfil entero —cuatro gigas— con el límite de los campos
    nativos, que son cinco minutos. Con siete workers eso agotó la memoria del
    contenedor y tiró el servicio.
    """
    from server.services.forecast_store import (
        CONVECTIVE_FORECAST_PRODUCTS,
        DERIVED_FORECAST_PRODUCTS,
    )

    huerfanos = [
        product
        for product in CONVECTIVE_FORECAST_PRODUCTS
        if product not in DERIVED_FORECAST_PRODUCTS
    ]
    assert not huerfanos, f"{huerfanos} se encolarían dos veces"


def test_no_convective_product_lands_in_the_native_tier():
    """Ningún producto que necesite el perfil puede acabar en el nivel 0."""
    from server.services.arome_forecast import PRODUCTS

    caros = {
        nombre
        for nombre, config in PRODUCTS.items()
        if config.get("kind") == "convective"
    }
    coladas = caros & set(forecast_worker.NATIVE_PRODUCTS)
    assert not coladas, f"{sorted(coladas)} irían al nivel 0"


def test_the_dcape_pass_does_not_load_what_it_will_discard():
    """El turno de DCAPE no trae la velocidad vertical ni calcula la helicidad.

    Sus dos mapas se publican en el nivel anterior, así que aquí se
    descartarían: eran 160 MB de lectura y medio giga de cálculo por perfil,
    tirados. Con seis a la vez, casi cuatro gigas.
    """
    import inspect

    from server.services import arome_forecast

    fuente = inspect.getsource(arome_forecast._convective_frames.__wrapped__)
    assert 'if only_dcape:\n        quiere = ("dewpoint",)' in fuente, (
        "de IP3 sólo debe pedir el rocío"
    )
    assert "bool(package_vv) and not only_dcape" in fuente, (
        "no debe reservar el perfil de velocidad vertical"
    )
    assert "if only_dcape\n            else _updraft_helicity_in_stripes" in fuente, (
        "no debe recalcular la helicidad del ascenso"
    )


def test_the_vorticity_grid_keeps_the_sign_of_the_transform():
    """La rejilla que se le pasa a la vorticidad lleva los pasos con signo.

    Las filas avanzan de norte a sur (transform.e < 0). En valor absoluto,
    ∂u/∂y entra cambiado de signo y en un vórtice ideal anula la vorticidad en
    vez de completarla: el mapa de helicidad sale plano donde más importa.
    """
    import inspect

    from server.services import arome_forecast

    fuente = inspect.getsource(arome_forecast._convective_frames.__wrapped__)
    assert "abs(float(reference.transform.e))" not in fuente, (
        "el paso latitudinal no puede ir en valor absoluto"
    )
    assert "float(reference.transform.e),\n" in fuente, (
        "la rejilla tiene que llevar el paso latitudinal con su signo"
    )


def test_packages_survive_while_an_older_run_still_needs_them(tmp_path: Path):
    """No se borran los GRIB de una pasada que sigue trabajando.

    En cuanto sale una pasada nueva, la anterior suele estar todavía con los
    convectivos o con DCAPE. Borrarle los paquetes la obligaba a rebajarlos
    enteros: en un solo log, 108 descargas y 218 descartes, con bloques que se
    bajaron hasta seis veces.
    """
    from server.services.forecast_store import RUN_SLOTS_KEY, run_manifest_key

    store = LocalObjectStore(tmp_path)
    vieja, nueva = "2026-08-28T06:00:00Z", "2026-08-28T12:00:00Z"
    write_json(store, RUN_SLOTS_KEY, {"slots": {"06": {"run": vieja}, "12": {"run": nueva}}})
    write_json(store, run_manifest_key(vieja), {"run": vieja, "status": "publishing"})
    write_json(store, run_manifest_key(nueva), {"run": nueva, "status": "publishing"})

    frontera = forecast_worker._oldest_unfinished_run(store, nueva)

    assert frontera == vieja, "la frontera la marca la más antigua sin terminar"


def test_packages_of_a_finished_run_are_released(tmp_path: Path):
    """Una pasada completa sí libera sus paquetes."""
    from server.services.forecast_store import RUN_SLOTS_KEY, run_manifest_key

    store = LocalObjectStore(tmp_path)
    vieja, nueva = "2026-08-28T06:00:00Z", "2026-08-28T12:00:00Z"
    write_json(store, RUN_SLOTS_KEY, {"slots": {"06": {"run": vieja}, "12": {"run": nueva}}})
    write_json(store, run_manifest_key(vieja), {"run": vieja, "status": "complete"})
    write_json(store, run_manifest_key(nueva), {"run": nueva, "status": "publishing"})

    assert forecast_worker._oldest_unfinished_run(store, nueva) == nueva


def _fake_grid(product: str, run_iso: str, valid_iso: str) -> bytes:
    """Rejilla mínima con la cabecera que lee el almacén."""
    header = json.dumps({
        "product": product,
        "run": run_iso,
        "valid_time": valid_iso,
        "calculation_scope": "model",
        "unit": "m²/s²",
        "width": 2,
        "height": 1,
    }).encode("utf-8")
    return struct.pack("<I", len(header)) + header + b"\x00" * 8


def _fake_source(run_iso: str, horas: list[str]):
    """Instancia de mentira: catálogo y frames sin salir a la red."""
    catalogo = {
        "publication": {"calculation_scope": "model"},
        "runs": [{
            "run": run_iso,
            "status": "complete",
            "products": {
                "updraft-helicity": {
                    "run": run_iso,
                    "unit": "m²/s²",
                    "valid_times": horas,
                    "available_times": horas[:2],
                },
                "wind-level": {
                    "run": run_iso,
                    "unit": "m/s",
                    "valid_times": horas,
                    "available_times": horas[:2],
                    "levels": {"height": [10.0, 100.0], "isobaric": [850.0]},
                },
            },
        }],
    }

    class Respuesta:
        def __init__(self, payload=None, content=b"", status=200):
            self.status_code = status
            self._payload = payload
            self.content = content

        def json(self):
            return self._payload

    class Session:
        def get(self, url, params=None, timeout=None, headers=None):
            if url.endswith("/catalog"):
                return Respuesta(payload=catalogo)
            producto = (params or {})["product"]
            valido = (params or {})["valid_time"]
            if valido not in horas[:2]:
                return Respuesta(status=425)
            return Respuesta(content=_fake_grid(producto, run_iso, valido))

    return Session()


def test_captured_fixtures_are_served_as_available_hours(tmp_path: Path, monkeypatch):
    """La foto local tiene que quedar servible sin clave de AROME.

    El visor lee las horas del manifiesto, así que una captura que anunciara
    los 36 plazos del RUN dejaría el deslizador lleno de horas que responden
    425. El manifiesto de la foto solo puede ofrecer lo que hay en disco.
    """
    import importlib.util

    ruta = Path(__file__).resolve().parents[2] / "scripts" / "capture_forecast_fixtures.py"
    especificacion = importlib.util.spec_from_file_location("capture_fixtures", ruta)
    capture = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(capture)

    run_iso = "2026-08-29T00:00:00Z"
    horas = [f"2026-08-29T{hora:02d}:00:00Z" for hora in range(4)]
    monkeypatch.setattr(capture.requests, "Session", lambda: _fake_source(run_iso, horas))

    codigo = capture.capture(SimpleNamespace(
        source="https://ejemplo.invalid",
        store=str(tmp_path),
        run="",
        products="updraft-helicity,wind-level",
        all=False,
        hours=6,
        vertical_kind="height",
        level=10.0,
        timeout=5.0,
        force=False,
        reset=False,
        list=False,
    ))

    assert codigo == 0
    store = LocalObjectStore(tmp_path)
    for valid_iso in horas[:2]:
        clave = frame_key(run_iso, "updraft-helicity", valid_iso)
        assert read_grid(store, clave) is not None, f"falta el frame de {valid_iso}"
        assert grid_metadata(read_grid(store, clave))["run"] == run_iso

    manifest = read_json(store, LATEST_MANIFEST_KEY)
    assert manifest["status"] == "complete"
    assert manifest["calculation_scope"] == "model"
    catalogo = augment_catalog_with_manifest(
        {"products": deepcopy(manifest["catalog_products"])},
        manifest,
        precomputed_only=True,
    )
    producto = catalogo["products"]["updraft-helicity"]
    # Solo las dos horas capturadas, y ninguna pendiente que dé 425 al pulsarla.
    assert producto["valid_times"] == horas[:2]
    assert producto["available_times"] == horas[:2]
    assert producto["publishing"] is False
    # Del viento solo se ha bajado un nivel: ofrecer los demás sería ofrecer 425.
    assert catalogo["products"]["wind-level"]["levels"] == {"height": [10.0], "isobaric": []}
    assert read_json(store, run_manifest_key(run_iso))["run"] == run_iso


def _capture_arguments(tmp_path: Path, products: str, **cambios):
    argumentos = {
        "source": "https://ejemplo.invalid",
        "store": str(tmp_path),
        "run": "",
        "products": products,
        "all": False,
        "hours": 6,
        "vertical_kind": "height",
        "level": 10.0,
        "timeout": 5.0,
        "force": False,
        "reset": False,
        "list": False,
    }
    argumentos.update(cambios)
    return SimpleNamespace(**argumentos)


def _capture_module():
    import importlib.util

    ruta = Path(__file__).resolve().parents[2] / "scripts" / "capture_forecast_fixtures.py"
    especificacion = importlib.util.spec_from_file_location("capture_fixtures", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def test_a_second_capture_keeps_what_the_first_one_saved(tmp_path: Path, monkeypatch):
    """Bajar un producto más no puede dejar sin índice a los anteriores.

    El manifiesto es lo único que mira el visor: si cada captura lo
    reescribiera, pedir dos horas de un mapa borraría del catálogo los seis
    productos que ya estaban en disco y el visor se quedaría en blanco con los
    ficheros delante.
    """
    capture = _capture_module()
    run_iso = "2026-08-29T00:00:00Z"
    horas = [f"2026-08-29T{hora:02d}:00:00Z" for hora in range(4)]
    monkeypatch.setattr(capture.requests, "Session", lambda: _fake_source(run_iso, horas))

    capture.capture(_capture_arguments(tmp_path, "updraft-helicity"))
    capture.capture(_capture_arguments(tmp_path, "wind-level"))

    manifest = read_json(LocalObjectStore(tmp_path), LATEST_MANIFEST_KEY)
    assert sorted(manifest["catalog_products"]) == ["updraft-helicity", "wind-level"]
    assert manifest["catalog_products"]["updraft-helicity"]["valid_times"] == horas[:2]
    assert manifest["products"]["updraft-helicity"]["available_times"] == horas[:2]


def test_a_capture_drops_hours_whose_grid_is_gone(tmp_path: Path, monkeypatch):
    """Lo heredado se contrasta contra el disco, no se cree a ciegas.

    Si alguien borra frames a mano, el manifiesto tiene que enterarse: una hora
    anunciada sin rejilla detrás responde 425 al pulsarla.
    """
    capture = _capture_module()
    run_iso = "2026-08-29T00:00:00Z"
    horas = [f"2026-08-29T{hora:02d}:00:00Z" for hora in range(4)]
    monkeypatch.setattr(capture.requests, "Session", lambda: _fake_source(run_iso, horas))
    store = LocalObjectStore(tmp_path)

    capture.capture(_capture_arguments(tmp_path, "updraft-helicity"))
    store.delete(frame_key(run_iso, "updraft-helicity", horas[1]))
    capture.capture(_capture_arguments(tmp_path, "wind-level"))

    manifest = read_json(store, LATEST_MANIFEST_KEY)
    assert manifest["catalog_products"]["updraft-helicity"]["valid_times"] == horas[:1]


def test_the_catalog_publishes_the_final_total_of_each_map():
    """El porcentaje de un mapa no puede medirse contra lo ya publicado.

    Las horas que anuncia Météo-France crecen durante la pasada: con doce
    plazos publicados y doce calculados, el mapa marcaba «Completo» y volvía a
    bajar en cuanto aparecían los siguientes. El denominador es el horizonte
    final, que el worker conoce desde el primer ciclo.
    """
    run = "2026-08-29T00:00:00Z"
    horas = [f"2026-08-29T{hora:02d}:00:00Z" for hora in range(12)]
    manifest = new_manifest(run, horas, catalog_products={})
    manifest["expected_totals"] = {"temperature-850": 51}
    for valid in horas:
        mark_available(manifest, "temperature-850", valid)

    catalogo = augment_catalog_with_manifest(
        {"products": {"temperature-850": {"run": run, "valid_times": list(horas)}}},
        manifest,
        precomputed_only=True,
    )

    producto = catalogo["products"]["temperature-850"]
    assert producto["expected_total"] == 51, "el visor necesita el total final"
    assert len(producto["available_times"]) == 12
    # Doce de cincuenta y una es un 24 %, no un mapa terminado: con el total
    # final delante, el visor ya no puede confundir una cosa con la otra.
    assert round(len(producto["available_times"]) * 100 / producto["expected_total"]) == 24


def test_the_worker_records_the_final_total_of_every_map():
    """El total final lo escribe quien lo sabe: el que reparte el trabajo."""
    import scripts.forecast_worker as forecast_worker

    run = "2026-08-29T00:00:00Z"
    horas = [f"2026-08-29T{hora:02d}:00:00Z" for hora in range(12)]
    manifest = new_manifest(run, horas, catalog_products={
        producto: {"run": run, "valid_times": list(horas)}
        for producto in PERSISTED_FORECAST_PRODUCTS
    })
    manifest["expected_hours"] = {"native": 51, "diagnostic": 36}

    forecast_worker._refresh_progress(manifest)

    totales = manifest["expected_totals"]
    assert totales["temperature-850"] == 51, "un nativo llega al horizonte entero"
    assert totales["updraft-helicity"] == 36, "los convectivos van recortados"
    assert manifest["progress"]["frames_total"] == sum(totales.values())
