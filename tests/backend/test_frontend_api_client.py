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

from api.weather_underground import WuError


# =====================================================================
# Configuración por env
# =====================================================================

def test_is_backend_enabled_default_off(monkeypatch) -> None:
    monkeypatch.delenv("METEOLABX_USE_API", raising=False)
    from utils.api_client import is_backend_enabled
    assert is_backend_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "ON"])
def test_is_backend_enabled_truthy_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("METEOLABX_USE_API", value)
    from utils.api_client import is_backend_enabled
    assert is_backend_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_is_backend_enabled_falsy_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("METEOLABX_USE_API", value)
    from utils.api_client import is_backend_enabled
    assert is_backend_enabled() is False


def test_backend_url_default(monkeypatch) -> None:
    monkeypatch.delenv("METEOLABX_API_URL", raising=False)
    from utils.api_client import backend_url
    assert backend_url() == "http://localhost:8000"


def test_backend_url_custom_strips_trailing_slash(monkeypatch) -> None:
    monkeypatch.setenv("METEOLABX_API_URL", "https://api.example.com/")
    from utils.api_client import backend_url
    assert backend_url() == "https://api.example.com"


# =====================================================================
# Helpers para mockear requests.post
# =====================================================================

def _mock_response(status: int, json_body: dict | None = None) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status
    response.json.return_value = json_body if json_body is not None else {}
    return response


# =====================================================================
# Camino feliz: shape correcto + null → NaN
# =====================================================================

def test_via_api_returns_dict_with_legacy_shape() -> None:
    from utils.api_client import fetch_wu_current_via_api

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
        result = fetch_wu_current_via_api("ITEST", "fake_key")

    # Llamada al endpoint correcto con body correcto
    args, kwargs = mock_post.call_args
    assert args[0].endswith("/v1/observations/current")
    assert kwargs["json"] == {"provider": "WU", "station_id": "ITEST", "api_key": "fake_key"}

    # Shape: floats normales se mantienen
    assert result["Tc"] == 22.0
    assert result["wind_dir_deg"] == 180.0
    assert result["epoch"] == 1717255200
    assert result["time_local"] == "2026-06-01 12:00:00"

    # null en JSON → NaN en el dict (lo que espera el resto del frontend)
    assert math.isnan(result["heat_index"])
    assert math.isnan(result["wind_chill"])


def test_via_api_missing_numeric_fields_become_nan() -> None:
    """Si el backend omite un campo numérico, el frontend lo recibe como NaN."""
    from utils.api_client import fetch_wu_current_via_api

    api_body = {"epoch": 1717255200}  # solo lo mínimo

    with patch("utils.api_client.requests.post", return_value=_mock_response(200, api_body)):
        result = fetch_wu_current_via_api("X", "Y")

    for field in ("Tc", "RH", "p_hpa", "wind", "uv", "solar_radiation"):
        assert math.isnan(result[field]), f"campo {field} debería ser NaN"


# =====================================================================
# Mapeo HTTP error → WuError (kind + status_code históricos)
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
    ],
)
def test_via_api_translates_backend_errors_to_wuerror(
    http_status: int, error_code: str, expected_wuerror_kind: str
) -> None:
    from utils.api_client import fetch_wu_current_via_api

    err_body = {"ok": False, "error_code": error_code, "provider": "WU", "detail": "..."}
    with patch("utils.api_client.requests.post", return_value=_mock_response(http_status, err_body)):
        with pytest.raises(WuError) as excinfo:
            fetch_wu_current_via_api("X", "Y")

    assert excinfo.value.kind == expected_wuerror_kind
    # WuError preserva el status_code del proveedor para el manejo legacy
    assert excinfo.value.status_code == http_status


def test_via_api_unknown_error_code_falls_back_to_http() -> None:
    """
    Si el backend devuelve un ``error_code`` que no conocemos, el cliente
    lo trata como ``WuError("http", status)`` para no romper la app.
    """
    from utils.api_client import fetch_wu_current_via_api

    err_body = {"ok": False, "error_code": "some_new_error_code_we_dont_know"}
    with patch("utils.api_client.requests.post", return_value=_mock_response(500, err_body)):
        with pytest.raises(WuError) as excinfo:
            fetch_wu_current_via_api("X", "Y")

    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 500


