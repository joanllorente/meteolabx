"""
Tests del endpoint ``POST /v1/observations/current``.

Aquí se testa el contrato HTTP completo: validación Pydantic, status
codes, shape del JSON de éxito y de error. La capa de servicio (WU)
queda mockeada vía ``dependency_overrides`` del ``http_client``.
"""

from __future__ import annotations

import math

import httpx
import pytest

from .conftest import WU_OK_OBSERVATION


# =====================================================================
# Camino feliz
# =====================================================================

def test_post_current_returns_200_and_normalized_observation(app_factory) -> None:
    with app_factory(status=200, json_body=WU_OK_OBSERVATION) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "WU", "station_id": "ITEST123", "api_key": "fake"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["Tc"] == 22.0
    assert body["RH"] == 65.0
    assert body["wind_dir_deg"] == 180.0
    assert body["epoch"] == 1717255200
    assert body["solar_radiation"] == 800.0


def test_post_current_normalizes_station_id_to_uppercase(app_factory) -> None:
    """El validador del request normaliza station_id a mayúsculas."""
    with app_factory(status=200, json_body=WU_OK_OBSERVATION) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "WU", "station_id": "  itest123  ", "api_key": "fake"},
        )

    # No comprobamos que station_id viaje a WU en mayúsculas (eso es
    # interno del servicio), pero sí que la petición pasa la validación.
    assert response.status_code == 200


def test_post_current_response_has_no_nan_literals(app_factory) -> None:
    """
    JSON estricto no admite NaN. Cuando faltan valores en el payload de
    WU, deben llegar como ``null``, no como ``NaN``.
    """
    payload_without_uv = {
        "observations": [
            {
                "epoch": 1717255200,
                "humidity": 50,
                "winddir": 0,
                # sin solarRadiation ni uv
                "metric": {
                    "temp": 18.0,
                    "windSpeed": 2.0,
                    "windGust": 0,
                },
            }
        ]
    }
    with app_factory(status=200, json_body=payload_without_uv) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "WU", "station_id": "X", "api_key": "Y"},
        )

    assert response.status_code == 200
    assert "NaN" not in response.text
    body = response.json()
    assert body["solar_radiation"] is None
    assert body["uv"] is None


# =====================================================================
# Errores upstream → ErrorResponse
# =====================================================================

@pytest.mark.parametrize(
    "wu_status,expected_http,expected_code",
    [
        (401, 401, "provider_unauthorized"),
        (404, 404, "station_not_found"),
        (429, 429, "provider_ratelimit"),
        (503, 502, "provider_http_error"),
    ],
)
def test_post_current_propagates_provider_error(
    app_factory, wu_status: int, expected_http: int, expected_code: str
) -> None:
    """
    Cualquier error de WU sale como ``ErrorResponse`` con su
    ``error_code`` estable y el HTTP status adecuado.
    """
    with app_factory(status=wu_status) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "WU", "station_id": "X", "api_key": "Y"},
        )

    assert response.status_code == expected_http
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == expected_code
    assert body["provider"] == "WU"


def test_post_current_timeout_returns_504(app_factory) -> None:
    with app_factory(raise_exc=httpx.TimeoutException("slow")) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "WU", "station_id": "X", "api_key": "Y"},
        )

    assert response.status_code == 504
    assert response.json()["error_code"] == "provider_timeout"


def test_post_current_network_error_returns_502(app_factory) -> None:
    with app_factory(raise_exc=httpx.ConnectError("dns fail")) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "WU", "station_id": "X", "api_key": "Y"},
        )

    assert response.status_code == 502
    assert response.json()["error_code"] == "provider_network_error"


# =====================================================================
# Validación Pydantic → 422
# =====================================================================

def test_post_current_rejects_empty_station_id(app_factory) -> None:
    with app_factory(status=200, json_body=WU_OK_OBSERVATION) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "WU", "station_id": "", "api_key": "Y"},
        )
    assert response.status_code == 422


