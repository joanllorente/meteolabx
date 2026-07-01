"""
Tests del cliente HTTP del frontend (``utils.api_client``) que consume el
backend FastAPI.

Estos tests no levantan el backend real; usan ``requests-mock``... ah,
no lo tenemos. Usamos ``unittest.mock.patch`` sobre ``requests.post``,
que es el único punto de salida HTTP del módulo. Es la dependencia más
pequeña posible y mantiene los tests rápidos.
"""

from __future__ import annotations

import math
from unittest.mock import patch, MagicMock

import pytest
import requests

from utils.api_errors import BackendApiError


# =====================================================================
# Configuración por env
# =====================================================================

def test_backend_is_unconditional_no_enable_flag() -> None:
    """
    Backend-first: ya no existe ``is_backend_enabled()`` ni el flag
    legacy ``METEOLABX_USE_API``; el frontend consume FastAPI siempre.
    """
    import utils.api_client as api_client
    assert not hasattr(api_client, "is_backend_enabled")


def test_backend_url_default(monkeypatch) -> None:
    monkeypatch.delenv("METEOLABX_API_URL", raising=False)
    from utils.api_client import backend_url
    assert backend_url() == "http://localhost:8000"


def test_backend_url_custom_strips_trailing_slash(monkeypatch) -> None:
    monkeypatch.setenv("METEOLABX_API_URL", "https://api.example.com/")
    from utils.api_client import backend_url
    assert backend_url() == "https://api.example.com"


def test_geocode_client_calls_fastapi_and_preserves_contract() -> None:
    from utils.api_client import fetch_geocode_via_api

    body = {"found": True, "lat": 41.38, "lon": 2.17, "display_name": "Barcelona"}
    with patch("utils.api_client.requests.get", return_value=_mock_response(200, body)) as request:
        result = fetch_geocode_via_api("Barcelona", accept_language="es,en")

    assert request.call_args.args[0].endswith("/v1/stations/geocode")
    assert request.call_args.kwargs["params"] == {"q": "Barcelona", "lang": "es,en"}
    assert result == body


def test_station_near_client_passes_provider_and_country_filters() -> None:
    from utils.api_client import fetch_stations_near_via_api

    body = {"count": 1, "stations": [{"provider": "IEM", "station_id": "X", "connectable": False}]}
    with patch("utils.api_client.requests.get", return_value=_mock_response(200, body)) as request:
        result = fetch_stations_near_via_api(
            40.4, -3.7, max_results=50, provider_ids=["IEM"], countries=["ES"],
            has_historical=True, hide_historical_only=True,
        )

    assert request.call_args.args[0].endswith("/v1/stations/near")
    assert request.call_args.kwargs["params"] == {
        "lat": 40.4,
        "lon": -3.7,
        "radius_km": 2000.0,
        "limit": 50,
        "providers": "IEM",
        "countries": "ES",
        "has_historical": "true",
        "hide_historical_only": "true",
    }
    assert result == body


def test_station_countries_client_calls_fastapi() -> None:
    from utils.api_client import fetch_station_countries_via_api

    with patch("utils.api_client.requests.get", return_value=_mock_response(200, {"US": 10, "ES": 2})) as request:
        result = fetch_station_countries_via_api(["IEM"])

    assert request.call_args.args[0].endswith("/v1/stations/countries")
    assert request.call_args.kwargs["params"] == {"providers": "IEM"}
    assert result == {"US": 10, "ES": 2}


def test_station_catalog_client_passes_country_filters() -> None:
    from utils.api_client import fetch_station_catalog_via_api

    body = {"count": 1, "stations": [{"provider": "AEMET", "station_id": "X"}]}
    with patch("utils.api_client.requests.get", return_value=_mock_response(200, body)) as request:
        result = fetch_station_catalog_via_api(
            lat=41.0,
            lon=2.0,
            max_results=5000,
            provider_ids=["AEMET", "IEM"],
            countries=["ES"],
            has_historical=True,
            hide_historical_only=True,
        )

    assert request.call_args.args[0].endswith("/v1/stations/catalog")
    assert request.call_args.kwargs["params"] == {
        "limit": 5000,
        "lat": 41.0,
        "lon": 2.0,
        "providers": "AEMET,IEM",
        "countries": "ES",
        "has_historical": "true",
        "hide_historical_only": "true",
    }
    assert result == body


def test_solar_model_has_no_external_http_fallback() -> None:
    from pathlib import Path

    source = Path("models/radiation.py").read_text(encoding="utf-8")
    assert "sunrise-sunset.org" not in source
    assert "_sunrise_sunset_api_times" not in source


# =====================================================================
# Helpers para mockear requests.post
# =====================================================================

