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


def test_shared_fields_decode_once_and_preserve_crops(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / 'global.tif'
    values = np.arange(180 * 360, dtype='float64').reshape(180, 360)
    with rasterio.open(path, 'w', driver='GTiff', width=360, height=180,
                       count=1, dtype='float64', transform=from_origin(-180, 90, 1, 1)) as ds:
        ds.write(values, 1)
    bounds = [(-20.2, 30.1, 40.3, 75.4), (-179.9, -89.9, -80.2, -20.1)]
    expected = [ecmwf_forecast._read_message_window(path, b) for b in bounds]
    reads = []
    reader = ecmwf_forecast._read_message_window
    def read(*args):
        reads.append(args)
        return reader(*args)
    monkeypatch.setattr(ecmwf_forecast, '_read_message_window', read)
    monkeypatch.setattr(ecmwf_forecast, 'read_index', lambda *a: [{'param': 't'}])
    monkeypatch.setattr(ecmwf_forecast, '_download_message', lambda *a: path)
    monkeypatch.setattr(ecmwf_forecast, '_discard_message', lambda *a: None)
    run = ecmwf_forecast.parse_run(RUN)
    with ecmwf_forecast.shared_downloads(), ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(copy_context().run, ecmwf_forecast._field,
                   run, 12, {'param': 't', 'offset': -273.15}, b) for b in bounds * 4]
        for i, future in enumerate(futures):
            result, extent = future.result()
            np.testing.assert_array_equal(result, expected[i % 2][0] - 273.15)
            assert extent == expected[i % 2][1]
            result[:] = 0  # El consumidor no puede modificar la caché.
    assert len(reads) == 1
    assert ecmwf_forecast._shared_fields.get() is None


def test_shared_fields_nested_sessions_restore_and_cleanup():
    with ecmwf_forecast.shared_downloads() as outer:
        with pytest.raises(RuntimeError):
            with ecmwf_forecast.shared_downloads() as inner:
                assert ecmwf_forecast._shared_fields.get() is inner
                raise RuntimeError('abort')
        assert ecmwf_forecast._shared_fields.get() is outer
    assert ecmwf_forecast._shared_fields.get() is None


@pytest.mark.parametrize('raw, expected', [('1', 1), ('8', 8), ('999', 8), ('0', 1), ('bad', 4)])
def test_ecmwf_worker_limits(monkeypatch, raw, expected):
    monkeypatch.setenv('METEOLABX_ECMWF_WORKERS', raw)
    assert ecmwf_forecast.calculation_workers() == expected


def test_published_index_is_not_rechecked_when_ecmwf_rate_limits(monkeypatch):
    run = ecmwf_forecast.parse_run(RUN)
    key = (run.isoformat(), 12)
    ecmwf_forecast._published_steps.discard(key)
    calls = []
    def head(*args, **kwargs):
        calls.append(args)
        return type('Response', (), {'status_code': 200})()
    monkeypatch.setattr(ecmwf_forecast.requests, 'head', head)
    try:
        assert ecmwf_forecast._index_exists(run, 12)
        assert ecmwf_forecast._index_exists(run, 12)
        assert len(calls) == 1
    finally:
        ecmwf_forecast._published_steps.discard(key)


def test_cycle_keeps_newest_persisted_run_when_head_fails(monkeypatch):
    older = '2026-08-29T18:00:00Z'
    newer = RUN
    monkeypatch.setattr(ecmwf_forecast, 'retained_manifests', lambda *a, **kw: [
        {'run': newer, 'catalog_products': {
            'ecmwf-mslp-theta-e-850': {'valid_times': [VALID]}}},
        {'run': older},
    ])
    monkeypatch.setattr(ecmwf_forecast, 'latest_run',
                        lambda: ecmwf_forecast.parse_run(older))
    key = (ecmwf_forecast.parse_run(newer).isoformat(), 12)
    try:
        assert ecmwf_forecast._run_for_store(object()) == (
            ecmwf_forecast.parse_run(newer), ecmwf_forecast.parse_run(newer))
        assert key in ecmwf_forecast._published_steps
    finally:
        ecmwf_forecast._published_steps.discard(key)


def test_older_run_waits_until_newest_is_fully_complete(monkeypatch):
    older = '2026-08-29T18:00:00Z'
    latest = ecmwf_forecast.parse_run(RUN)
    current = {'run': RUN, 'status': 'publishing', 'progress': {'percent': 99.0}}
    manifests = [current, {'run': older, 'status': 'publishing'}]
    monkeypatch.setattr(ecmwf_forecast, 'retained_manifests', lambda *a, **kw: manifests)
    monkeypatch.setattr(ecmwf_forecast, 'latest_run', lambda: latest)
    assert ecmwf_forecast._run_for_store(object()) == (latest, latest)
    current['status'] = 'complete'
    current['progress']['percent'] = 66.4
    assert ecmwf_forecast._run_for_store(object()) == (latest, latest)
    current['progress']['percent'] = 100.0
    assert ecmwf_forecast._run_for_store(object()) == (
        ecmwf_forecast.parse_run(older), latest)


def test_index_retries_rate_limit_before_marking_failure(monkeypatch):
    run = ecmwf_forecast.parse_run(RUN)
    calls = []
    def get(*args, **kwargs):
        calls.append(args)
        status = 429 if len(calls) == 1 else 200
        return type('Response', (), {
            'status_code': status,
            'text': '{"param":"t"}\n',
        })()
    monkeypatch.setattr(ecmwf_forecast.requests, 'get', get)
    monkeypatch.setattr(ecmwf_forecast.time, 'sleep', lambda _: None)
    ecmwf_forecast._read_index_cached.cache_clear()
    try:
        assert ecmwf_forecast.read_index(run, 12) == [{'param': 't'}]
        assert len(calls) == 2
    finally:
        ecmwf_forecast._read_index_cached.cache_clear()