def test_post_current_wu_requires_api_key(app_factory) -> None:
    """
    Con WU, ``api_key`` vacío ya no es rechazado por Pydantic (es
    opcional en el schema porque AEMET no la necesita), pero el
    endpoint devuelve 400 con error_code ``missing_api_key`` indicando
    que WU sí la requiere. Esto es lo que el frontend debe interpretar
    como "introduce tu API key de WU".
    """
    with app_factory(status=200, json_body=WU_OK_OBSERVATION) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "WU", "station_id": "X", "api_key": ""},
        )
    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "missing_api_key"
    assert body["provider"] == "WU"


def test_post_current_rejects_unknown_provider(app_factory) -> None:
    """Provider fuera del Literal Pydantic → 422 con detalle de validación."""
    with app_factory(status=200, json_body=WU_OK_OBSERVATION) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "NO_SUCH_PROVIDER", "station_id": "X", "api_key": "Y"},
        )
    assert response.status_code == 422


def test_post_current_rejects_missing_body(app_factory) -> None:
    with app_factory(status=200, json_body=WU_OK_OBSERVATION) as client:
        response = client.post("/v1/observations/current", json={})
    assert response.status_code == 422


# =====================================================================
# Seguridad básica: la API key no debe aparecer en la respuesta de error
# =====================================================================

def test_api_key_is_never_echoed_in_error_response(app_factory) -> None:
    """
    Aunque ahora no la metemos en respuestas, dejamos el test como
    barrera para futuras regresiones (alguien podría poner el body
    completo en ``detail``).
    """
    secret = "S3CR3T_DO_NOT_LEAK_xyz789"
    with app_factory(status=401) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "WU", "station_id": "X", "api_key": secret},
        )

    assert response.status_code == 401
    assert secret not in response.text


# =====================================================================
# POST /v1/observations/series/today
# =====================================================================

# Payload realista de WU /all/1day para tests de series.
WU_TODAY_SERIES_BODY = {
    "observations": [
        {
            "epoch": 1717254000,
            "humidityAvg": 60,
            "winddir": 90,
            "lat": 41.387,
            "lon": 2.169,
            "metric": {
                "tempAvg": 19.0,
                "dewptLow": 12.0,
                "pressureMin": 1013.0,
                "windSpeed": 5.0,
                "windgustHigh": 10.0,
                "solarRadiation": 200.0,
                "precipTotal": 0.0,
            },
        },
        {
            "epoch": 1717254300,
            "humidityAvg": 58,
            "winddir": 95,
            "metric": {
                "tempAvg": 19.5,
                "dewptLow": 12.0,
                "pressureMin": 1013.1,
                "windSpeed": 6.0,
                "windgustHigh": 11.0,
                "solarRadiation": 250.0,
                "precipTotal": 0.4,
            },
        },
    ]
}


