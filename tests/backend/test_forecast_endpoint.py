"""Contrato HTTP del visor AROME."""

from __future__ import annotations

from fastapi.testclient import TestClient
import json
import struct

from server.config import Settings, get_settings
from server.main import create_app
from server.routers import forecast
from server.services.arome_forecast import (
    AROME_MODEL_GRID_BOUNDS,
    AROME_MODEL_GRID_SHAPE,
    _boundary_payload,
    _main_cycle_runs,
    _place_local_array_in_model_grid,
)
from server.services.forecast_store import (
    LATEST_MANIFEST_KEY,
    LocalObjectStore,
    frame_key,
    new_manifest,
    mark_available,
    register_run_slot,
    write_grid,
    write_json,
)
from server.services import arome_forecast
from tabs.arome_forecast import CoverageCatalog


def _app_with_key() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        arome_api_key="test-token",
        ranking_refresh_enabled=False,
    )
    return TestClient(app)


def test_forecast_catalog_requires_server_key():
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        arome_api_key="",
        ranking_refresh_enabled=False,
    )
    with TestClient(app) as client:
        response = client.get("/v1/forecast/arome/catalog")
    assert response.status_code == 503


def test_forecast_catalog_returns_connected_products(monkeypatch):
    monkeypatch.setattr(
        forecast,
        "catalog_payload",
        lambda token: {"model": "AROME France", "products": {"shear-06": {}}},
    )
    with _app_with_key() as client:
        response = client.get("/v1/forecast/arome/catalog")
    assert response.status_code == 200
    assert response.json()["products"] == {"shear-06": {}}


def test_forecast_catalog_skips_one_missing_optional_coverage(monkeypatch):
    from datetime import datetime, timezone

    run = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(arome_forecast, "PRODUCTS", {"available": {}, "missing": {}})

    def fake_context(_token, product_id):
        if product_id == "missing":
            raise arome_forecast.AromeError("Cobertura no anunciada")
        return (
            {"kind": "native", "vmax": 10.0, "unit": "K"},
            None,
            None,
            {},
            run,
            [run],
        )

    monkeypatch.setattr(arome_forecast, "_product_context", fake_context)
    arome_forecast._catalog_payload_cached.cache_clear()
    try:
        payload = arome_forecast._catalog_payload_cached("token", 1)
    finally:
        arome_forecast._catalog_payload_cached.cache_clear()

    assert payload["products"]["available"]["run"] == "2026-08-24T12:00:00Z"
    assert payload["unavailable_products"] == {"missing": "Cobertura no anunciada"}