def _mock_response(status: int, json_body: dict | None = None) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status
    response.json.return_value = json_body if json_body is not None else {}
    return response


# =====================================================================
# Camino feliz: contrato canónico sin reinterpretación
# =====================================================================

def test_via_api_preserves_canonical_shape() -> None:
    from utils.api_client import fetch_provider_current_via_api_strict

    api_body = {
        "epoch": 1717255200,
        "time_local": "2026-06-01 12:00:00",
        "time_utc": "2026-06-01T10:00:00Z",
        "Tc": 22.0, "RH": 65.0, "p_hpa": 1013.0, "Td": 14.0,
        "wind": 8.0, "gust": 12.0, "wind_dir_deg": 180.0,
        "feels_like": 22.0, "heat_index": None, "wind_chill": None,
        "precip_rate": 0.0, "precip_total": 0.4,
        "solar_radiation": 800.0, "uv": 6.0,
        "lat": 41.387, "lon": 2.169, "elevation": 12.0,
    }

    with patch("utils.api_client.requests.post", return_value=_mock_response(200, api_body)) as mock_post:
        result = fetch_provider_current_via_api_strict("WU", "ITEST", api_key="fake_key")

    # Llamada al endpoint correcto con body correcto
    args, kwargs = mock_post.call_args
    assert args[0].endswith("/v1/observations/current")
    assert kwargs["json"] == {"provider": "WU", "station_id": "ITEST", "api_key": "fake_key"}

    # Shape: floats normales se mantienen
    assert result["Tc"] == 22.0
    assert result["wind_dir_deg"] == 180.0
    assert result["epoch"] == 1717255200
    assert result["time_local"] == "2026-06-01 12:00:00"

    assert result["heat_index"] is None
    assert result["wind_chill"] is None


def test_via_api_does_not_invent_missing_numeric_fields() -> None:
    from utils.api_client import fetch_provider_current_via_api_strict

    api_body = {"epoch": 1717255200}  # solo lo mínimo

    with patch("utils.api_client.requests.post", return_value=_mock_response(200, api_body)):
        result = fetch_provider_current_via_api_strict("WU", "X", api_key="Y")

    for field in ("Tc", "RH", "p_hpa", "wind", "uv", "solar_radiation"):
        assert field not in result


# =====================================================================
# Mapeo HTTP error → BackendApiError (kind + status_code históricos)
# =====================================================================

@pytest.mark.parametrize(
    "http_status,error_code,expected_wuerror_kind",
    [
        (401, "provider_unauthorized", "unauthorized"),
        (404, "station_not_found", "notfound"),
        (429, "provider_ratelimit", "ratelimit"),
        (504, "provider_timeout", "timeout"),
        (502, "provider_network_error", "network"),
        (502, "provider_http_error", "http"),
        (502, "provider_bad_response", "badjson"),
        (502, "provider_no_current_data", "nodata"),
    ],
)
def test_via_api_translates_backend_errors_to_client_error(
    http_status: int, error_code: str, expected_wuerror_kind: str
) -> None:
    from utils.api_client import fetch_provider_current_via_api_strict

    err_body = {"ok": False, "error_code": error_code, "provider": "WU", "detail": "..."}
    with patch("utils.api_client.requests.post", return_value=_mock_response(http_status, err_body)):
        with pytest.raises(BackendApiError) as excinfo:
            fetch_provider_current_via_api_strict("WU", "X", api_key="Y")

    assert excinfo.value.kind == expected_wuerror_kind
    # El cliente conserva el status HTTP para que la UI decida cómo presentarlo.
    assert excinfo.value.status_code == http_status
    assert excinfo.value.detail == "..."


def test_via_api_unknown_error_code_falls_back_to_http() -> None:
    """
    Si el backend devuelve un ``error_code`` que no conocemos, el cliente
    lo trata como ``BackendApiError("http", status)`` para no romper la app.
    """
    from utils.api_client import fetch_provider_current_via_api_strict

    err_body = {"ok": False, "error_code": "some_new_error_code_we_dont_know"}
    with patch("utils.api_client.requests.post", return_value=_mock_response(500, err_body)):
        with pytest.raises(BackendApiError) as excinfo:
            fetch_provider_current_via_api_strict("WU", "X", api_key="Y")

    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 500


def test_via_api_non_json_error_response_falls_back_to_http() -> None:
    """Backend roto (proxy 502 con HTML, p.ej.) → BackendApiError('http', status)."""
    from utils.api_client import fetch_provider_current_via_api_strict

    bad_response = MagicMock(spec=requests.Response)
    bad_response.status_code = 502
    bad_response.json.side_effect = ValueError("not JSON")

    with patch("utils.api_client.requests.post", return_value=bad_response):
        with pytest.raises(BackendApiError) as excinfo:
            fetch_provider_current_via_api_strict("WU", "X", api_key="Y")

    assert excinfo.value.kind == "http"