def test_via_api_non_json_error_response_falls_back_to_http() -> None:
    """Backend roto (proxy 502 con HTML, p.ej.) → WuError('http', status)."""
    from utils.api_client import fetch_wu_current_via_api

    bad_response = MagicMock(spec=requests.Response)
    bad_response.status_code = 502
    bad_response.json.side_effect = ValueError("not JSON")

    with patch("utils.api_client.requests.post", return_value=bad_response):
        with pytest.raises(WuError) as excinfo:
            fetch_wu_current_via_api("X", "Y")

    assert excinfo.value.kind == "http"


# =====================================================================
# Errores de red al hablar con el BACKEND (no con WU)
# =====================================================================

def test_via_api_backend_timeout_becomes_wuerror_timeout() -> None:
    from utils.api_client import fetch_wu_current_via_api

    with patch("utils.api_client.requests.post", side_effect=requests.Timeout("read timeout")):
        with pytest.raises(WuError) as excinfo:
            fetch_wu_current_via_api("X", "Y")

    assert excinfo.value.kind == "timeout"


def test_via_api_backend_unreachable_becomes_wuerror_network() -> None:
    """
    Si el backend está caído / DNS falla, lo reportamos como ``network``
    aunque técnicamente es un fallo del backend, no de WU. Es el código
    que ya entiende todo el manejo de errores en meteolabx.py.
    """
    from utils.api_client import fetch_wu_current_via_api

    with patch("utils.api_client.requests.post", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(WuError) as excinfo:
            fetch_wu_current_via_api("X", "Y")

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


# =====================================================================
# fetch_daily_timeseries_via_api: shape legacy + tolerancia a errores
# =====================================================================

def test_series_via_api_returns_dict_with_legacy_shape() -> None:
    from utils.api_client import fetch_daily_timeseries_via_api

    api_body = {
        "epochs": [1000, 2000],
        "temps": [20.0, 21.0],
        "humidities": [60.0, None],
        "dewpts": [12.0, 13.0],
        "pressures": [1013.0, None],
        "uv_indexes": [None, None],
        "solar_radiations": [200.0, 250.0],
        "winds": [5.0, 6.0],
        "gusts": [10.0, 11.0],
        "wind_dirs": [90.0, 95.0],
        "lat": 41.387,
        "lon": 2.169,
        "has_data": True,
    }

    with patch("utils.api_client.requests.post", return_value=_mock_response(200, api_body)) as mock_post:
        result = fetch_daily_timeseries_via_api("ITEST", "fake")

    # Endpoint correcto
    args, _ = mock_post.call_args
    assert args[0].endswith("/v1/observations/series/today")

    # Shape: epochs intactos, floats normales, null → NaN
    assert result["epochs"] == [1000, 2000]
    assert result["temps"] == [20.0, 21.0]
    assert math.isnan(result["humidities"][1])  # null → NaN
    assert math.isnan(result["pressures"][1])
    assert math.isnan(result["uv_indexes"][0])
    assert result["lat"] == 41.387
    assert result["has_data"] is True


def test_series_via_api_swallows_provider_error_and_returns_empty() -> None:
    """
    El legacy nunca propaga errores en series del día. El cliente del
    frontend mantiene esa semántica: backend error → dict vacío.
    """
    from utils.api_client import fetch_daily_timeseries_via_api

    err_body = {"ok": False, "error_code": "provider_timeout"}
    with patch("utils.api_client.requests.post", return_value=_mock_response(504, err_body)):
        result = fetch_daily_timeseries_via_api("X", "Y")

    assert result["has_data"] is False
    assert result["epochs"] == []
    assert math.isnan(result["lat"])


def test_series_via_api_swallows_network_error_and_returns_empty() -> None:
    """Backend caído / DNS roto → dict vacío, igual que el legacy."""
    from utils.api_client import fetch_daily_timeseries_via_api

    with patch("utils.api_client.requests.post", side_effect=requests.ConnectionError("refused")):
        result = fetch_daily_timeseries_via_api("X", "Y")

    assert result["has_data"] is False
    assert result["epochs"] == []


# =====================================================================
# Feature flag de /processed
# =====================================================================

def test_is_processed_endpoint_enabled_requires_backend_flag(monkeypatch) -> None:
    """Sin ``METEOLABX_USE_API=1`` el flag de processed es falsy."""
    from utils.api_client import is_processed_endpoint_enabled
    monkeypatch.delenv("METEOLABX_USE_API", raising=False)
    monkeypatch.setenv("METEOLABX_USE_PROCESSED_API", "1")
    assert is_processed_endpoint_enabled() is False


def test_is_processed_endpoint_enabled_when_both_flags_on(monkeypatch) -> None:
    from utils.api_client import is_processed_endpoint_enabled
    monkeypatch.setenv("METEOLABX_USE_API", "1")
    monkeypatch.setenv("METEOLABX_USE_PROCESSED_API", "1")
    assert is_processed_endpoint_enabled() is True


def test_is_processed_endpoint_disabled_when_processed_flag_off(monkeypatch) -> None:
    from utils.api_client import is_processed_endpoint_enabled
    monkeypatch.setenv("METEOLABX_USE_API", "1")
    monkeypatch.setenv("METEOLABX_USE_PROCESSED_API", "0")
    assert is_processed_endpoint_enabled() is False


# =====================================================================
# fetch_wu_current_processed_via_api
# =====================================================================

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
    from utils.api_client import fetch_wu_current_processed_via_api

    with patch("utils.api_client.requests.post", return_value=_mock_response(200, PROCESSED_OK_BODY)) as mock_post:
        result = fetch_wu_current_processed_via_api(
            "ITEST", "fake", sun_tz_name="Europe/Madrid",
        )

    # Endpoint y body correctos
    args, kwargs = mock_post.call_args
    assert args[0].endswith("/v1/observations/current/processed")
    assert kwargs["json"]["provider"] == "WU"
    assert kwargs["json"]["sun_tz_name"] == "Europe/Madrid"

    # Shape: tres bloques
    assert set(result.keys()) == {"observation", "derivatives", "warnings"}

    # Observation: floats normales preservados, null → NaN
    obs = result["observation"]
    assert obs["Tc"] == 22.0
    assert obs["epoch"] == 1717255200
    assert math.isnan(obs["wind_chill"])  # null → NaN

    # Derivatives: 32 campos del shape ProcessedData-like
    deriv = result["derivatives"]
    assert deriv["z"] == 12.0
    assert deriv["clarity"] == pytest.approx(0.78)
    assert deriv["has_chart_data"] is True
    assert deriv["p_abs_disp"] == "1012"
    # Floats null → NaN en derivadas
    assert math.isnan(deriv["r5_mm_h"])
    assert math.isnan(deriv["r10_mm_h"])

    # Warnings
    assert result["warnings"] == []


def test_processed_via_api_passes_max_data_age_minutes() -> None:
    from utils.api_client import fetch_wu_current_processed_via_api

    with patch("utils.api_client.requests.post", return_value=_mock_response(200, PROCESSED_OK_BODY)) as mock_post:
        fetch_wu_current_processed_via_api("X", "Y", max_data_age_minutes=120.0)

    _, kwargs = mock_post.call_args
    assert kwargs["json"]["max_data_age_minutes"] == 120.0


def test_processed_via_api_translates_401_to_wuerror_unauthorized() -> None:
    from utils.api_client import fetch_wu_current_processed_via_api

    err_body = {"ok": False, "error_code": "provider_unauthorized"}
    with patch("utils.api_client.requests.post", return_value=_mock_response(401, err_body)):
        with pytest.raises(WuError) as excinfo:
            fetch_wu_current_processed_via_api("X", "Y")

    assert excinfo.value.kind == "unauthorized"
    assert excinfo.value.status_code == 401


def test_processed_via_api_backend_timeout_becomes_wuerror_timeout() -> None:
    from utils.api_client import fetch_wu_current_processed_via_api

    with patch("utils.api_client.requests.post", side_effect=requests.Timeout("slow")):
        with pytest.raises(WuError) as excinfo:
            fetch_wu_current_processed_via_api("X", "Y")
    assert excinfo.value.kind == "timeout"


def test_processed_via_api_propagates_warnings() -> None:
    from utils.api_client import fetch_wu_current_processed_via_api

    body = dict(PROCESSED_OK_BODY)
    body["warnings"] = ["⚠️ Datos de WU con 120 minutos de antigüedad."]
    with patch("utils.api_client.requests.post", return_value=_mock_response(200, body)):
        result = fetch_wu_current_processed_via_api("X", "Y")
    assert result["warnings"] == ["⚠️ Datos de WU con 120 minutos de antigüedad."]