def test_forecast_frame_returns_png_and_metadata(monkeypatch):
    monkeypatch.setattr(
        forecast,
        "frame_png",
        lambda token, product, valid_time, vertical_kind="height", level=10: (
            b"\x89PNG\r\n\x1a\n",
            {"X-AROME-Max": "28.4", "X-AROME-Run": "2026-08-24T03:00:00Z"},
        ),
    )
    with _app_with_key() as client:
        response = client.get(
            "/v1/forecast/arome/frames.png",
            params={"product": "shear-06", "valid_time": "2026-08-24T12:00:00Z"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-arome-max"] == "28.4"
    assert response.content.startswith(b"\x89PNG")


def test_forecast_grid_returns_binary_native_cells(monkeypatch):
    monkeypatch.setattr(
        forecast,
        "frame_grid",
        lambda token, product, valid_time, vertical_kind="height", level=10: (
            b"\x02\x00\x00\x00{}" + b"\x00" * 8,
            {"X-AROME-Max": "12.0"},
        ),
    )
    with _app_with_key() as client:
        response = client.get(
            "/v1/forecast/arome/frames.grid",
            params={"product": "ship", "valid_time": "2026-08-24T12:00:00Z"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.meteolabx.arome-grid"
    assert response.content.startswith(b"\x02\x00\x00\x00{}")


def test_forecast_grid_prefers_precomputed_frame(monkeypatch, tmp_path):
    run = "2026-08-24T12:00:00Z"
    valid = "2026-08-24T13:00:00Z"
    metadata = {
        "product": "ship", "width": 1, "height": 1, "unit": "",
        "run": run, "valid_time": valid, "maximum": 1.5,
    }
    header = json.dumps(metadata).encode()
    content = struct.pack("<I", len(header)) + header + struct.pack("<f", 1.5)
    store = LocalObjectStore(tmp_path)
    manifest = new_manifest(run, [valid])
    mark_available(manifest, "ship", valid)
    write_grid(store, frame_key(run, "ship", valid), content)
    write_json(store, LATEST_MANIFEST_KEY, manifest)
    monkeypatch.setattr(forecast, "get_forecast_store", lambda: store)
    monkeypatch.setattr(
        forecast,
        "frame_grid",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cálculo bajo demanda")),
    )

    with _app_with_key() as client:
        response = client.get(
            "/v1/forecast/arome/frames.grid",
            params={"product": "ship", "valid_time": valid},
        )
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["x-meteolabx-precomputed"] == "1"


def test_forecast_grid_prefers_persisted_native_frame(monkeypatch, tmp_path):
    run = "2026-08-24T12:00:00Z"
    valid = "2026-08-24T13:00:00Z"
    metadata = {
        "product": "temperature-2m", "width": 1, "height": 1, "unit": "°C",
        "run": run, "valid_time": valid, "maximum": 25.0,
    }
    header = json.dumps(metadata).encode()
    content = struct.pack("<I", len(header)) + header + struct.pack("<f", 25.0)
    store = LocalObjectStore(tmp_path)
    manifest = new_manifest(run, [valid])
    mark_available(manifest, "temperature-2m", valid)
    write_grid(store, frame_key(run, "temperature-2m", valid), content)
    write_json(store, LATEST_MANIFEST_KEY, manifest)
    monkeypatch.setattr(forecast, "get_forecast_store", lambda: store)
    monkeypatch.setattr(
        forecast,
        "frame_grid",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("descarga nativa bajo demanda")),
    )

    with _app_with_key() as client:
        response = client.get(
            "/v1/forecast/arome/frames.grid",
            params={"product": "temperature-2m", "valid_time": valid, "run": run},
        )
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["x-meteolabx-precomputed"] == "1"


def test_forecast_catalog_exposes_retained_run_slots(monkeypatch, tmp_path):
    store = LocalObjectStore(tmp_path)
    runs = ("2026-08-24T06:00:00Z", "2026-08-24T12:00:00Z")
    for run in runs:
        product = {"run": run, "run_local": run, "valid_times": [run], "unit": "°C", "vmax": 42}
        manifest = new_manifest(run, [run], catalog_products={"temperature-2m": product})
        mark_available(manifest, "temperature-2m", run)
        write_json(store, forecast.run_manifest_key(run), manifest)
        register_run_slot(store, manifest)
    write_json(store, LATEST_MANIFEST_KEY, manifest)
    monkeypatch.setattr(forecast, "get_forecast_store", lambda: store)
    monkeypatch.setattr(
        forecast,
        "catalog_payload",
        lambda _token: {"model": "AROME France", "resolution": "0,025°", "domain": {}, "products": {"temperature-2m": product}},
    )

    with _app_with_key() as client:
        response = client.get("/v1/forecast/arome/catalog")
    assert response.status_code == 200
    assert [item["run"] for item in response.json()["runs"]] == list(reversed(runs))
    assert response.json()["runs"][0]["products"]["temperature-2m"]["available_times"] == [runs[-1]]


def test_forecast_progress_reads_only_persisted_manifests(monkeypatch, tmp_path):
    store = LocalObjectStore(tmp_path)
    manifest = new_manifest(
        "2026-08-24T18:00:00Z",
        ["2026-08-24T19:00:00Z"],
        catalog_products={
            "temperature-2m": {
                "run": "2026-08-24T18:00:00Z",
                "valid_times": ["2026-08-24T19:00:00Z"],
            }
        },
    )
    manifest["worker_heartbeat_at"] = "2026-08-25T00:20:00Z"
    manifest["progress"].update(
        {"frames_available": 7, "frames_total": 1139, "percent": 0.61}
    )
    write_json(store, forecast.run_manifest_key(manifest["run"]), manifest)
    write_json(store, LATEST_MANIFEST_KEY, manifest)
    register_run_slot(store, manifest)
    monkeypatch.setattr(forecast, "get_forecast_store", lambda: store)
    monkeypatch.setattr(
        forecast,
        "catalog_payload",
        lambda _token: (_ for _ in ()).throw(AssertionError("consulta remota")),
    )

    with _app_with_key() as client:
        response = client.get("/v1/forecast/arome/progress")

    assert response.status_code == 200
    assert response.json()["run"] == manifest["run"]
    assert response.json()["progress"]["frames_available"] == 7
    assert response.json()["worker_heartbeat_at"] == "2026-08-25T00:20:00Z"


def test_forecast_boundaries_preserve_each_region_outline():
    payload = _boundary_payload({
        "features": [{
            "properties": {"NAMEUNIT": "Aragón"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-1.0, 40.0], [0.0, 40.0], [-1.0, 40.0]]],
            },
        }],
    })

    assert payload == [{
        "name": "Aragón",
        "level": "country",
        "rings": [[[-1.0, 40.0], [0.0, 40.0], [-1.0, 40.0]]],
    }]


