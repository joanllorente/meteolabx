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
    ECMWF_PRODUCT_REVISIONS,
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
    assert frame_key(RUN, "ecmwf-mslp-theta-e-850", VALID, model="ecmwf") == (
        "forecast/models/ecmwf/runs/20260830T000000Z/ecmwf-mslp-theta-e-850"
        "/20260830T120000Z.grid.gz"
    )
    assert latest_manifest_key("ecmwf") == "forecast/models/ecmwf/manifests/latest.json"
    assert run_slots_key("ecmwf") == "forecast/models/ecmwf/manifests/slots.json"


def test_an_unknown_model_is_refused_instead_of_writing_somewhere_odd():
    with pytest.raises(ValueError):
        frame_key(RUN, "ecmwf-mslp-theta-e-850", VALID, model="../otro")


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
        "ecmwf-mslp-theta-e-850",
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
        "ecmwf-mslp-theta-e-850": {"run": RUN, "valid_times": [VALID], "vmax": 600.0, "unit": "dam"}
    }
    mark_available(manifest, "ecmwf-mslp-theta-e-850", VALID)
    write_grid(store, frame_key(RUN, "ecmwf-mslp-theta-e-850", VALID, model="ecmwf"), _frame_bytes())
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
            params={"product": "ecmwf-mslp-theta-e-850", "valid_time": VALID},
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
            params={"product": "ecmwf-mslp-theta-e-850", "valid_time": "2026-08-30T15:00:00Z"},
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
    producto = payload["products"]["ecmwf-mslp-theta-e-850"]
    assert producto["available_times"] == [VALID]
    assert payload["runs"][0]["run"] == RUN
    # El catálogo de ECMWF no puede arrastrar productos de AROME.
    assert set(payload["products"]) == {"ecmwf-mslp-theta-e-850"}


def test_the_progress_of_one_model_ignores_the_other():
    _publish_frame()
    with _client() as client:
        ecmwf = client.get("/v1/forecast/ecmwf/progress").json()
        arome = client.get("/v1/forecast/arome/progress").json()
    assert ecmwf["run"] == RUN
    assert arome["run"] is None


