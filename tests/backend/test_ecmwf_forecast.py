"""Separación de modelos en el almacén y contrato del visor para ECMWF."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import struct
from unittest.mock import patch

from fastapi.testclient import TestClient
import numpy as np
import pytest

from server.config import Settings, get_settings
from server.main import create_app
from server.services import ecmwf_forecast
from server.services.ecmwf_forecast import EcmwfError
from server.services.forecast_store import (
    DEFAULT_FORECAST_MODEL,
    LATEST_MANIFEST_KEY,
    RUN_SLOTS_KEY,
    frame_key,
    get_forecast_store,
    latest_manifest_key,
    mark_available,
    manifest_model,
    new_manifest,
    persisted_products,
    register_run_slot,
    run_manifest_key,
    run_slots_key,
    write_grid,
    write_json,
)

RUN = "2026-08-30T00:00:00Z"
VALID = "2026-08-30T12:00:00Z"


# --- Claves y manifiestos separados por modelo -----------------------------


def test_arome_keeps_its_keys_when_a_second_model_appears():
    """El volumen de producción ya tiene AROME escrito ahí; no se mueve.

    Cambiar su prefijo dejaría huérfanas las pasadas guardadas: invisibles
    para el visor y, peor, fuera del alcance de la poda que impide que el
    volumen se llene.
    """
    assert frame_key(RUN, "temperature-2m", VALID) == (
        "forecast/runs/20260830T000000Z/temperature-2m/20260830T120000Z.grid.gz"
    )
    assert LATEST_MANIFEST_KEY == "forecast/manifests/latest.json"
    assert RUN_SLOTS_KEY == "forecast/manifests/slots.json"
    assert run_manifest_key(RUN) == "forecast/manifests/20260830T000000Z.json"


def test_ecmwf_writes_under_its_own_namespace():
    assert frame_key(RUN, "z500-mslp", VALID, model="ecmwf") == (
        "forecast/models/ecmwf/runs/20260830T000000Z/z500-mslp"
        "/20260830T120000Z.grid.gz"
    )
    assert latest_manifest_key("ecmwf") == "forecast/models/ecmwf/manifests/latest.json"
    assert run_slots_key("ecmwf") == "forecast/models/ecmwf/manifests/slots.json"


def test_an_unknown_model_is_refused_instead_of_writing_somewhere_odd():
    with pytest.raises(ValueError):
        frame_key(RUN, "z500-mslp", VALID, model="../otro")


def test_each_model_keeps_its_own_run_slots():
    """Dos pasadas del mismo turno no pueden desalojarse entre modelos."""
    store = get_forecast_store()
    for model in (DEFAULT_FORECAST_MODEL, "ecmwf"):
        manifest = new_manifest(RUN, [VALID], model=model)
        write_json(store, run_manifest_key(RUN, model=model), manifest)
        assert register_run_slot(store, manifest) is None

    for model in (DEFAULT_FORECAST_MODEL, "ecmwf"):
        index = json.loads(store.get(run_slots_key(model)))
        assert index["slots"]["00"]["run"] == RUN
        assert index["slots"]["00"]["manifest"] == run_manifest_key(RUN, model=model)


def test_a_manifest_declares_its_model_and_only_its_products():
    manifest = new_manifest(RUN, [VALID], model="ecmwf")
    assert manifest_model(manifest) == "ecmwf"
    assert set(manifest["products"]) == set(persisted_products("ecmwf"))
    assert "temperature-2m" not in manifest["products"]
    # Los manifiestos escritos antes de separar modelos no traen el campo.
    assert manifest_model({"run": RUN}) == DEFAULT_FORECAST_MODEL


# --- Lectura del open data -------------------------------------------------


INDICE = [
    {"param": "gh", "levtype": "pl", "levelist": "500", "_offset": 10, "_length": 4},
    {"param": "gh", "levtype": "pl", "levelist": "850", "_offset": 20, "_length": 4},
    {"param": "msl", "levtype": "sfc", "_offset": 30, "_length": 4},
]


def test_the_message_is_chosen_by_parameter_and_level():
    seleccion = ecmwf_forecast._select_message(
        INDICE, {"param": "gh", "levtype": "pl", "levelist": "500"}
    )
    assert seleccion["_offset"] == 10
    # `scale` es cosa nuestra, no del índice: no debe entrar en la búsqueda.
    seleccion = ecmwf_forecast._select_message(
        INDICE, {"param": "msl", "levtype": "sfc", "scale": 0.01}
    )
    assert seleccion["_offset"] == 30


def test_a_missing_message_says_what_faltaba():
    with pytest.raises(EcmwfError, match="param=gh"):
        ecmwf_forecast._select_message(
            INDICE, {"param": "gh", "levtype": "pl", "levelist": "300"}
        )


def test_the_step_comes_from_the_gap_between_run_and_valid_time():
    run = datetime(2026, 8, 30, 0, tzinfo=timezone.utc)
    assert ecmwf_forecast.step_of(run, "2026-08-30T12:00:00Z") == 12
    assert ecmwf_forecast.step_of(run, "2026-09-01T00:00:00Z") == 48


def test_an_hour_outside_the_three_hour_step_is_refused():
    """Pedir una hora que el modelo no publica no debe bajar nada."""
    run = datetime(2026, 8, 30, 0, tzinfo=timezone.utc)
    for imposible in ("2026-08-30T13:00:00Z", "2026-08-29T21:00:00Z"):
        with pytest.raises(EcmwfError):
            ecmwf_forecast.step_of(run, imposible)


def test_the_run_is_only_looked_for_after_the_publication_delay():
    ahora = datetime(2026, 8, 30, 11, 30, tzinfo=timezone.utc)
    candidatas = ecmwf_forecast.candidate_runs(ahora)
    assert candidatas[0] == datetime(2026, 8, 30, 0, tzinfo=timezone.utc)
    assert candidatas[1] == datetime(2026, 8, 29, 18, tzinfo=timezone.utc)


def test_a_broken_domain_falls_back_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("METEOLABX_ECMWF_DOMAIN", "esto,no,son,números")
    assert ecmwf_forecast.domain_bounds() == ecmwf_forecast.DEFAULT_DOMAIN
    monkeypatch.setenv("METEOLABX_ECMWF_DOMAIN", "-20,30,10,60")
    assert ecmwf_forecast.domain_bounds() == (-20.0, 30.0, 10.0, 60.0)


# --- Contrato HTTP ---------------------------------------------------------


def _frame_bytes() -> bytes:
    from server.services.forecast_grid import pack_grid

    valores = np.linspace(500.0, 590.0, 12).reshape(3, 4)
    return pack_grid(
        "z500-mslp",
        valores,
        bounds=(-10.0, 35.0, 10.0, 50.0),
        unit="dam",
        vmin=480.0,
        vmax=600.0,
        overlay=np.full((3, 4), 1013.0),
        overlay_unit="hPa",
        metadata={
            "run": RUN,
            "valid_time": VALID,
            "forecast_model": "ecmwf",
            "boundary_scope": "ecmwf",
        },
    )


def _publish_frame() -> None:
    store = get_forecast_store()
    manifest = new_manifest(RUN, [VALID], model="ecmwf")
    manifest["catalog_products"] = {
        "z500-mslp": {"run": RUN, "valid_times": [VALID], "vmax": 600.0, "unit": "dam"}
    }
    mark_available(manifest, "z500-mslp", VALID)
    write_grid(store, frame_key(RUN, "z500-mslp", VALID, model="ecmwf"), _frame_bytes())
    write_json(store, run_manifest_key(RUN, model="ecmwf"), manifest)
    write_json(store, latest_manifest_key("ecmwf"), manifest)
    register_run_slot(store, manifest)


def _client(**ajustes) -> TestClient:
    # La ruta no existe en producción hasta que se habilita el modelo. Estos
    # tests prueban su contrato interno, por eso levantan una app opt-in.
    with patch.dict(os.environ, {"METEOLABX_ENABLE_ECMWF": "true"}):
        app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        ranking_refresh_enabled=False, **ajustes
    )
    return TestClient(app)


def test_ecmwf_is_not_public_without_the_feature_flag(monkeypatch):
    monkeypatch.delenv("METEOLABX_ENABLE_ECMWF", raising=False)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        ranking_refresh_enabled=False
    )
    with TestClient(app) as client:
        assert client.get("/v1/forecast/ecmwf/catalog").status_code == 404


def test_the_persisted_frame_is_served_without_touching_ecmwf(monkeypatch):
    def no_bajar(*_args, **_kwargs):
        raise AssertionError("Un frame ya publicado no debe descargarse otra vez.")

    monkeypatch.setattr(ecmwf_forecast, "frame_payload", no_bajar)
    _publish_frame()
    with _client() as client:
        respuesta = client.get(
            "/v1/forecast/ecmwf/frames.grid",
            params={"product": "z500-mslp", "valid_time": VALID},
        )
    assert respuesta.status_code == 200
    assert respuesta.headers["X-MeteoLabX-Precomputed"] == "1"
    largo = struct.unpack("<I", respuesta.content[:4])[0]
    cabecera = json.loads(respuesta.content[4 : 4 + largo])
    assert cabecera["forecast_model"] == "ecmwf"
    assert cabecera["unit"] == "dam"
    assert cabecera["overlay_unit"] == "hPa"
    assert cabecera["array_order"] == ["value", "overlay"]


def test_an_hour_still_pending_answers_425_instead_of_computing():
    _publish_frame()
    with _client(forecast_precomputed_only=True) as client:
        respuesta = client.get(
            "/v1/forecast/ecmwf/frames.grid",
            params={"product": "z500-mslp", "valid_time": "2026-08-30T15:00:00Z"},
        )
    assert respuesta.status_code == 425


def test_an_unknown_product_never_reaches_the_service():
    with _client() as client:
        respuesta = client.get(
            "/v1/forecast/ecmwf/frames.grid",
            params={"product": "sbcape-sbli", "valid_time": VALID},
        )
    assert respuesta.status_code == 422


def test_the_catalog_is_served_from_the_manifest_of_the_run():
    _publish_frame()
    with _client() as client:
        payload = client.get("/v1/forecast/ecmwf/catalog").json()
    assert payload["model"] == "ECMWF IFS"
    producto = payload["products"]["z500-mslp"]
    assert producto["available_times"] == [VALID]
    assert payload["runs"][0]["run"] == RUN
    # El catálogo de ECMWF no puede arrastrar productos de AROME.
    assert set(payload["products"]) == {"z500-mslp"}


def test_the_progress_of_one_model_ignores_the_other():
    _publish_frame()
    with _client() as client:
        ecmwf = client.get("/v1/forecast/ecmwf/progress").json()
        arome = client.get("/v1/forecast/arome/progress").json()
    assert ecmwf["run"] == RUN
    assert arome["run"] is None