def test_forecast_boundaries_preserve_administrative_level():
    payload = _boundary_payload({
        "features": [{
            "properties": {"NAMEUNIT": "Cataluña", "boundary_level": "admin1"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 40.0], [1.0, 40.0], [0.0, 40.0]]],
            },
        }],
    })

    assert payload[0]["level"] == "admin1"


def test_forecast_catalog_uses_only_main_six_hour_cycles():
    from datetime import datetime, timezone

    runs = {
        datetime(2026, 8, 24, hour, tzinfo=timezone.utc)
        for hour in (0, 3, 6, 9, 12, 15, 18, 21)
    }
    assert {run.hour for run in _main_cycle_runs(runs)} == {0, 6, 12, 18}


def test_local_crop_keeps_full_model_view_without_calculating_outside():
    import numpy as np

    west, _south, _east, north = AROME_MODEL_GRID_BOUNDS
    source = np.asarray([[7.5]], dtype=np.float32)
    result = _place_local_array_in_model_grid(
        source,
        (west, north - 0.025, west + 0.025, north),
    )

    assert result.shape == AROME_MODEL_GRID_SHAPE
    assert result.size == 803_757
    assert result[0, 0] == 7.5
    assert np.isnan(result[0, 1])


def test_wcs_catalog_keeps_hourly_precipitation_separate_from_other_periods():
    xml = b"""<Capabilities xmlns:wcs="http://www.opengis.net/wcs/2.0">
      <wcs:Contents>
        <wcs:CoverageSummary><wcs:CoverageId>TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___2026-08-24T12.00.00Z_PT1H</wcs:CoverageId></wcs:CoverageSummary>
        <wcs:CoverageSummary><wcs:CoverageId>TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE___2026-08-24T12.00.00Z_PT9H</wcs:CoverageId></wcs:CoverageSummary>
      </wcs:Contents>
    </Capabilities>"""
    catalog = CoverageCatalog(xml)
    prefix = "TOTAL_PRECIPITATION__GROUND_OR_WATER_SURFACE"
    run = next(iter(catalog.runs_for(prefix, "PT1H")))

    assert catalog.coverage_id(prefix, run, period="PT1H").endswith("_PT1H")
    assert catalog.coverage_id(prefix, run, period="PT9H").endswith("_PT9H")


def test_debug_backend_allows_local_forecast_origin(monkeypatch):
    monkeypatch.setattr(
        forecast,
        "catalog_payload",
        lambda token: {"model": "AROME France", "products": {}},
    )
    monkeypatch.setenv("METEOLABX_AROME_API_KEY", "test-token")
    monkeypatch.setenv("METEOLABX_DEBUG", "true")
    monkeypatch.setenv("METEOLABX_CORS_ORIGINS", '["https://meteolabx.com"]')
    monkeypatch.setenv("METEOLABX_RANKING_REFRESH_ENABLED", "false")
    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            response = client.get(
                "/v1/forecast/arome/catalog",
                headers={"Origin": "http://127.0.0.1:8501"},
            )
    finally:
        get_settings.cache_clear()
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8501"