@pytest.mark.parametrize('product,level,parameters,has_vectors', [
    ('relative-vorticity-500', '500', {'vo', 'gh', 'sp'}, False),
    ('q-vectors-700', '700', {'t', 'gh', 'sp'}, True),
])
def test_synoptic_products_package_matching_grid_and_vectors(monkeypatch, product, level, parameters, has_vectors):
    requested = []
    bounds = (-10.125, 39.875, .125, 50.125)
    monkeypatch.setattr(ecmwf_forecast, 'domain_bounds', lambda *_: bounds)

    def field(run, step, selector, domain):
        requested.append(selector)
        west, south, east, north = domain
        width, height = round((east-west)*4), round((north-south)*4)
        x, y = np.meshgrid(np.arange(width), np.arange(height))
        fields = {
            'u': 10. + y*.2, 'v': x*.1, 'vo': 1e-5 + x*1e-7,
            't': 270. + x*.1, 'gh': 3000. + y*y*.1 + x*y*.05,
            'sp': np.full((height, width), 100000.),
        }
        fields['sp'][height//2, width//2] = 40000.
        return fields[selector['param']], domain

    monkeypatch.setattr(ecmwf_forecast, '_field', field)
    payload, _ = ecmwf_forecast.frame_payload(product, ecmwf_forecast.parse_run(RUN), 12)
    header_len = struct.unpack('<I', payload[:4])[0]
    header = json.loads(payload[4:4+header_len])
    assert header['bounds'] == list(bounds)
    assert header['width'] == header['height'] == 41
    assert header['has_vectors'] is has_vectors
    assert header['has_overlay'] is True
    assert header['overlay_unit'] == 'dam'
    assert header['unit'] == ecmwf_forecast.PRODUCTS[product]['unit']
    assert {selector['param'] for selector in requested} == parameters
    assert all(selector.get('levelist') == level for selector in requested if selector['param'] != 'sp')
    body = payload[4+header_len:]
    size = 41*41
    for index, array in enumerate(header['arrays']):
        encoded = np.frombuffer(body[index*size*2:(index+1)*size*2], dtype=np.uint8)
        codes = encoded[:size].astype(np.uint16)*256 + encoded[size:]
        assert codes.reshape(41, 41)[20, 20] == 0, 'el punto bajo tierra debe quedar sin dato'
        assert np.count_nonzero(codes) > size*.8
        if array['name'] == 'overlay':
            # Primera celda del recorte en el campo con halo: 3° por lado en
            # vorticidad; en Q, 6° de latitud y 24° de longitud.
            row, col = (12, 12) if product == 'relative-vorticity-500' else (24, 96)
            decoded = array['offset'] + (float(codes[0])-1)*array['step']
            assert decoded == pytest.approx((3000 + row*row*.1 + col*row*.05)*.1, abs=array['step'])
    assert set(ecmwf_forecast.PRODUCTS) == set(persisted_products('ecmwf'))


def test_worker_upgrades_old_vorticity_frames_only_once(monkeypatch):
    store = get_forecast_store()
    manifest = new_manifest(RUN, [VALID], model='ecmwf')
    for product in ecmwf_forecast.PRODUCTS:
        mark_available(manifest, product, VALID)
    write_json(store, run_manifest_key(RUN, model='ecmwf'), manifest)
    run = ecmwf_forecast.parse_run(RUN)
    monkeypatch.setattr(ecmwf_forecast, 'latest_run', lambda: run)
    monkeypatch.setattr(ecmwf_forecast, 'catalog_payload', lambda _: {
        'products': {product: {'valid_times': [VALID]} for product in ecmwf_forecast.PRODUCTS}
    })
    calls = []
    def frame(product, run, step, domain='europe'):
        calls.append((product, domain))
        return _frame_bytes(), {}
    monkeypatch.setattr(ecmwf_forecast, 'frame_payload', frame)
    revisados = [product for product in ecmwf_forecast.PRODUCTS if product in ECMWF_PRODUCT_REVISIONS]
    # Los dominios nuevos no tienen aún ningún frame: se calculan todos sus
    # mapas. En Europa, solo los revisados.
    esperados = [
        (product, domain)
        for product in ecmwf_forecast.PRODUCTS
        for domain in ecmwf_forecast.DOMAINS
        if domain != 'europe' or product in revisados
    ]
    for _ in range(len(esperados) + 1):
        ecmwf_forecast.run_cycle(max_frames=1)
    assert calls == esperados
    for product in revisados:
        revision = ECMWF_PRODUCT_REVISIONS[product]
        assert f'{product}--rev{revision}/' in frame_key(RUN, product, VALID, model='ecmwf')
        assert f'scopes/australia/runs/' in frame_key(RUN, product, VALID, model='ecmwf', scope='australia')


def _decode_values(payload):
    header_len = struct.unpack('<I', payload[:4])[0]
    header = json.loads(payload[4:4+header_len])
    size = header['width'] * header['height']
    array = header['arrays'][0]
    body = payload[4+header_len:]
    encoded = np.frombuffer(body[:size*2], dtype=np.uint8)
    codes = encoded[:size].astype(np.uint16)*256 + encoded[size:]
    values = array['offset'] + (codes.astype(float)-1)*array['step']
    return header, np.where(codes == 0, np.nan, values).reshape(header['height'], header['width'])


def test_six_hour_precipitation_subtracts_the_previous_accumulation(monkeypatch):
    bounds = (-10.125, 39.875, .125, 50.125)
    monkeypatch.setattr(ecmwf_forecast, 'domain_bounds', lambda *_: bounds)

    def field(run, step, selector, domain):
        west, south, east, north = domain
        shape = (round((north-south)*4), round((east-west)*4))
        if selector['param'] == 'tp':
            # Metros de agua acumulados desde el inicio: 1 mm por hora.
            return np.full(shape, step * 1e-3), domain
        return np.full(shape, 101300.) * selector.get('scale', 1), domain

    monkeypatch.setattr(ecmwf_forecast, '_field', field)
    run = ecmwf_forecast.parse_run(RUN)
    header, values = _decode_values(ecmwf_forecast.frame_payload('ecmwf-precip-6h', run, 12)[0])
    assert header['unit'] == 'mm'
    assert np.nanmedian(values) == pytest.approx(6.0, abs=.05)
    with pytest.raises(ecmwf_forecast.EcmwfError):
        ecmwf_forecast.frame_payload('ecmwf-precip-6h', run, 3)
    assert ecmwf_forecast.product_steps('ecmwf-precip-6h', [0, 3, 6, 9]) == [6, 9]
    assert ecmwf_forecast.product_steps('ecmwf-jet-300', [0, 3]) == [0, 3]


def test_omega_is_shown_positive_upwards_and_masked_below_ground(monkeypatch):
    bounds = (-10.125, 39.875, .125, 50.125)
    monkeypatch.setattr(ecmwf_forecast, 'domain_bounds', lambda *_: bounds)

    def field(run, step, selector, domain):
        west, south, east, north = domain
        shape = (round((north-south)*4), round((east-west)*4))
        if selector['param'] == 'w':
            return np.full(shape, -0.5), domain  # ω negativo: el aire sube
        if selector['param'] == 'sp':
            presion = np.full(shape, 100000.)
            presion[shape[0]//2, shape[1]//2] = 60000.
            return presion, domain
        return np.full(shape, 3000.) * selector.get('scale', 1), domain

    monkeypatch.setattr(ecmwf_forecast, '_field', field)
    header, values = _decode_values(
        ecmwf_forecast.frame_payload('ecmwf-omega-700', ecmwf_forecast.parse_run(RUN), 12)[0]
    )
    assert np.nanmedian(values) == pytest.approx(0.5, abs=.01)
    assert np.isnan(values[header['height']//2, header['width']//2])


def test_catalog_announces_each_product_revision_for_the_frame_url(monkeypatch):
    # Los frames son inmutables en el navegador: la revisión tiene que llegar
    # al visor para que la meta en la URL y no reutilice un mapa antiguo.
    _publish_frame()
    monkeypatch.setitem(ECMWF_PRODUCT_REVISIONS, "ecmwf-mslp-theta-e-850", 3)
    with _client() as client:
        catalogo = client.get("/v1/forecast/ecmwf/catalog").json()
    assert catalogo["products"]["ecmwf-mslp-theta-e-850"]["frame_revision"] == 3
    assert catalogo["runs"][0]["products"]["ecmwf-mslp-theta-e-850"]["frame_revision"] == 3


def test_each_domain_has_its_own_catalog_and_frames():
    # El manifiesto es uno por pasada: los mapas de otros dominios van con
    # `@dominio`, y el catálogo de cada dominio los ve con su clave de siempre.
    store = get_forecast_store()
    manifest = new_manifest(RUN, [VALID], model="ecmwf")
    producto = "ecmwf-mslp-theta-e-850"
    manifest["catalog_products"] = {
        producto: {"run": RUN, "valid_times": [VALID], "vmax": 60.0, "unit": "°C"},
        f"{producto}@australia": {"run": RUN, "valid_times": [VALID], "vmax": 60.0, "unit": "°C"},
    }
    mark_available(manifest, f"{producto}@australia", VALID)
    write_grid(store, frame_key(RUN, producto, VALID, model="ecmwf", scope="australia"), _frame_bytes())
    write_json(store, run_manifest_key(RUN, model="ecmwf"), manifest)
    write_json(store, latest_manifest_key("ecmwf"), manifest)
    register_run_slot(store, manifest)
    with _client(forecast_precomputed_only=True) as client:
        australia = client.get("/v1/forecast/ecmwf/catalog", params={"domain": "australia"}).json()
        europa = client.get("/v1/forecast/ecmwf/catalog").json()
        frame = client.get("/v1/forecast/ecmwf/frames.grid", params={
            "product": producto, "valid_time": VALID, "run": RUN, "domain": "australia",
        })
        otro = client.get("/v1/forecast/ecmwf/frames.grid", params={
            "product": producto, "valid_time": VALID, "run": RUN,
        })
        malo = client.get("/v1/forecast/ecmwf/catalog", params={"domain": "luna"})
    assert australia["domain"]["id"] == "australia"
    assert australia["runs"][0]["products"][producto]["available_times"] == [VALID]
    assert not europa["runs"] or europa["runs"][0]["products"][producto].get("available_times", []) == []
    assert {item["id"] for item in australia["domains"]} == set(ecmwf_forecast.DOMAINS)
    assert frame.status_code == 200
    # El frame de Australia no se sirve como si fuera el de Europa.
    assert otro.status_code != 200 or otro.content != frame.content
    assert malo.status_code == 422


def test_dominio_sin_calcular_hereda_el_catalogo_de_europa():
    from server.routers.ecmwf import _domain_view

    manifiesto = {
        "run": "2026-09-27T00:00:00Z",
        "catalog_products": {"ecmwf-temperature-850": {"valid_times": ["a", "b"]}},
        "products": {"ecmwf-temperature-850": {"available_times": ["a"]}},
    }
    vista = _domain_view(manifiesto, "north-america")
    assert vista["catalog_products"] == manifiesto["catalog_products"]
    # Los mapas de Europa no cuentan como calculados en el otro dominio.
    assert vista["products"] == {}

    manifiesto["catalog_products"]["ecmwf-temperature-850@north-america"] = {"valid_times": ["a"]}
    assert _domain_view(manifiesto, "north-america")["catalog_products"] == {
        "ecmwf-temperature-850": {"valid_times": ["a"]}
    }
