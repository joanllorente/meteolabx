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
    retained_manifests,
    read_grid,
    read_json,
    write_grid,
    write_json,
)
from scripts.forecast_worker import pending_hours
from scripts import forecast_worker
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

    monkeypatch.setattr(forecast_worker, "get_settings", lambda: SimpleNamespace(arome_api_key="token"))
    monkeypatch.setattr(forecast_worker, "get_forecast_store", lambda: store)
    monkeypatch.setattr(forecast_worker, "catalog_payload", lambda _token: catalog)
    monkeypatch.setattr(forecast_worker, "frame_grid", fake_grid)
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
    assert set(convective[0].products) == set(forecast_worker.CONVECTIVE_FORECAST_PRODUCTS)
    assert convective[0].products[0] == "mucape-muli"
    assert convective[0].products[-1] == "ship"


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
