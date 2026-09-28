"""AROME-IFS reutiliza el cálculo de AROME sin cruzarse con él.

Los dos modelos comparten rejilla, campos y horas de pasada. Lo que no pueden
compartir es dónde se piden los datos ni dónde se guardan: una pasada de las
00Z de cada uno tiene exactamente las mismas claves salvo por el modelo.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import struct

from fastapi.testclient import TestClient
import pytest

from server.config import Settings, get_settings
from server.main import create_app
from server.routers import forecast
from server.services import arome_models
from server.services.arome_models import (
    current_model,
    per_model_lru_cache,
    run_in_model_context,
    set_process_model,
    using_model,
)
from server.services.forecast_store import (
    LATEST_MANIFEST_KEY,
    LocalObjectStore,
    frame_key,
    latest_manifest_key,
    mark_available,
    new_manifest,
    read_json,
    register_run_slot,
    retained_manifests,
    run_manifest_key,
    write_grid,
    write_json,
)

RUN = "2026-09-28T00:00:00Z"
VALID = "2026-09-28T03:00:00Z"


@pytest.fixture(autouse=True)
def _modelo_por_defecto(monkeypatch):
    # Ningún test puede dejarle a otro el proceso en AROME-IFS. Con `setenv`,
    # monkeypatch borra la variable al terminar aunque `set_process_model` la
    # haya cambiado por el camino; `delenv` sobre una ausente no la vigilaría.
    monkeypatch.setenv(arome_models.MODEL_ENV, "arome")


def test_default_model_is_arome_and_context_overrides_it():
    assert current_model() == "arome"
    with using_model("arome-ifs") as source:
        assert current_model() == "arome-ifs"
        assert source.package_product == "productAROIFS"
    assert current_model() == "arome"


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):
        with using_model("arpege"):
            pass


def test_threads_only_see_the_model_when_the_context_is_passed():
    with using_model("arome-ifs"), ThreadPoolExecutor(max_workers=1) as pool:
        sin_contexto = pool.submit(current_model).result()
        con_contexto = pool.submit(run_in_model_context(current_model)).result()
    # Es justo el caso que run_in_model_context existe para evitar.
    assert sin_contexto == "arome"
    assert con_contexto == "arome-ifs"


def test_one_wrapped_function_can_run_in_many_threads_at_once():
    import threading

    barrera = threading.Barrier(4)

    def banda(_indice):
        # Las cuatro dentro a la vez: con un solo Context compartido, la
        # segunda en entrar fallaría con «already entered».
        barrera.wait(timeout=5)
        return current_model()

    with using_model("arome-ifs"), ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(run_in_model_context(banda), range(4))) == ["arome-ifs"] * 4


def test_process_model_reaches_threads_without_context(monkeypatch):
    set_process_model("arome-ifs")
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(current_model).result() == "arome-ifs"


def test_per_model_cache_keeps_models_apart():
    llamadas = []

    @per_model_lru_cache(4)
    def mapa(run: str) -> str:
        llamadas.append((current_model(), run))
        return f"{current_model()}:{run}"

    assert mapa(RUN) == f"arome:{RUN}"
    with using_model("arome-ifs"):
        assert mapa(RUN) == f"arome-ifs:{RUN}"
    assert mapa(RUN) == f"arome:{RUN}"
    assert llamadas == [("arome", RUN), ("arome-ifs", RUN)]
    mapa.cache_clear()
    assert mapa.cache_info().currsize == 0


def test_store_keys_follow_the_active_model():
    # AROME conserva sus claves de siempre: el volumen de producción las usa.
    assert frame_key(RUN, "temperature-2m", VALID).startswith("forecast/runs/")
    assert latest_manifest_key() == LATEST_MANIFEST_KEY
    with using_model("arome-ifs"):
        assert frame_key(RUN, "temperature-2m", VALID).startswith(
            "forecast/models/arome-ifs/runs/"
        )
        assert latest_manifest_key() == "forecast/models/arome-ifs/manifests/latest.json"
        assert run_manifest_key(RUN).startswith("forecast/models/arome-ifs/manifests/")
        # La revisión de cálculo es del código, que es el mismo para los dos.
        assert "--calc" in frame_key(RUN, "ship", VALID)
        manifest = new_manifest(RUN, [VALID])
    assert manifest["forecast_model"] == "arome-ifs"
    assert manifest["model"] == "AROME-IFS 0,025°"
    assert manifest["calculation_revision"] == new_manifest(RUN, [VALID])["calculation_revision"]
    # ECMWF sigue diciendo explícitamente el suyo y no depende del contexto.
    with using_model("arome-ifs"):
        assert frame_key(RUN, "ecmwf-temperature-850", VALID, model="ecmwf").startswith(
            "forecast/models/ecmwf/"
        )


def test_old_manifests_without_model_stay_arome(tmp_path):
    store = LocalObjectStore(tmp_path)
    manifest = new_manifest(RUN, [VALID])
    manifest.pop("forecast_model")
    write_json(store, run_manifest_key(RUN, model="arome"), manifest)
    with using_model("arome-ifs"):
        register_run_slot(store, manifest)
    # Lo escrito antes de separar modelos es de AROME aunque lo lea el otro.
    assert [m["run"] for m in retained_manifests(store, model="arome")] == [RUN]
    assert retained_manifests(store, model="arome-ifs") == []


def test_wcs_client_and_packages_point_to_the_model(monkeypatch, tmp_path):
    from server.services import arome_packages
    from server.services.arome_wcs import AromeWCS

    monkeypatch.setenv("METEOLABX_AROME_PACKAGE_CACHE_DIR", str(tmp_path))
    assert AromeWCS("t").base.endswith("/MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS")
    assert arome_packages._cache_dir() == tmp_path
    with using_model("arome-ifs"):
        cliente = AromeWCS("t")
        carpeta = arome_packages._cache_dir()
        ruta = arome_packages._package_path(
            "IP1", datetime(2026, 9, 28, tzinfo=timezone.utc), "00H06H"
        )
    # El cliente se queda con su modelo aunque luego lo use otro hilo.
    assert cliente.base.endswith("/MF-NWP-HIGHRES-AROMEIFS-0025-FRANCE-WCS")
    assert carpeta == tmp_path / "arome-ifs"
    assert ruta.parent == tmp_path / "arome-ifs"


def test_package_download_url_uses_the_model_api(monkeypatch, tmp_path):
    from server.services import arome_packages

    pedidas = []

    class Respuesta:
        status_code = 404
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def falso_get(url, **kwargs):
        pedidas.append(url)
        return Respuesta()

    monkeypatch.setattr(arome_packages, "authorization_headers", lambda: {})
    monkeypatch.setattr(arome_packages.requests, "get", falso_get)
    run = datetime(2026, 9, 28, tzinfo=timezone.utc)
    with using_model("arome-ifs"), pytest.raises(arome_packages.AromePackageNotReady):
        arome_packages._download_package("IP1", run, "00H06H", tmp_path / "IP1.grib2")
    assert pedidas == [
        "https://public-api.meteofrance.fr/previnum/DPPaquetAROMEIFS/v1/models/"
        "AROMEIFS/grids/0.025/packages/IP1/productAROIFS"
    ]


def _grid(run: str, valor: float) -> bytes:
    cabecera = json.dumps({
        "product": "temperature-2m", "width": 1, "height": 1, "unit": "°C",
        "run": run, "valid_time": VALID, "maximum": valor,
    }).encode()
    return struct.pack("<I", len(cabecera)) + cabecera + struct.pack("<f", valor)


def _publicar(store, modelo: str, valor: float) -> bytes:
    contenido = _grid(RUN, valor)
    producto = {"run": RUN, "run_local": RUN, "valid_times": [VALID], "unit": "°C", "vmax": 42}
    with using_model(modelo):
        manifest = new_manifest(RUN, [VALID], catalog_products={"temperature-2m": producto})
        mark_available(manifest, "temperature-2m", VALID)
        write_grid(store, frame_key(RUN, "temperature-2m", VALID), contenido)
        write_json(store, run_manifest_key(RUN), manifest)
        write_json(store, latest_manifest_key(), manifest)
        register_run_slot(store, manifest)
    return contenido


def _cliente(monkeypatch, store, *, ifs: bool = True) -> TestClient:
    if ifs:
        monkeypatch.setenv("METEOLABX_ENABLE_AROME_IFS", "1")
    else:
        monkeypatch.delenv("METEOLABX_ENABLE_AROME_IFS", raising=False)
    monkeypatch.setattr(forecast, "get_forecast_store", lambda: store)
    monkeypatch.setattr(
        forecast,
        "frame_grid",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cálculo bajo demanda")),
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        arome_api_key="t", ranking_refresh_enabled=False, forecast_precomputed_only=True,
    )
    return TestClient(app)


def test_same_run_of_both_models_is_served_separately(monkeypatch, tmp_path):
    store = LocalObjectStore(tmp_path)
    arome = _publicar(store, "arome", 21.0)
    ifs = _publicar(store, "arome-ifs", 19.5)
    parametros = {"product": "temperature-2m", "valid_time": VALID, "run": RUN}

    with _cliente(monkeypatch, store) as client:
        de_arome = client.get("/v1/forecast/arome/frames.grid", params=parametros)
        de_ifs = client.get("/v1/forecast/arome-ifs/frames.grid", params=parametros)
        catalogo = client.get("/v1/forecast/arome-ifs/catalog").json()
        progreso = client.get("/v1/forecast/arome-ifs/progress").json()

    assert de_arome.status_code == de_ifs.status_code == 200
    assert de_arome.content == arome
    assert de_ifs.content == ifs
    assert catalogo["model"] == "AROME-IFS"
    assert catalogo["products"]["temperature-2m"]["available_times"] == [VALID]
    assert progreso["run"] == RUN
    # Servir AROME-IFS no deja el proceso en ese modelo.
    assert current_model() == "arome"


def test_arome_ifs_is_hidden_until_enabled(monkeypatch, tmp_path):
    store = LocalObjectStore(tmp_path)
    _publicar(store, "arome-ifs", 19.5)
    with _cliente(monkeypatch, store, ifs=False) as client:
        assert client.get("/v1/forecast/arome-ifs/catalog").status_code == 404
        # AROME sigue en su sitio aunque no tenga pasadas.
        assert client.get("/v1/forecast/arome/progress").status_code == 200


def test_worker_state_and_job_payload_carry_the_model(monkeypatch):
    from scripts import forecast_worker

    assert forecast_worker.worker_state_key() == "forecast/worker/state.json"
    set_process_model("arome-ifs")
    assert forecast_worker.worker_state_key() == "forecast/models/arome-ifs/worker/state.json"

    capturado = {}

    class Proceso:
        exitcode = 0

        def __init__(self, target, args, name):
            capturado.update(payload=args[1], name=name)

        def start(self): pass
        def join(self, timeout=None): pass
        def is_alive(self): return False

    class Contexto:
        def Queue(self, maxsize=0):
            class Cola:
                def get(self, timeout=None): return ("ok", "")
                def close(self): pass
            return Cola()

        def Process(self, target, args, name):
            return Proceso(target, args, name)

    monkeypatch.setattr(forecast_worker.multiprocessing, "get_context", lambda _kind: Contexto())
    job = forecast_worker.ForecastJob(
        run=RUN, valid_time=VALID, products=("temperature-2m",), scope="model", tier=0,
    )
    forecast_worker._run_isolated_job(job, 60)
    assert capturado["payload"]["model"] == "arome-ifs"
    assert capturado["name"].startswith("arome-ifs-")


def test_run_alerts_of_each_model_do_not_silence_each_other():
    from server.services.run_report import alert_for_report

    base = {"run": RUN, "severity": "warn", "issues": ["lenta"]}
    arome = alert_for_report({**base, "model": "arome"})
    ifs = alert_for_report({**base, "model": "arome-ifs", "model_label": "AROME-IFS 0,025°"})
    assert arome.key == f"forecast/run-report/{RUN}"
    assert ifs.key == f"forecast/run-report/arome-ifs/{RUN}"
    assert "AROME-IFS" in ifs.subject


def test_stored_manifest_is_read_from_its_model(tmp_path):
    store = LocalObjectStore(tmp_path)
    _publicar(store, "arome-ifs", 19.5)
    assert read_json(store, LATEST_MANIFEST_KEY) is None
    with using_model("arome-ifs"):
        assert read_json(store, latest_manifest_key())["forecast_model"] == "arome-ifs"
