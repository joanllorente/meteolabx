"""
Tests del cliente del frontend para AEMET y del dispatch en
``services.aemet``.

Cubre:
- ``utils.api_client.fetch_aemet_current_via_api_strict``: shape de la
  petición, parsing del response, traducción de errores.
- ``services.aemet._translate_backend_to_legacy_aemet_shape``:
  traducción del shape canónico al que ``parse_aemet_data`` produce.
- ``services.aemet._get_aemet_data_via_active_source``: dispatch
  backend/legacy con fallback transparente.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest
import requests

from api.weather_underground import WuError


# =====================================================================
# fetch_aemet_current_via_api_strict
# =====================================================================

def _mock_response(status: int, json_body=None) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status
    response.json.return_value = json_body if json_body is not None else {}
    return response


# Payload realista que devuelve el endpoint /v1/observations/current
# cuando el provider es AEMET.
BACKEND_AEMET_RESPONSE = {
    "epoch": 1717255200,
    "time_local": "2026-06-01T15:00:00+0000",
    "time_utc": "",
    "lat": 41.297, "lon": 2.07, "elevation": 4.0,
    "Tc": 22.4, "RH": 65.0,
    "p_hpa": 1015.2, "p_abs_hpa": 1014.8,
    "Td": 15.13, "feels_like": 21.7, "heat_index": 24.06,
    "wind_chill": None, "precip_rate": None,
    "wind": 18.0, "gust": 29.52, "wind_dir_deg": 180.0,
    "precip_total": 0.4,
    "solar_radiation": None, "uv": None,
    "idema": "0201X",
    "station_name": "BARCELONA AEROPUERTO",
}


def test_aemet_client_sends_provider_aemet_and_empty_api_key() -> None:
    from utils.api_client import fetch_aemet_current_via_api_strict

    with patch(
        "utils.api_client.requests.post",
        return_value=_mock_response(200, BACKEND_AEMET_RESPONSE),
    ) as mock_post:
        fetch_aemet_current_via_api_strict("0201X")

    args, kwargs = mock_post.call_args
    assert args[0].endswith("/v1/observations/current")
    assert kwargs["json"]["provider"] == "AEMET"
    assert kwargs["json"]["station_id"] == "0201X"
    # AEMET ignora api_key; mandamos string vacío explícitamente.
    assert kwargs["json"]["api_key"] == ""


def test_aemet_client_returns_canonical_shape_with_aemet_extras() -> None:
    """
    El cliente debe preservar los campos AEMET-específicos
    (``p_abs_hpa``, ``idema``, ``station_name``) que el wrapper WU no
    contemplaba.
    """
    from utils.api_client import fetch_aemet_current_via_api_strict

    with patch(
        "utils.api_client.requests.post",
        return_value=_mock_response(200, BACKEND_AEMET_RESPONSE),
    ):
        result = fetch_aemet_current_via_api_strict("0201X")

    # Campos comunes (con null→NaN)
    assert result["Tc"] == 22.4
    assert result["p_hpa"] == 1015.2
    assert math.isnan(result["solar_radiation"])  # null → NaN

    # AEMET-específicos preservados
    assert result["p_abs_hpa"] == 1014.8
    assert result["idema"] == "0201X"
    assert result["station_name"] == "BARCELONA AEROPUERTO"


@pytest.mark.parametrize(
    "http_status,error_code,expected_kind",
    [
        (401, "provider_unauthorized", "unauthorized"),
        (404, "station_not_found", "notfound"),
        (429, "provider_ratelimit", "ratelimit"),
        (504, "provider_timeout", "timeout"),
        (502, "provider_network_error", "network"),
    ],
)
def test_aemet_client_translates_backend_errors_to_wuerror(
    http_status: int, error_code: str, expected_kind: str,
) -> None:
    from utils.api_client import fetch_aemet_current_via_api_strict

    err_body = {"ok": False, "error_code": error_code, "provider": "AEMET"}
    with patch(
        "utils.api_client.requests.post",
        return_value=_mock_response(http_status, err_body),
    ):
        with pytest.raises(WuError) as excinfo:
            fetch_aemet_current_via_api_strict("0201X")

    assert excinfo.value.kind == expected_kind


def test_aemet_client_backend_unreachable_becomes_wuerror_network() -> None:
    from utils.api_client import fetch_aemet_current_via_api_strict

    with patch(
        "utils.api_client.requests.post",
        side_effect=requests.ConnectionError("refused"),
    ):
        with pytest.raises(WuError) as excinfo:
            fetch_aemet_current_via_api_strict("0201X")
    assert excinfo.value.kind == "network"


# =====================================================================
# _translate_backend_to_legacy_aemet_shape
# =====================================================================

def test_translate_backend_to_legacy_shape_adds_missing_keys() -> None:
    """
    La traducción añade las claves legacy que el backend no expone
    (``temp_max``, ``rh_max``, etc. con None) y duplica RH como ``rh``,
    ``wind`` como ``wind_speed_kmh``, ``gust`` como ``gust_max``.
    """
    from services.aemet import _translate_backend_to_legacy_aemet_shape

    result = _translate_backend_to_legacy_aemet_shape(BACKEND_AEMET_RESPONSE)

    # Campos extremos legacy (no en backend): None
    assert result["temp_max"] is None
    assert result["temp_min"] is None
    assert result["rh_max"] is None
    assert result["rh_min"] is None

    # Duplicados legacy
    assert result["rh"] == result["RH"]
    assert result["wind_speed_kmh"] == result["wind"]
    assert result["gust_max"] == result["gust"]


def test_translate_backend_to_legacy_shape_renames_canonical_keys() -> None:
    from services.aemet import _translate_backend_to_legacy_aemet_shape

    result = _translate_backend_to_legacy_aemet_shape(BACKEND_AEMET_RESPONSE)

    # p_abs_hpa → p_station (legacy expects this key)
    assert result["p_station"] == 1014.8

    # time_local → fint
    assert result["fint"] == "2026-06-01T15:00:00+0000"

    # station_name → ubi
    assert result["ubi"] == "BARCELONA AEROPUERTO"

    # Campos no renombrados (siguen igual)
    assert result["Tc"] == 22.4
    assert result["idema"] == "0201X"


def test_translate_backend_to_legacy_shape_preserves_derived_values() -> None:
    """
    Td/feels_like/heat_index del backend (ya calculados por
    add_basic_derived) llegan al shape legacy y NO se sobrescriben con
    NaN. Downstream ``process_standard_provider`` los recalculará con
    las mismas fórmulas → resultado idéntico.
    """
    from services.aemet import _translate_backend_to_legacy_aemet_shape

    result = _translate_backend_to_legacy_aemet_shape(BACKEND_AEMET_RESPONSE)

    assert result["Td"] == pytest.approx(15.13)
    assert result["feels_like"] == pytest.approx(21.7)
    assert result["heat_index"] == pytest.approx(24.06)


# =====================================================================
# _get_aemet_data_via_active_source — dispatch
# =====================================================================

# Shape que ``parse_aemet_data`` produce (lo que el caller espera).
LEGACY_AEMET_PARSED = {
    "Tc": 18.0, "RH": 70.0, "p_hpa": 1010.0, "p_station": 1009.0,
    "wind": 10.0, "wind_speed_kmh": 10.0, "wind_dir_deg": 200.0,
    "gust": 15.0, "gust_max": 15.0, "precip_total": 0.0,
    "elevation": 100.0, "epoch": 1717255200,
    "fint": "2026-06-01T15:00:00+0000",
    "lat": 40.4, "lon": -3.7, "ubi": "MADRID RETIRO", "idema": "3195",
    "Td": float("nan"), "solar_radiation": float("nan"), "uv": float("nan"),
    "feels_like": float("nan"), "heat_index": float("nan"),
    "temp_max": None, "temp_min": None, "rh_max": None, "rh_min": None,
    "rh": 70.0,
}


def test_dispatch_uses_legacy_when_backend_flag_off(monkeypatch) -> None:
    """Sin flag, va por el path legacy (fetch + parse), nunca al backend."""
    from services.aemet import _get_aemet_data_via_active_source

    monkeypatch.delenv("METEOLABX_USE_API", raising=False)

    backend_calls = 0
    legacy_calls = 0

    def fake_backend(station_id):
        nonlocal backend_calls
        backend_calls += 1
        return BACKEND_AEMET_RESPONSE

    def fake_legacy_fetch(idema):
        nonlocal legacy_calls
        legacy_calls += 1
        return {"ta": "18.0", "hr": "70"}  # raw AEMET shape

    def fake_legacy_parse(raw):
        return LEGACY_AEMET_PARSED

    with patch("utils.api_client.fetch_aemet_current_via_api_strict", side_effect=fake_backend), \
         patch("services.aemet.fetch_aemet_station_data", side_effect=fake_legacy_fetch), \
         patch("services.aemet.parse_aemet_data", side_effect=fake_legacy_parse):
        result = _get_aemet_data_via_active_source("3195")

    assert backend_calls == 0
    assert legacy_calls == 1
    assert result == LEGACY_AEMET_PARSED


def test_dispatch_uses_backend_when_flag_on_and_backend_ok(monkeypatch) -> None:
    from services.aemet import _get_aemet_data_via_active_source

    monkeypatch.setenv("METEOLABX_USE_API", "1")

    backend_calls = 0
    legacy_calls = 0

    def fake_backend(station_id):
        nonlocal backend_calls
        backend_calls += 1
        return BACKEND_AEMET_RESPONSE

    def fake_legacy_fetch(idema):
        nonlocal legacy_calls
        legacy_calls += 1
        return {"ta": "18.0"}

    with patch("utils.api_client.fetch_aemet_current_via_api_strict", side_effect=fake_backend), \
         patch("services.aemet.fetch_aemet_station_data", side_effect=fake_legacy_fetch):
        result = _get_aemet_data_via_active_source("0201X")

    assert backend_calls == 1
    assert legacy_calls == 0
    # Shape traducido del backend
    assert result["Tc"] == 22.4
    assert result["ubi"] == "BARCELONA AEROPUERTO"


@pytest.mark.parametrize("backend_error_kind", ["network", "timeout"])
def test_dispatch_falls_back_to_legacy_when_backend_unreachable(
    monkeypatch, backend_error_kind: str,
) -> None:
    from services.aemet import _get_aemet_data_via_active_source

    monkeypatch.setenv("METEOLABX_USE_API", "1")

    def fake_backend(station_id):
        raise WuError(backend_error_kind)

    def fake_legacy_fetch(idema):
        return {"ta": "18.0"}  # raw

    def fake_legacy_parse(raw):
        return LEGACY_AEMET_PARSED

    with patch("utils.api_client.fetch_aemet_current_via_api_strict", side_effect=fake_backend), \
         patch("services.aemet.fetch_aemet_station_data", side_effect=fake_legacy_fetch), \
         patch("services.aemet.parse_aemet_data", side_effect=fake_legacy_parse):
        result = _get_aemet_data_via_active_source("3195")

    assert result == LEGACY_AEMET_PARSED  # legacy path ejecutado


@pytest.mark.parametrize(
    "real_provider_error",
    ["unauthorized", "notfound", "ratelimit", "http", "badjson"],
)
def test_dispatch_propagates_real_provider_errors_as_runtime_error(
    monkeypatch, real_provider_error: str,
) -> None:
    """
    Si el backend devuelve un error REAL del proveedor (no de red),
    NO hacemos fallback al legacy (que daría el mismo error). En su
    lugar lo convertimos a RuntimeError, contrato esperado por
    ``get_aemet_data``.
    """
    from services.aemet import _get_aemet_data_via_active_source

    monkeypatch.setenv("METEOLABX_USE_API", "1")

    def fake_backend(station_id):
        raise WuError(real_provider_error, status_code=401)

    legacy_calls = 0

    def fake_legacy_fetch(idema):
        nonlocal legacy_calls
        legacy_calls += 1
        return {"ta": "18.0"}

    with patch("utils.api_client.fetch_aemet_current_via_api_strict", side_effect=fake_backend), \
         patch("services.aemet.fetch_aemet_station_data", side_effect=fake_legacy_fetch):
        with pytest.raises(RuntimeError):
            _get_aemet_data_via_active_source("3195")

    # No hay fallback al legacy para errores reales del proveedor.
    assert legacy_calls == 0


# =====================================================================
# fetch_aemet_today_series_with_lookback — hook backend series
# =====================================================================

@pytest.fixture(autouse=True)
def _clear_aemet_series_cache():
    """
    ``fetch_aemet_today_series_with_lookback`` está decorada con
    ``@st.cache_data(ttl=600)``. Entre tests parametrizados con el mismo
    ``idema`` el cache devolvería el resultado del primero. Lo limpiamos
    antes y después de cada test para garantizar aislamiento.
    """
    from services.aemet import fetch_aemet_today_series_with_lookback
    try:
        fetch_aemet_today_series_with_lookback.clear()
    except Exception:
        pass
    yield
    try:
        fetch_aemet_today_series_with_lookback.clear()
    except Exception:
        pass


# Shape canónico que devuelve el backend AEMET /series/today
BACKEND_AEMET_SERIES_RESPONSE = {
    "epochs": [],   # se rellenará en cada test según necesite
    "temps": [],
    "humidities": [],
    "dewpts": [],
    "pressures": [],
    "uv_indexes": [],
    "solar_radiations": [],
    "winds": [],
    "gusts": [],
    "wind_dirs": [],
    "lat": 41.297,
    "lon": 2.07,
    "has_data": True,
}


def _make_backend_series_response(*, epochs, temps=None, humidities=None,
                                   pressures=None, winds=None, gusts=None,
                                   wind_dirs=None):
    """Crea un dict del backend con arrays paralelos para tests."""
    n = len(epochs)
    fill = lambda v: ([v] * n) if not isinstance(v, list) else v
    return {
        "epochs": list(epochs),
        "temps": fill(temps if temps is not None else float("nan")),
        "humidities": fill(humidities if humidities is not None else float("nan")),
        "dewpts": [None] * n,
        "pressures": fill(pressures if pressures is not None else float("nan")),
        "uv_indexes": [None] * n,
        "solar_radiations": [None] * n,
        "winds": fill(winds if winds is not None else float("nan")),
        "gusts": fill(gusts if gusts is not None else float("nan")),
        "wind_dirs": fill(wind_dirs if wind_dirs is not None else float("nan")),
        "lat": 41.297,
        "lon": 2.07,
        "has_data": n > 0,
    }


def test_today_series_uses_backend_when_flag_on_and_window_filters_correctly(monkeypatch) -> None:
    """
    Con USE_API=1 y backend OK, la serie viene del backend. Además se
    aplica la ventana temporal [día actual, día actual + 1).
    """
    import time as _time
    from services.aemet import fetch_aemet_today_series_with_lookback

    monkeypatch.setenv("METEOLABX_USE_API", "1")

    # Generamos 3 epochs alrededor del momento actual: uno antes de hoy
    # (debería filtrarse), dos dentro de hoy.
    now = _time.time()
    yesterday = int(now - 36 * 3600)  # 36 h atrás → ayer
    today_morning = int(now - 1 * 3600)
    today_now = int(now)

    backend_dict = _make_backend_series_response(
        epochs=[yesterday, today_morning, today_now],
        temps=[15.0, 18.0, 20.0],
        humidities=[80.0, 70.0, 65.0],
        winds=[5.0, 6.0, 7.0],
    )

    def fake_backend_series(station_id):
        return backend_dict

    legacy_called = False

    def fake_legacy_fetch(station):
        nonlocal legacy_called
        legacy_called = True
        return []

    with patch(
        "utils.api_client.fetch_aemet_today_series_via_api_strict",
        side_effect=fake_backend_series,
    ), patch("services.aemet.fetch_aemet_daily_timeseries", side_effect=fake_legacy_fetch):
        result = fetch_aemet_today_series_with_lookback("3195", hours_before_start=0)

    assert legacy_called is False, "Legacy no debe llamarse cuando backend OK"
    assert result["has_data"] is True
    # Solo los puntos dentro de la ventana [día actual, día+1)
    assert len(result["epochs"]) == 2
    assert result["temps"] == [18.0, 20.0]
    assert result["humidities"] == [70.0, 65.0]
    assert result["winds"] == [6.0, 7.0]
    # precips se rellena con NaN (backend no expone per-record); confirmamos
    # que está la lista paralela del mismo largo.
    assert len(result["precips"]) == len(result["epochs"])


@pytest.mark.parametrize("backend_error_kind", ["network", "timeout"])
def test_today_series_falls_back_to_legacy_when_backend_unreachable(
    monkeypatch, backend_error_kind: str,
) -> None:
    """
    Backend caído o lento → fallback transparente a AEMET directo.
    El legacy debe llamarse y su resultado es lo que se devuelve.
    """
    from services.aemet import fetch_aemet_today_series_with_lookback

    monkeypatch.setenv("METEOLABX_USE_API", "1")

    def fake_backend_series(station_id):
        raise WuError(backend_error_kind)

    legacy_called = False

    def fake_legacy_fetch(station):
        nonlocal legacy_called
        legacy_called = True
        # Lista vacía → _build_aemet_local_window_series devuelve empty
        return []

    with patch(
        "utils.api_client.fetch_aemet_today_series_via_api_strict",
        side_effect=fake_backend_series,
    ), patch("services.aemet.fetch_aemet_daily_timeseries", side_effect=fake_legacy_fetch):
        result = fetch_aemet_today_series_with_lookback("3195")

    assert legacy_called is True
    assert result["has_data"] is False
    assert result["epochs"] == []


@pytest.mark.parametrize(
    "real_provider_error",
    ["unauthorized", "notfound", "ratelimit", "http", "badjson"],
)
def test_today_series_falls_back_to_legacy_on_real_provider_errors(
    monkeypatch, real_provider_error: str,
) -> None:
    """
    A diferencia de ``get_aemet_data`` (donde un error real se propaga
    como RuntimeError), aquí degradamos al legacy. AEMET es notoriamente
    intermitente y el legacy tiene reintentos propios; preferimos serie
    vacía o legacy a romper la pestaña de tendencias.
    """
    from services.aemet import fetch_aemet_today_series_with_lookback

    monkeypatch.setenv("METEOLABX_USE_API", "1")

    def fake_backend_series(station_id):
        raise WuError(real_provider_error, status_code=401)

    legacy_called = False

    def fake_legacy_fetch(station):
        nonlocal legacy_called
        legacy_called = True
        return []

    with patch(
        "utils.api_client.fetch_aemet_today_series_via_api_strict",
        side_effect=fake_backend_series,
    ), patch("services.aemet.fetch_aemet_daily_timeseries", side_effect=fake_legacy_fetch):
        fetch_aemet_today_series_with_lookback("3195")

    assert legacy_called is True


def test_today_series_uses_legacy_when_flag_off(monkeypatch) -> None:
    """Sin flag, el backend ni se intenta — directo al legacy."""
    from services.aemet import fetch_aemet_today_series_with_lookback

    monkeypatch.delenv("METEOLABX_USE_API", raising=False)

    backend_called = False

    def fake_backend_series(station_id):
        nonlocal backend_called
        backend_called = True
        return _make_backend_series_response(epochs=[])

    legacy_called = False

    def fake_legacy_fetch(station):
        nonlocal legacy_called
        legacy_called = True
        return []

    with patch(
        "utils.api_client.fetch_aemet_today_series_via_api_strict",
        side_effect=fake_backend_series,
    ), patch("services.aemet.fetch_aemet_daily_timeseries", side_effect=fake_legacy_fetch):
        fetch_aemet_today_series_with_lookback("3195")

    assert backend_called is False, "Backend no debe llamarse sin flag"
    assert legacy_called is True


# =====================================================================
# fetch_aemet_today_series_via_api_strict — cliente backend
# =====================================================================

def test_aemet_series_client_sends_provider_aemet() -> None:
    from utils.api_client import fetch_aemet_today_series_via_api_strict

    backend_body = _make_backend_series_response(
        epochs=[1717255200], temps=[20.0],
    )
    with patch(
        "utils.api_client.requests.post",
        return_value=_mock_response(200, backend_body),
    ) as mock_post:
        result = fetch_aemet_today_series_via_api_strict("0201X")

    args, kwargs = mock_post.call_args
    assert args[0].endswith("/v1/observations/series/today")
    assert kwargs["json"]["provider"] == "AEMET"
    assert kwargs["json"]["station_id"] == "0201X"
    assert kwargs["json"]["api_key"] == ""
    # null en arrays → NaN
    assert math.isnan(result["dewpts"][0])
    assert result["temps"] == [20.0]


def test_aemet_series_client_translates_backend_error() -> None:
    from utils.api_client import fetch_aemet_today_series_via_api_strict

    err_body = {"ok": False, "error_code": "provider_unauthorized"}
    with patch(
        "utils.api_client.requests.post",
        return_value=_mock_response(401, err_body),
    ):
        with pytest.raises(WuError) as excinfo:
            fetch_aemet_today_series_via_api_strict("0201X")
    assert excinfo.value.kind == "unauthorized"


# =====================================================================
# fetch_aemet_current_processed_via_api — cliente backend /processed
# =====================================================================

BACKEND_AEMET_PROCESSED_RESPONSE = {
    "observation": {
        "epoch": 1717255200,
        "time_local": "2026-06-01T15:00:00+0000",
        "time_utc": "",
        "lat": 41.297, "lon": 2.07, "elevation": 4.0,
        "Tc": 22.4, "RH": 65.0,
        "p_hpa": 1015.2, "p_abs_hpa": 1014.8,
        "Td": 15.13, "feels_like": 21.7, "heat_index": 24.06,
        "wind_chill": None, "precip_rate": None,
        "wind": 18.0, "gust": 29.52, "wind_dir_deg": 180.0,
        "precip_total": 0.4,
        "solar_radiation": None, "uv": None,
        "idema": "0201X",
        "station_name": "BARCELONA AEROPUERTO",
    },
    "derivatives": {
        "z": 4.0,
        "p_abs": 1014.8, "p_msl": 1015.2,
        "p_abs_disp": "1014.8", "p_msl_disp": "1015.2",
        "dp3": 1.2, "rate_h": 0.4,
        "p_label": "Estable", "p_arrow": "→",
        "inst_mm_h": 0.0, "r5_mm_h": None, "r10_mm_h": None,
        "inst_label": "Sin precipitación",
        "e_sat": 2645.0, "e": 1719.3, "Td_calc": 15.13, "Tw": 17.5,
        "q": 0.0107, "q_gkg": 10.7, "theta": 295.4, "Tv": 296.1, "Te": 322.0,
        "rho": 1.21, "rho_v_gm3": 12.6, "lcl": 850.0,
        "solar_rad": None, "uv": None, "et0": None, "clarity": None, "balance": None,
        "has_radiation": False, "has_chart_data": True,
    },
    "warnings": [],
}


def test_aemet_processed_client_sends_provider_aemet_with_empty_api_key() -> None:
    from utils.api_client import fetch_aemet_current_processed_via_api

    with patch(
        "utils.api_client.requests.post",
        return_value=_mock_response(200, BACKEND_AEMET_PROCESSED_RESPONSE),
    ) as mock_post:
        fetch_aemet_current_processed_via_api(
            "0201X", sun_tz_name="Europe/Madrid", max_data_age_minutes=60.0,
        )

    args, kwargs = mock_post.call_args
    assert args[0].endswith("/v1/observations/current/processed")
    body = kwargs["json"]
    assert body["provider"] == "AEMET"
    assert body["station_id"] == "0201X"
    assert body["api_key"] == ""  # AEMET ignora; el backend usa la del servidor
    assert body["sun_tz_name"] == "Europe/Madrid"
    assert body["max_data_age_minutes"] == 60.0


def test_aemet_processed_client_returns_three_blocks_with_aemet_extras() -> None:
    """
    El cliente devuelve {observation, derivatives, warnings}. La sección
    ``observation`` mantiene los campos extras de AEMET (p_abs_hpa,
    idema, station_name) que el wrapper WU no contemplaba.
    """
    from utils.api_client import fetch_aemet_current_processed_via_api

    with patch(
        "utils.api_client.requests.post",
        return_value=_mock_response(200, BACKEND_AEMET_PROCESSED_RESPONSE),
    ):
        result = fetch_aemet_current_processed_via_api("0201X")

    assert set(result.keys()) == {"observation", "derivatives", "warnings"}

    obs = result["observation"]
    # Campos comunes (con null → NaN)
    assert obs["Tc"] == 22.4
    assert math.isnan(obs["solar_radiation"])
    # AEMET-específicos preservados
    assert obs["p_abs_hpa"] == 1014.8
    assert obs["idema"] == "0201X"
    assert obs["station_name"] == "BARCELONA AEROPUERTO"

    deriv = result["derivatives"]
    # Floats → preservados, null → NaN
    assert deriv["z"] == 4.0
    assert deriv["Td_calc"] == pytest.approx(15.13)
    assert math.isnan(deriv["et0"])  # null → NaN para AEMET (sin radiación)
    assert deriv["has_radiation"] is False


@pytest.mark.parametrize(
    "http_status,error_code,expected_kind",
    [
        (401, "provider_unauthorized", "unauthorized"),
        (404, "station_not_found", "notfound"),
        (429, "provider_ratelimit", "ratelimit"),
        (504, "provider_timeout", "timeout"),
        (502, "provider_network_error", "network"),
    ],
)
def test_aemet_processed_client_translates_backend_errors(
    http_status: int, error_code: str, expected_kind: str,
) -> None:
    from utils.api_client import fetch_aemet_current_processed_via_api

    err_body = {"ok": False, "error_code": error_code, "provider": "AEMET"}
    with patch(
        "utils.api_client.requests.post",
        return_value=_mock_response(http_status, err_body),
    ):
        with pytest.raises(WuError) as excinfo:
            fetch_aemet_current_processed_via_api("0201X")
    assert excinfo.value.kind == expected_kind


def test_aemet_processed_client_backend_unreachable() -> None:
    """Backend caído / DNS fallido → WuError('network')."""
    from utils.api_client import fetch_aemet_current_processed_via_api

    with patch(
        "utils.api_client.requests.post",
        side_effect=requests.ConnectionError("refused"),
    ):
        with pytest.raises(WuError) as excinfo:
            fetch_aemet_current_processed_via_api("0201X")
    assert excinfo.value.kind == "network"