def test_post_today_series_returns_200_and_arrays(app_factory) -> None:
    with app_factory(status=200, json_body=WU_TODAY_SERIES_BODY) as client:
        response = client.post(
            "/v1/observations/series/today",
            json={"provider": "WU", "station_id": "ITEST", "api_key": "fake"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["epochs"] == [1717254000, 1717254300]
    assert body["temps"] == [19.0, 19.5]
    assert body["precips"] == [0.0, 0.4]
    assert body["has_data"] is True
    assert body["lat"] == 41.387


def test_post_today_series_response_has_no_nan_literals(app_factory) -> None:
    """Huecos en el payload de WU → null en el JSON, no NaN."""
    body_with_gaps = {
        "observations": [
            # Solo temp + humidity; el resto debe llegar como null
            {"epoch": 1717254000, "humidityAvg": 50,
             "metric": {"tempAvg": 18.0}},
        ]
    }
    with app_factory(status=200, json_body=body_with_gaps) as client:
        response = client.post(
            "/v1/observations/series/today",
            json={"provider": "WU", "station_id": "X", "api_key": "Y"},
        )

    assert response.status_code == 200
    assert "NaN" not in response.text
    body = response.json()
    assert body["dewpts"] == [None]
    assert body["solar_radiations"] == [None]


def test_series_schemas_keep_arrays_aligned_when_epoch_is_invalid() -> None:
    from server.schemas.observation import RecentSeries, TodaySeries

    payload = {
        "epochs": [100, "invalid", 300],
        "temps": [10.0, 99.0, 30.0],
        "humidities": [50.0, 99.0, 70.0],
        "pressures": [1010.0, 999.0, 1030.0],
        "has_data": True,
    }

    today = TodaySeries.from_provider_dict(payload)
    recent = RecentSeries.from_provider_dict(payload)

    assert today.epochs == [100, 300]
    assert today.temps == [10.0, 30.0]
    assert today.humidities == [50.0, 70.0]
    assert recent.epochs == [100, 300]
    assert recent.temps == [10.0, 30.0]
    assert recent.pressures == [1010.0, 1030.0]


def test_post_today_series_propagates_provider_errors(app_factory) -> None:
    """El endpoint de series usa el MISMO mapeo de errores que /current."""
    with app_factory(status=401) as client:
        response = client.post(
            "/v1/observations/series/today",
            json={"provider": "WU", "station_id": "X", "api_key": "bad"},
        )
    assert response.status_code == 401
    assert response.json()["error_code"] == "provider_unauthorized"


def test_post_today_series_rejects_empty_station_id(app_factory) -> None:
    with app_factory(status=200, json_body=WU_TODAY_SERIES_BODY) as client:
        response = client.post(
            "/v1/observations/series/today",
            json={"provider": "WU", "station_id": "", "api_key": "Y"},
        )
    assert response.status_code == 422


def test_post_today_series_unknown_provider_returns_422(app_factory) -> None:
    """Provider fuera del Literal Pydantic → 422."""
    with app_factory(status=200, json_body=WU_TODAY_SERIES_BODY) as client:
        response = client.post(
            "/v1/observations/series/today",
            json={"provider": "NO_SUCH_PROVIDER", "station_id": "X", "api_key": "Y"},
        )
    assert response.status_code == 422


# =====================================================================
# AEMET en /current
# =====================================================================

# Fixture: payload AEMET realista (record final del array que devuelve OpenData).
def _aemet_series_fint(hour: int, minute: int) -> str:
    """fint de HOY (la serie del backend recorta al día local de Madrid)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    local = datetime.now(ZoneInfo("Europe/Madrid")).replace(
        hour=hour, minute=minute, second=0, microsecond=0,
    )
    return local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S+0000")


AEMET_RECORD_OK = {
    "idema": "0201X",
    # fint dinámico: el pipeline degrada observaciones obsoletas (y con
    # una serie fresca promocionaría su último punto como current).
    "fint": None,  # se rellena más abajo con _aemet_series_fint(13, 0)
    "ubi": "BARCELONA AEROPUERTO",
    "lat": 41.297, "lon": 2.07, "alt": 4,
    "ta": "22.4", "hr": "65",
    "pres_nmar": "1015.2", "pres": "1014.8",
    "vv": "5.0", "vmax": "8.2", "dv": "180",
    "prec": "0.4",
}

AEMET_RECORD_OK["fint"] = _aemet_series_fint(13, 0)

AEMET_STEP1_OK = {"estado": 200, "datos": "https://opendata.aemet.es/datos/abc"}


def _make_aemet_app(*, aemet_api_key: str = "TEST_KEY_FROM_SERVER"):
    """
    Crea una FastAPI app con backend Settings que tiene aemet_api_key
    configurada y un MockTransport que distingue paso 1 y paso 2 de
    AEMET por URL.
    """
    import httpx as _httpx
    from server.config import Settings, get_settings
    from server.dependencies.http import get_http_client
    from server.main import create_app

    def handler(request: _httpx.Request) -> _httpx.Response:
        if "/datos/estacion/" in str(request.url):
            return _httpx.Response(200, json=AEMET_STEP1_OK)
        return _httpx.Response(200, json=[AEMET_RECORD_OK])

    mock_client = _httpx.AsyncClient(transport=_httpx.MockTransport(handler))
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: mock_client
    def _settings() -> Settings:
        settings = Settings(aemet_api_key=aemet_api_key)
        # Reasignación post-init: permite simular "sin key" en tests
        # saltándose la cadena de fallbacks legacy del model_validator.
        settings.aemet_api_key = aemet_api_key
        return settings

    app.dependency_overrides[get_settings] = _settings
    return app


def test_post_current_with_aemet_returns_normalized_observation() -> None:
    """AEMET end-to-end: 2-step fetch + normalización + derivados calculados."""
    from fastapi.testclient import TestClient

    app = _make_aemet_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "AEMET", "station_id": "0201X", "api_key": ""},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["Tc"] == 22.4
    assert body["RH"] == 65.0
    assert body["p_hpa"] == 1015.2  # MSL
    # AEMET reporta wind en m/s; el backend devuelve km/h: 5.0 * 3.6 = 18.0
    assert body["wind"] == pytest.approx(18.0)
    # Derivados: calculados (no del API)
    assert body["Td"] is not None
    assert body["feels_like"] is not None
    # AEMET no tiene piranómetro → NaN → null en JSON
    assert body["solar_radiation"] is None
    assert body["uv"] is None


def test_post_current_with_aemet_ignores_api_key_in_body() -> None:
    """
    AEMET usa la key del servidor; cualquier ``api_key`` en el body se
    ignora. Aunque el cliente envíe una key absurda, la petición sale OK.
    """
    from fastapi.testclient import TestClient

    app = _make_aemet_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "AEMET", "station_id": "0201X", "api_key": "ignored"},
        )
    assert response.status_code == 200


AEMET_SERIES_BODY = [
    {
        "idema": "0201X",
        "fint": _aemet_series_fint(12, 0),
        "lat": 41.297, "lon": 2.07, "alt": 4,
        "ta": "20.0", "hr": "70",
        "pres_nmar": "1015.0", "pres": "1014.6",
        "vv": "4.0", "vmax": "6.5", "dv": "180",
    },
    {
        "idema": "0201X",
        "fint": _aemet_series_fint(12, 10),
        "lat": 41.297, "lon": 2.07, "alt": 4,
        "ta": "20.4", "hr": "68",
        "pres_nmar": "1015.1", "pres": "1014.7",
        "vv": "4.2", "vmax": "6.8", "dv": "175",
    },
]


def _make_aemet_app_with_series(
    *,
    aemet_api_key: str = "TEST_SERVER_KEY",
    current_body=None,
    series_body=None,
):
    """
    Como ``_make_aemet_app`` pero distingue por endpoint AEMET:
    - URLs que contienen ``/diezminutal/`` → ``series_body``
    - resto → ``current_body``
    """
    import httpx as _httpx
    from server.config import Settings, get_settings
    from server.dependencies.http import get_http_client
    from server.main import create_app

    def handler(request: _httpx.Request) -> _httpx.Response:
        path = str(request.url.path)
        # Paso 1: el path AEMET difiere; paso 2 es siempre la URL temporal de opendata.aemet
        if "/datos/estacion/" in path and "/diezminutal/" in path:
            return _httpx.Response(200, json={
                "estado": 200,
                "datos": "https://opendata.aemet.es/datos/series",
            })
        if "/datos/estacion/" in path:
            return _httpx.Response(200, json={
                "estado": 200,
                "datos": "https://opendata.aemet.es/datos/current",
            })
        # Paso 2: distinguimos por URL del paso 2
        if "/series" in path:
            return _httpx.Response(200, json=series_body or AEMET_SERIES_BODY)
        if "/current" in path:
            return _httpx.Response(200, json=[current_body] if current_body else [AEMET_RECORD_OK])
        return _httpx.Response(404)

    mock_client = _httpx.AsyncClient(transport=_httpx.MockTransport(handler))
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: mock_client
    def _settings() -> Settings:
        settings = Settings(aemet_api_key=aemet_api_key)
        # Reasignación post-init: permite simular "sin key" en tests
        # saltándose la cadena de fallbacks legacy del model_validator.
        settings.aemet_api_key = aemet_api_key
        return settings

    app.dependency_overrides[get_settings] = _settings
    return app


def test_post_today_series_with_aemet_returns_normalized_arrays() -> None:
    """
    AEMET en ``/series/today`` ya funciona end-to-end: el dispatcher
    pasa a ``server.services.aemet.fetch_today_series``, que normaliza
    a arrays paralelas (epochs, temps, humidities, etc.).
    """
    from fastapi.testclient import TestClient

    app = _make_aemet_app_with_series()
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/series/today",
            json={"provider": "AEMET", "station_id": "0201X", "api_key": ""},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    assert len(body["epochs"]) == 2
    assert body["temps"] == [20.0, 20.4]
    # AEMET reporta viento en m/s; el backend convierte a km/h.
    assert body["winds"][0] == pytest.approx(4.0 * 3.6)
    assert body["gusts"][1] == pytest.approx(6.8 * 3.6)
    # AEMET no expone radiación ni UV → listas con NaN serializados a null.
    assert all(v is None for v in body["solar_radiations"])
    assert all(v is None for v in body["uv_indexes"])
    # lat/lon detectados del primer record con coordenadas.
    assert body["lat"] == pytest.approx(41.297)


def test_post_current_processed_aemet_combines_current_and_series() -> None:
    """
    ``/current/processed`` con provider=AEMET hace ambas llamadas
    (current + series) en paralelo, pasa por el pipeline de dominio
    y devuelve observación + derivadas.
    """
    from fastapi.testclient import TestClient

    app = _make_aemet_app_with_series()
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "AEMET", "station_id": "0201X", "api_key": ""},
        )

    assert response.status_code == 200
    body = response.json()
    # El contrato puede crecer (station/daily_extremes/series añadidos
    # después); los bloques núcleo deben estar siempre.
    assert {"observation", "derivatives", "warnings"} <= set(body.keys())

    # Observación con derivados ya calculados (Td/feels_like/heat_index)
    obs = body["observation"]
    assert obs["Tc"] == 22.4  # del current record
    assert obs["Td"] is not None  # calculado con Magnus-Tetens

    # Derivadas: has_chart_data debería ser True porque la serie trae 2 puntos.
    deriv = body["derivatives"]
    assert deriv["has_chart_data"] is True


def test_post_current_processed_aemet_requires_server_api_key() -> None:
    """AEMET sin key del servidor → 401 unauthorized, no fallback al body."""
    from fastapi.testclient import TestClient

    app = _make_aemet_app_with_series(aemet_api_key="")
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "AEMET", "station_id": "0201X", "api_key": "ignored"},
        )
    assert response.status_code == 401
    assert response.json()["error_code"] == "provider_unauthorized"


def test_post_current_with_aemet_no_server_key_returns_unauthorized() -> None:
    """
    Si la API key de AEMET del servidor está vacía, el endpoint devuelve
    401 con ``provider_unauthorized``. Mensaje claro para el operador:
    "configura METEOLABX_AEMET_API_KEY".
    """
    from fastapi.testclient import TestClient

    app = _make_aemet_app(aemet_api_key="")
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current",
            json={"provider": "AEMET", "station_id": "0201X", "api_key": ""},
        )

    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "provider_unauthorized"
    assert body["provider"] == "AEMET"


def test_post_today_series_aemet_requires_server_api_key() -> None:
    """
    AEMET en ``/series/today`` ya está implementado: usa la API key del
    servidor (``METEOLABX_AEMET_API_KEY``). Si esa env var está vacía,
    el servicio AEMET lanza ``provider_unauthorized``. El test verifica
    que la cadena dispatch → fetch → error se propaga correctamente.
    """
    # Con la cadena de fallbacks, Settings() ya no queda sin key de
    # AEMET; forzamos el vacío con el factory que la bypasea.
    from fastapi.testclient import TestClient

    app = _make_aemet_app_with_series(aemet_api_key="")
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/series/today",
            json={"provider": "AEMET", "station_id": "0201X", "api_key": ""},
        )
    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "provider_unauthorized"
    assert body["provider"] == "AEMET"


# =====================================================================
# POST /v1/observations/current/processed
# =====================================================================
#
# Este endpoint llama a ``/current`` Y ``/series/today`` en paralelo,
# luego ejecuta el pipeline puro. Para mockear ambas llamadas con un
# único transport, ruteamos por URL.


def _make_processed_mock_app(
    *,
    current_response,
    series_response,
):
    """
    App con mock transport que rutea por endpoint WU:
    /observations/current → ``current_response``
    /all/1day             → ``series_response``

    Cada response puede ser una tupla ``(status, json_body)`` o una
    excepción a propagar.
    """
    import httpx
    from server.dependencies.http import get_http_client
    from server.main import create_app

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/observations/current"):
            resp = current_response
        else:
            resp = series_response
        if isinstance(resp, BaseException):
            raise resp
        status, body = resp
        return httpx.Response(status, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: client
    return app


WU_CURRENT_BODY_OK = {
    "observations": [
        {
            "epoch": 1717255200,
            "humidity": 65,
            "winddir": 180,
            "lat": 41.387,
            "lon": 2.169,
            "elev": 12.0,
            "solarRadiation": 800.0,
            "uv": 6.0,
            "obsTimeLocal": "2026-06-01 12:00:00",
            "obsTimeUtc": "2026-06-01T10:00:00Z",
            "metric": {
                "temp": 22.0,
                "pressure": 1013.0,
                "dewpt": 14.0,
                "heatIndex": 22.0,
                "windSpeed": 8.0,
                "windGust": 12.0,
                "precipRate": 0.0,
                "precipTotal": 0.4,
            },
        }
    ]
}


def test_iem_observation_endpoints_are_connectable() -> None:
    import httpx
    from server.dependencies.http import get_http_client
    from server.main import create_app

    payload = {
        "data": [
            {
                "utc_valid": "2026-06-10T08:00:00Z",
                "tmpf": 68.0,
                "dwpf": 50.0,
                "relh": 52.0,
                "alti": 29.92,
                "sknt": 10.0,
                "gust": 20.0,
                "drct": 180.0,
                "p01i": 0.01,
            },
            {
                "utc_valid": "2026-06-10T09:00:00Z",
                "tmpf": 70.0,
                "dwpf": 51.0,
                "relh": 55.0,
                "alti": 29.94,
                "sknt": 12.0,
                "gust": 22.0,
                "drct": 190.0,
                "p01i": 0.02,
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    app = create_app()
    app.dependency_overrides[get_http_client] = lambda: mock_client

    request_body = {
        "provider": "IEM",
        "station_id": "ES__ASOS|LEBL",
        "sun_tz_name": "Europe/Madrid",
        "days_back": 1,
    }
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        current = client.post("/v1/observations/current", json=request_body)
        today = client.post("/v1/observations/series/today", json=request_body)
        processed = client.post("/v1/observations/current/processed", json=request_body)
        recent = client.post("/v1/observations/series/recent", json=request_body)

    assert current.status_code == 200
    assert current.json()["Tc"] == pytest.approx((70.0 - 32.0) * 5.0 / 9.0)
    assert today.status_code == 200
    assert today.json()["has_data"] is True
    assert processed.status_code == 200
    assert processed.json()["station"]["network"] == "ES__ASOS"
    assert processed.json()["station"]["connectable"] is True
    assert recent.status_code == 200
    assert recent.json()["has_data"] is True


def test_aemet_processed_overlays_newer_series_point_before_pipeline() -> None:
    from server.routers.observations import _overlay_aemet_current_from_newer_series

    current = {
        "epoch": 1000,
        "Tc": 18.0,
        "RH": 70.0,
        "p_hpa": 1012.0,
        "p_abs_hpa": 900.0,
        "wind": 4.0,
        "gust": 8.0,
        "wind_dir_deg": 90.0,
    }
    series = {
        "epochs": [900, 1600],
        "temps": [17.0, 22.5],
        "humidities": [72.0, 55.0],
        "pressures": [1011.0, 1014.5],
        "winds": [5.0, 12.0],
        "gusts": [9.0, 20.0],
        "wind_dirs": [100.0, 270.0],
        "has_data": True,
    }

    merged = _overlay_aemet_current_from_newer_series(current, series)

    assert merged["epoch"] == 1600
    assert merged["Tc"] == 22.5
    assert merged["RH"] == 55.0
    assert merged["wind"] == 12.0
    assert merged["gust"] == 20.0
    assert merged["wind_dir_deg"] == 270.0
    assert merged["p_hpa"] == 1014.5
    assert math.isnan(merged["p_abs_hpa"])


def test_post_current_processed_returns_observation_derivatives_and_warnings() -> None:
    from fastapi.testclient import TestClient

    app = _make_processed_mock_app(
        current_response=(200, WU_CURRENT_BODY_OK),
        series_response=(200, WU_TODAY_SERIES_BODY),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "WU", "station_id": "ITEST", "api_key": "fake",
                  "sun_tz_name": "Europe/Madrid"},
        )

    assert response.status_code == 200
    body = response.json()
    # El contrato puede crecer (station/daily_extremes/series añadidos
    # después); los bloques núcleo deben estar siempre.
    assert {"observation", "derivatives", "warnings"} <= set(body.keys())

    # observation viene mutada con Td/feels_like/heat_index calculados
    # por el pipeline (no los del payload WU). El pipeline calcula
    # incondicionalmente — el filtrado por rango se hace en el frontend
    # al renderizar.
    obs = body["observation"]
    assert obs["Tc"] == 22.0
    assert obs["RH"] == 65.0
    assert obs["heat_index"] is not None
    assert obs["feels_like"] is not None
    # Td (calculado) sobrescribe al del payload
    assert obs["Td"] is not None

    # derivatives tiene los 32 campos
    deriv = body["derivatives"]
    assert deriv["has_radiation"] is True
    # has_chart_data: la serie de fixture tiene 2 puntos → True
    assert deriv["has_chart_data"] is True
    # Td_calc presente (cálculo a partir de Tc/RH)
    assert deriv["Td_calc"] is not None
    assert 13.0 <= deriv["Td_calc"] <= 16.0
    # p_msl viene tal cual de WU (que reporta MSL), p_abs se deriva con
    # ``msl_to_absolute`` usando z+Tc. Para z=12m la diferencia es de
    # ~1 hPa, así que p_abs ≈ 1012 mientras MSL = 1013.
    assert deriv["p_msl_disp"] == "1013"
    assert body["series"]["precips"] == [0.0, 0.4]
    # WU usa 0 decimales: el display NO debe llevar punto.
    assert "." not in deriv["p_abs_disp"]
    # Y debe ser un entero razonablemente cercano a 1013 (no más de 5 hPa abajo).
    assert 1008 <= int(deriv["p_abs_disp"]) <= 1013


def test_post_current_processed_resilient_when_series_fails() -> None:
    """Si la serie falla, el endpoint sigue devolviendo 200 con derivadas parciales."""
    from fastapi.testclient import TestClient

    app = _make_processed_mock_app(
        current_response=(200, WU_CURRENT_BODY_OK),
        series_response=(503, {}),  # serie falla
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "WU", "station_id": "X", "api_key": "Y"},
        )

    assert response.status_code == 200
    body = response.json()
    # Sin serie → has_chart_data False, ET0 null
    assert body["derivatives"]["has_chart_data"] is False
    assert body["derivatives"]["et0"] is None
    # Pero las derivadas que no dependen de la serie siguen presentes
    assert body["derivatives"]["Td_calc"] is not None


def test_post_current_processed_current_failure_propagates_provider_error() -> None:
    """Si ``current`` falla, devolvemos ErrorResponse (no enmascaramos)."""
    from fastapi.testclient import TestClient

    app = _make_processed_mock_app(
        current_response=(401, {}),  # API key inválida
        series_response=(200, WU_TODAY_SERIES_BODY),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "WU", "station_id": "X", "api_key": "bad"},
        )

    assert response.status_code == 401
    assert response.json()["error_code"] == "provider_unauthorized"


def test_post_current_processed_response_has_no_nan_literals() -> None:
    """Ningún campo NaN escapa al JSON (validación estricta)."""
    from fastapi.testclient import TestClient

    app = _make_processed_mock_app(
        current_response=(200, WU_CURRENT_BODY_OK),
        series_response=(503, {}),  # forzamos serie vacía
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "WU", "station_id": "X", "api_key": "Y"},
        )

    assert "NaN" not in response.text


def test_post_current_processed_validation_max_data_age_minutes_must_be_non_negative() -> None:
    from fastapi.testclient import TestClient

    app = _make_processed_mock_app(
        current_response=(200, WU_CURRENT_BODY_OK),
        series_response=(200, WU_TODAY_SERIES_BODY),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/observations/current/processed",
            json={"provider": "WU", "station_id": "X", "api_key": "Y",
                  "max_data_age_minutes": -1},
        )
    assert response.status_code == 422