def test_cycle_runs_parallel_and_checkpoints_completed_batch(monkeypatch):
    import threading
    run = ecmwf_forecast.parse_run(RUN)
    product = 'ecmwf-temperature-500'
    monkeypatch.setattr(ecmwf_forecast, 'PRODUCTS', {product: ecmwf_forecast.PRODUCTS[product]})
    monkeypatch.setattr(ecmwf_forecast, 'latest_run', lambda: run)
    monkeypatch.setattr(ecmwf_forecast, 'catalog_payload', lambda _: {
        'products': {product: {'valid_times': [VALID]}}
    })
    monkeypatch.setattr(ecmwf_forecast, 'read_json', lambda *a: None)
    monkeypatch.setenv('METEOLABX_ECMWF_WORKERS', '3')
    monkeypatch.setattr(ecmwf_forecast, 'memory_worker_capacity', lambda count, cached: count)
    barrier = threading.Barrier(3)
    def frame(*args):
        barrier.wait(timeout=5)
        return _frame_bytes(), {}
    monkeypatch.setattr(ecmwf_forecast, 'frame_payload', frame)
    snapshots = []
    monkeypatch.setattr(ecmwf_forecast, 'write_json', lambda store, key, value:
                        snapshots.append(json.loads(json.dumps(value))))
    monkeypatch.setattr(ecmwf_forecast, 'register_run_slot', lambda *a: None)
    monkeypatch.setattr(ecmwf_forecast, 'prune_retained_runs', lambda *a, **k: None)
    result = ecmwf_forecast.run_cycle(max_frames=3)
    assert result['frames_published'] == 3
    assert snapshots[0]['progress']['frames_available'] == 0
    assert snapshots[2]['progress']['frames_available'] == 3


@pytest.mark.parametrize('used, cached, expected', [
    (0, 0, 4), (2, 0, 2), (3, 0, 0), (3, 0.25, 1), (5, 0, 0),
])
def test_memory_capacity_reserves_whole_batch(monkeypatch, used, cached, expected):
    from server.services import forecast_memory
    gb = 1024**3
    for key in ('WORKER_MEMORY_GB', 'MEMORY_RESERVE_GB', 'CACHE_MEMORY_GB'):
        monkeypatch.delenv('METEOLABX_ECMWF_' + key, raising=False)
    monkeypatch.setattr(forecast_memory, 'cgroup_memory', lambda *_: (used * gb, 4 * gb))
    assert ecmwf_forecast.memory_worker_capacity(4, int(cached * gb)) == expected


def test_unmeasured_memory_limits_ecmwf_to_one_worker(monkeypatch):
    from server.services import forecast_memory
    monkeypatch.setattr(forecast_memory, 'cgroup_memory', lambda *_: None)
    assert ecmwf_forecast.memory_worker_capacity(8) == 1


def test_cycle_defers_without_errors_and_resumes_when_memory_returns(monkeypatch):
    product = 'ecmwf-temperature-500'
    run = ecmwf_forecast.parse_run(RUN)
    monkeypatch.setattr(ecmwf_forecast, 'PRODUCTS', {product: ecmwf_forecast.PRODUCTS[product]})
    monkeypatch.setattr(ecmwf_forecast, 'latest_run', lambda: run)
    monkeypatch.setattr(ecmwf_forecast, 'catalog_payload', lambda _: {
        'products': {product: {'valid_times': [VALID]}}
    })
    capacities = iter([1, 0])
    monkeypatch.setattr(ecmwf_forecast, 'memory_worker_capacity', lambda *a: next(capacities))
    calls = []
    def frame(*args):
        calls.append(args[-1])
        return _frame_bytes(), {}
    monkeypatch.setattr(ecmwf_forecast, 'frame_payload', frame)
    result = ecmwf_forecast.run_cycle()
    assert result['frames_published'] == 1
    assert result['waiting_reason'] == 'memory'
    assert result['failures'] == 0
    assert ecmwf_forecast._shared_fields.get() is None
    monkeypatch.setattr(ecmwf_forecast, 'memory_worker_capacity', lambda count, cached: count)
    resumed = ecmwf_forecast.run_cycle()
    assert resumed['frames_published'] == len(ecmwf_forecast.DOMAINS) - 1
    assert resumed['waiting_reason'] is None
    assert len(calls) == len(set(calls))


@pytest.mark.parametrize('stat, expected_used', [('anon 100\nfile 600\n', 100), (None, 700)])
def test_shared_memory_reader_uses_declared_limit_and_anonymous_memory(tmp_path, stat, expected_used):
    from server.services.forecast_memory import cgroup_memory
    (tmp_path / 'memory.current').write_text('700')
    (tmp_path / 'memory.max').write_text('max')
    if stat:
        (tmp_path / 'memory.stat').write_text(stat)
    measurement = cgroup_memory(1000, path_factory=lambda p: tmp_path / p.rsplit('/', 1)[-1])
    assert measurement == (expected_used, 1000)


@pytest.mark.parametrize('raw', ['nan', 'inf', '-1', 'invalid', '0'])
def test_memory_settings_reject_invalid_reservations(monkeypatch, raw):
    monkeypatch.setenv('METEOLABX_ECMWF_WORKER_MEMORY_GB', raw)
    assert ecmwf_forecast._memory_gb('METEOLABX_ECMWF_WORKER_MEMORY_GB', 0.5) == 512 * 1024**2