# =====================================================================
# Errores de red al hablar con el BACKEND (no con WU)
# =====================================================================

def test_via_api_backend_timeout_becomes_client_timeout() -> None:
    from utils.api_client import fetch_provider_current_via_api_strict

    with patch("utils.api_client.requests.post", side_effect=requests.Timeout("read timeout")):
        with pytest.raises(BackendApiError) as excinfo:
            fetch_provider_current_via_api_strict("WU", "X", api_key="Y")

    assert excinfo.value.kind == "timeout"


def test_via_api_backend_unreachable_becomes_client_network_error() -> None:
    """
    Si el backend está caído / DNS falla, lo reportamos como ``network``
    aunque técnicamente es un fallo del backend, no de WU. Es el código
    que ya entiende todo el manejo de errores en meteolabx.py.
    """
    from utils.api_client import fetch_provider_current_via_api_strict

    with patch("utils.api_client.requests.post", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(BackendApiError) as excinfo:
            fetch_provider_current_via_api_strict("WU", "X", api_key="Y")

    assert excinfo.value.kind == "network"


# =====================================================================
# Pureza: el cliente API no debe arrastrar streamlit
# =====================================================================

def test_api_client_does_not_import_streamlit() -> None:
    """``utils/api_client.py`` es código de Streamlit pero NO debe importar
    ``streamlit`` directamente; los caches y la sesión son cosa del caller.
    """
    from pathlib import Path
    source = Path("utils/api_client.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


def test_api_client_has_no_legacy_payload_adapters() -> None:
    from pathlib import Path

    source = Path("utils/api_client.py").read_text(encoding="utf-8")
    assert "_denormalize_for_legacy" not in source
    assert "_denormalize_series_for_legacy" not in source
    assert "_denormalize_derivatives_for_legacy" not in source
    assert "_null_to_nan" not in source
    for wrapper in (
        "fetch_wu_current_via_api",
        "fetch_daily_timeseries_via_api",
        "fetch_aemet_current_via_api_strict",
        "fetch_aemet_current_processed_via_api",
        "fetch_aemet_today_series_via_api_strict",
        "fetch_wu_current_processed_via_api",
    ):
        assert f"def {wrapper}(" not in source


def test_wu_frontend_module_has_no_direct_provider_transport() -> None:
    from pathlib import Path

    source = Path("api/weather_underground.py").read_text(encoding="utf-8")
    assert "import requests" not in source
    assert "api.weather.com" not in source
    assert "WU_URL_" not in source
    assert "class WuError" not in source


# Body realista que el endpoint ``/current/processed`` devuelve.
PROCESSED_OK_BODY = {
    "observation": {
        "epoch": 1717255200,
        "time_local": "2026-06-01 12:00:00",
        "time_utc": "2026-06-01T10:00:00Z",
        "lat": 41.387,
        "lon": 2.169,
        "elevation": 12.0,
        "Tc": 22.0, "RH": 65.0, "p_hpa": 1013.0, "Td": 15.13,
        "wind": 8.0, "gust": 12.0, "wind_dir_deg": 180.0,
        "feels_like": 21.7, "heat_index": 24.06, "wind_chill": None,
        "precip_rate": 0.0, "precip_total": 0.4,
        "solar_radiation": 800.0, "uv": 6.0,
    },
    "derivatives": {
        "z": 12.0,
        "p_abs": 1011.5, "p_msl": 1013.0,
        "p_abs_disp": "1012", "p_msl_disp": "1013",
        "dp3": 1.5, "rate_h": 0.5,
        "p_label": "Estable", "p_arrow": "→",
        "inst_mm_h": 0.0, "r5_mm_h": None, "r10_mm_h": None,
        "inst_label": "Sin precipitación",
        "e_sat": 2645.0, "e": 1719.3, "Td_calc": 15.13, "Tw": 17.5,
        "q": 0.0107, "q_gkg": 10.7, "theta": 295.4, "Tv": 296.1, "Te": 322.0,
        "rho": 1.19, "rho_v_gm3": 12.6, "lcl": 850.0,
        "solar_rad": 800.0, "uv": 6.0, "et0": 4.2, "clarity": 0.78,
        "balance": -3.8,
        "has_radiation": True, "has_chart_data": True,
    },
    "warnings": [],
}


def test_processed_via_api_returns_observation_derivatives_warnings() -> None:
    from utils.api_client import fetch_provider_current_processed_via_api

    with patch("utils.api_client.requests.post", return_value=_mock_response(200, PROCESSED_OK_BODY)) as mock_post:
        result = fetch_provider_current_processed_via_api("WU", 
            "ITEST", api_key="fake", sun_tz_name="Europe/Madrid",
        )

    # Endpoint y body correctos
    args, kwargs = mock_post.call_args
    assert args[0].endswith("/v1/observations/current/processed")
    assert kwargs["json"]["provider"] == "WU"
    assert kwargs["json"]["sun_tz_name"] == "Europe/Madrid"

    # Payload completo de dashboard: observación, derivadas y los bloques
    # diarios opcionales (None si el backend probado no los incluyó).
    assert set(result.keys()) == {"observation", "derivatives", "warnings"}

    # Observation: valores y null canónicos preservados.
    obs = result["observation"]
    assert obs["Tc"] == 22.0
    assert obs["epoch"] == 1717255200
    assert obs["wind_chill"] is None

    # Derivatives: 32 campos del shape ProcessedData-like
    deriv = result["derivatives"]
    assert deriv["z"] == 12.0
    assert deriv["clarity"] == pytest.approx(0.78)
    assert deriv["has_chart_data"] is True
    assert deriv["p_abs_disp"] == "1012"
    assert deriv["r5_mm_h"] is None
    assert deriv["r10_mm_h"] is None

    # Warnings
    assert result["warnings"] == []


def test_processed_via_api_passes_max_data_age_minutes() -> None:
    from utils.api_client import fetch_provider_current_processed_via_api

    with patch("utils.api_client.requests.post", return_value=_mock_response(200, PROCESSED_OK_BODY)) as mock_post:
        fetch_provider_current_processed_via_api("WU", "X", api_key="Y", max_data_age_minutes=120.0)

    _, kwargs = mock_post.call_args
    assert kwargs["json"]["max_data_age_minutes"] == 120.0


def test_processed_via_api_passes_wu_calibration() -> None:
    from utils.api_client import fetch_provider_current_processed_via_api

    with patch("utils.api_client.requests.post", return_value=_mock_response(200, PROCESSED_OK_BODY)) as mock_post:
        fetch_provider_current_processed_via_api("WU", 
            "X",
            api_key="Y",
            calibration={"thermometer": 1.5, "barometer": -2.0},
        )

    _, kwargs = mock_post.call_args
    assert kwargs["json"]["calibration"] == {
        "thermometer": 1.5,
        "barometer": -2.0,
    }


def test_weatherlink_station_list_uses_backend() -> None:
    from utils.api_client import fetch_weatherlink_stations_via_api

    body = {"stations": [{"station_id": "123", "station_name": "Casa"}]}
    with patch("utils.api_client.requests.post", return_value=_mock_response(200, body)) as mock_post:
        result = fetch_weatherlink_stations_via_api("key", "secret")

    args, kwargs = mock_post.call_args
    assert args[0].endswith("/v1/stations/weatherlink")
    assert kwargs["json"] == {"api_key": "key", "api_secret": "secret"}
    assert result["ok"] is True
    assert result["stations"][0]["station_name"] == "Casa"


def test_processed_via_api_translates_401_to_wuerror_unauthorized() -> None:
    from utils.api_client import fetch_provider_current_processed_via_api

    err_body = {"ok": False, "error_code": "provider_unauthorized"}
    with patch("utils.api_client.requests.post", return_value=_mock_response(401, err_body)):
        with pytest.raises(BackendApiError) as excinfo:
            fetch_provider_current_processed_via_api("WU", "X", api_key="Y")

    assert excinfo.value.kind == "unauthorized"
    assert excinfo.value.status_code == 401


def test_processed_via_api_backend_timeout_becomes_wuerror_timeout() -> None:
    from utils.api_client import fetch_provider_current_processed_via_api

    with patch("utils.api_client.requests.post", side_effect=requests.Timeout("slow")):
        with pytest.raises(BackendApiError) as excinfo:
            fetch_provider_current_processed_via_api("WU", "X", api_key="Y")
    assert excinfo.value.kind == "timeout"


def test_processed_via_api_propagates_warnings() -> None:
    from utils.api_client import fetch_provider_current_processed_via_api

    body = dict(PROCESSED_OK_BODY)
    body["warnings"] = [{"code": "data_age", "params": {"provider": "WU", "minutes": 120}}]
    with patch("utils.api_client.requests.post", return_value=_mock_response(200, body)):
        result = fetch_provider_current_processed_via_api("WU", "X", api_key="Y")
    assert result["warnings"] == [{"code": "data_age", "params": {"provider": "WU", "minutes": 120}}]
