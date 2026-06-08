"""
Tests del fallback "backend → WU directo" en
``api.weather_underground._fetch_current_via_active_source`` y
``_fetch_today_series_via_active_source``.

Comportamiento que blindamos:
- Backend disponible (``USE_API=1``) y responde OK → el frontend usa
  el resultado del backend.
- Backend disponible pero **inalcanzable** (red/timeout) → fallback
  transparente a WU directo. La UI sigue mostrando datos.
- Backend disponible pero responde con error REAL del proveedor
  (401/404/429/etc.) → propaga, NO hay fallback (WU directo daría
  el mismo error y enmascararlo confundiría al usuario).
- ``USE_API=0`` → siempre WU directo, sin tocar el backend.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from api.weather_underground import (
    _fetch_current_via_active_source,
    _fetch_today_series_via_active_source,
    WuError,
)


# =====================================================================
# Helpers
# =====================================================================

CURRENT_OK = {
    "Tc": 22.0, "RH": 65.0, "p_hpa": 1013.0, "Td": 14.0,
    "wind": 8.0, "gust": 12.0, "feels_like": 22.0,
    "heat_index": float("nan"), "wind_chill": float("nan"),
    "precip_rate": 0.0, "precip_total": 0.4, "wind_dir_deg": 180.0,
    "solar_radiation": 800.0, "uv": 6.0,
    "epoch": 1717255200,
    "time_local": "2026-06-01 12:00:00",
    "time_utc": "2026-06-01T10:00:00Z",
    "lat": 41.387, "lon": 2.169, "elevation": 12.0,
}

SERIES_OK = {
    "epochs": [1, 2, 3],
    "temps": [20.0, 21.0, 22.0],
    "humidities": [60.0, 58.0, 55.0],
    "dewpts": [12.0, 12.0, 13.0],
    "pressures": [1013.0, 1013.0, 1013.0],
    "uv_indexes": [3.0, 4.0, 5.0],
    "solar_radiations": [200.0, 400.0, 600.0],
    "winds": [5.0, 5.0, 6.0],
    "gusts": [10.0, 10.0, 11.0],
    "wind_dirs": [180.0, 180.0, 190.0],
    "lat": 41.387, "lon": 2.169,
    "has_data": True,
}


# =====================================================================
# /current: backend OK
# =====================================================================

def test_current_uses_backend_when_flag_on_and_backend_ok(monkeypatch) -> None:
    """Camino feliz: backend disponible → se usa su respuesta."""
    monkeypatch.setenv("METEOLABX_USE_API", "1")

    backend_calls = direct_calls = 0

    def fake_via_api(station_id, api_key):
        nonlocal backend_calls
        backend_calls += 1
        return CURRENT_OK

    def fake_direct(station_id, api_key):
        nonlocal direct_calls
        direct_calls += 1
        return CURRENT_OK

    with patch("utils.api_client.fetch_wu_current_via_api", side_effect=fake_via_api), \
         patch("api.weather_underground.fetch_wu_current", side_effect=fake_direct):
        result = _fetch_current_via_active_source("X", "Y")

    assert result == CURRENT_OK
    assert backend_calls == 1
    assert direct_calls == 0, "No debería haber llamado a WU directo"


# =====================================================================
# /current: backend inalcanzable → fallback transparente
# =====================================================================

@pytest.mark.parametrize("backend_error_kind", ["network", "timeout"])
def test_current_falls_back_to_direct_when_backend_unreachable(
    monkeypatch, backend_error_kind: str
) -> None:
    """
    Si el cliente del backend lanza ``WuError('network')`` o
    ``WuError('timeout')``, caemos a WU directo SIN propagar el error.
    """
    monkeypatch.setenv("METEOLABX_USE_API", "1")

    def fake_via_api(station_id, api_key):
        raise WuError(backend_error_kind)

    def fake_direct(station_id, api_key):
        return CURRENT_OK

    with patch("utils.api_client.fetch_wu_current_via_api", side_effect=fake_via_api), \
         patch("api.weather_underground.fetch_wu_current", side_effect=fake_direct):
        result = _fetch_current_via_active_source("X", "Y")

    assert result == CURRENT_OK


# =====================================================================
# /current: error real del proveedor → propaga, NO hay fallback
# =====================================================================

@pytest.mark.parametrize(
    "error_kind,error_status",
    [
        ("unauthorized", 401),
        ("notfound", 404),
        ("ratelimit", 429),
        ("http", 500),
        ("badjson", None),
    ],
)
def test_current_propagates_real_provider_errors_no_fallback(
    monkeypatch, error_kind: str, error_status
) -> None:
    """
    Si el backend devuelve un error REAL del proveedor (401, 404, 429,
    5xx, JSON malformado), propagamos el ``WuError`` tal cual. WU directo
    daría exactamente el mismo error y enmascararlo es peor (el usuario
    no sabría que su API key es mala).
    """
    monkeypatch.setenv("METEOLABX_USE_API", "1")

    def fake_via_api(station_id, api_key):
        raise WuError(error_kind, error_status)

    def fake_direct(station_id, api_key):
        return CURRENT_OK  # debería ignorarse

    with patch("utils.api_client.fetch_wu_current_via_api", side_effect=fake_via_api), \
         patch("api.weather_underground.fetch_wu_current", side_effect=fake_direct) as mock_direct:
        with pytest.raises(WuError) as excinfo:
            _fetch_current_via_active_source("X", "Y")

    assert excinfo.value.kind == error_kind
    mock_direct.assert_not_called()


# =====================================================================
# /current: flag off → siempre WU directo
# =====================================================================

def test_current_uses_direct_wu_when_flag_off(monkeypatch) -> None:
    monkeypatch.delenv("METEOLABX_USE_API", raising=False)

    backend_calls = direct_calls = 0

    def fake_via_api(station_id, api_key):
        nonlocal backend_calls
        backend_calls += 1
        return CURRENT_OK

    def fake_direct(station_id, api_key):
        nonlocal direct_calls
        direct_calls += 1
        return CURRENT_OK

    with patch("utils.api_client.fetch_wu_current_via_api", side_effect=fake_via_api), \
         patch("api.weather_underground.fetch_wu_current", side_effect=fake_direct):
        _fetch_current_via_active_source("X", "Y")

    assert backend_calls == 0
    assert direct_calls == 1


# =====================================================================
# /series/today: backend OK
# =====================================================================

def test_series_uses_backend_when_flag_on_and_backend_ok(monkeypatch) -> None:
    monkeypatch.setenv("METEOLABX_USE_API", "1")

    backend_calls = direct_calls = 0

    def fake_strict(station_id, api_key):
        nonlocal backend_calls
        backend_calls += 1
        return SERIES_OK

    def fake_direct(station_id, api_key):
        nonlocal direct_calls
        direct_calls += 1
        return SERIES_OK

    with patch("utils.api_client.fetch_daily_timeseries_via_api_strict", side_effect=fake_strict), \
         patch("api.weather_underground.fetch_daily_timeseries", side_effect=fake_direct):
        result = _fetch_today_series_via_active_source("X", "Y")

    assert result == SERIES_OK
    assert backend_calls == 1
    assert direct_calls == 0


# =====================================================================
# /series/today: backend inalcanzable → fallback transparente
# =====================================================================

@pytest.mark.parametrize("backend_error_kind", ["network", "timeout"])
def test_series_falls_back_to_direct_when_backend_unreachable(
    monkeypatch, backend_error_kind: str
) -> None:
    monkeypatch.setenv("METEOLABX_USE_API", "1")

    def fake_strict(station_id, api_key):
        raise WuError(backend_error_kind)

    def fake_direct(station_id, api_key):
        return SERIES_OK

    with patch("utils.api_client.fetch_daily_timeseries_via_api_strict", side_effect=fake_strict), \
         patch("api.weather_underground.fetch_daily_timeseries", side_effect=fake_direct):
        result = _fetch_today_series_via_active_source("X", "Y")

    assert result == SERIES_OK


# =====================================================================
# /series/today: error real del proveedor → propaga
# =====================================================================

def test_series_propagates_unauthorized_no_fallback(monkeypatch) -> None:
    """
    A diferencia del legacy ``fetch_daily_timeseries`` (que swallow todo
    a dict vacío), el dispatcher con backend propaga errores reales del
    proveedor. El caller upstream (``fetch_daily_timeseries_session_cached``)
    es quien decide si swallow o no. Esto es importante porque un 401
    real del proveedor indica que la API key del usuario es mala —
    enmascararlo confunde más.
    """
    monkeypatch.setenv("METEOLABX_USE_API", "1")

    def fake_strict(station_id, api_key):
        raise WuError("unauthorized", 401)

    def fake_direct(station_id, api_key):
        return SERIES_OK

    with patch("utils.api_client.fetch_daily_timeseries_via_api_strict", side_effect=fake_strict), \
         patch("api.weather_underground.fetch_daily_timeseries", side_effect=fake_direct) as mock_direct:
        with pytest.raises(WuError) as excinfo:
            _fetch_today_series_via_active_source("X", "Y")

    assert excinfo.value.kind == "unauthorized"
    mock_direct.assert_not_called()


# =====================================================================
# strict variant del cliente de series
# =====================================================================

def test_strict_series_client_raises_on_backend_error() -> None:
    """
    ``fetch_daily_timeseries_via_api_strict`` debe lanzar ``WuError``
    cuando el backend falla (a diferencia del wrapper "safe").
    """
    from utils.api_client import fetch_daily_timeseries_via_api_strict

    response = MagicMock(spec=requests.Response)
    response.status_code = 502
    response.json.return_value = {"ok": False, "error_code": "provider_network_error"}

    with patch("utils.api_client.requests.post", return_value=response):
        with pytest.raises(WuError) as excinfo:
            fetch_daily_timeseries_via_api_strict("X", "Y")

    assert excinfo.value.kind == "network"


def test_safe_series_client_swallows_to_empty_dict() -> None:
    """``fetch_daily_timeseries_via_api`` (safe) sigue devolviendo dict vacío."""
    from utils.api_client import fetch_daily_timeseries_via_api

    with patch("utils.api_client.requests.post", side_effect=requests.ConnectionError("nope")):
        result = fetch_daily_timeseries_via_api("X", "Y")

    assert result["has_data"] is False
    assert result["epochs"] == []
