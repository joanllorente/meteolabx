"""
Tests del contrato de ``POST /v1/climo/dataset`` (serialización del
dataset + extremes) y de su cliente/hook frontend.

Todos los proveedores de climo tienen ya port async puro
(``server/services/*_climo.py``); estos tests ejercitan el contorno del
endpoint mockeando la rama async (Meteocat, que además devuelve
``extremes``). El dispatcher frontend anterior quedó sin uso.
"""

from __future__ import annotations

import io
import math
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient

from server.main import create_app


SAMPLE_DF = pd.DataFrame(
    {
        "date": ["2025-06-01", "2025-06-02"],
        "epoch": [1748736000.0, 1748822400.0],
        "temp_mean": [21.5, float("nan")],
        "temp_max": [27.0, 26.0],
        "temp_min": [16.0, 15.5],
        "precip_total": [0.0, 4.2],
    }
)

# El router importa el servicio async perezosamente
# (``from server.services import meteocat_climo``), así que el patch va
# sobre el atributo del módulo del servicio.
_ASYNC_TARGET = "server.services.meteocat_climo.fetch_climo_dataset"


def _client() -> TestClient:
    return TestClient(create_app())


def _request_body(provider: str = "METEOCAT") -> dict:
    return {
        "provider": provider,
        "station_id": "IBARCE12345",
        "api_key": "K",
        "summary_mode": "monthly",
        "periods": [{"label": "jun 2025", "start": "2025-06-01", "end": "2025-06-30"}],
    }


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn


def test_climo_dataset_roundtrip() -> None:
    captured = {}

    async def fake_fetch(client, station_code, api_key, *, summary_mode, periods, selected_years):
        captured["station_code"] = station_code
        captured["api_key"] = api_key
        captured["periods"] = periods
        return SAMPLE_DF, None

    with patch(_ASYNC_TARGET, side_effect=fake_fetch):
        with _client() as client:
            response = client.post("/v1/climo/dataset", json=_request_body())

    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is True
    assert body["extremes"] is None

    # La rama async recibió los periodos como tuplas (start, end) reales
    assert captured["periods"][0] == (date(2025, 6, 1), date(2025, 6, 30))
    assert captured["station_code"] == "IBARCE12345"
    assert captured["api_key"] == "K"

    # Round-trip del DataFrame con dtypes y NaN preservados
    df = pd.read_json(io.StringIO(body["dataset"]), orient="table")
    assert list(df.columns) == list(SAMPLE_DF.columns)
    assert df["temp_max"].tolist() == [27.0, 26.0]
    assert math.isnan(df["temp_mean"].iloc[1])


def test_climo_dataset_passes_extremes_dict() -> None:
    extremes = {"Mínima de máximas": {"Valor": "3.2 °C", "Fecha": "15/01/2025"}}

    with patch(_ASYNC_TARGET, side_effect=_async_return((SAMPLE_DF, extremes))):
        with _client() as client:
            response = client.post("/v1/climo/dataset", json=_request_body("METEOCAT"))

    assert response.status_code == 200
    assert response.json()["extremes"] == extremes


def test_climo_dataset_weatherlink_passes_api_secret() -> None:
    captured = {}

    async def fake_fetch(
        client,
        station_code,
        api_key,
        api_secret,
        *,
        summary_mode,
        periods,
        selected_years,
    ):
        captured["station_code"] = station_code
        captured["api_key"] = api_key
        captured["api_secret"] = api_secret
        captured["periods"] = periods
        return SAMPLE_DF

    with patch("server.services.weatherlink_climo.fetch_climo_dataset", side_effect=fake_fetch):
        body = _request_body("WEATHERLINK")
        body["api_secret"] = "S"
        with _client() as client:
            response = client.post("/v1/climo/dataset", json=body)

    assert response.status_code == 200
    assert response.json()["has_data"] is True
    assert captured["station_code"] == "IBARCE12345"
    assert captured["api_key"] == "K"
    assert captured["api_secret"] == "S"
    assert captured["periods"][0] == (date(2025, 6, 1), date(2025, 6, 30))


def test_climo_dataset_dispatches_iem_without_credentials() -> None:
    captured = {}

    async def fake_fetch(
        client,
        station_code,
        *,
        summary_mode,
        periods,
        selected_years,
    ):
        captured["station_code"] = station_code
        captured["summary_mode"] = summary_mode
        captured["periods"] = periods
        return SAMPLE_DF

    with patch("server.services.iem_climo.fetch_climo_dataset", side_effect=fake_fetch):
        body = _request_body("IEM")
        body["station_id"] = "TR__ASOS|LTFG"
        body["api_key"] = ""
        with _client() as client:
            response = client.post("/v1/climo/dataset", json=body)

    assert response.status_code == 200
    assert response.json()["has_data"] is True
    assert captured["station_code"] == "TR__ASOS|LTFG"
    assert captured["summary_mode"] == "monthly"
    assert captured["periods"][0] == (date(2025, 6, 1), date(2025, 6, 30))


def test_climo_dataset_empty_dataframe_has_no_data() -> None:
    with patch(_ASYNC_TARGET, side_effect=_async_return((pd.DataFrame(), None))):
        with _client() as client:
            response = client.post("/v1/climo/dataset", json=_request_body())

    body = response.json()
    assert body["has_data"] is False
    assert body["dataset"] is None


def test_climo_dataset_unsupported_provider() -> None:
    with _client() as client:
        response = client.post("/v1/climo/dataset", json=_request_body("NWS"))
    assert response.status_code == 400
    assert response.json()["error_code"] == "unsupported_provider"


def test_climo_dataset_dispatch_error_maps_to_502() -> None:
    async def _boom(*args, **kwargs):
        raise RuntimeError("upstream roto")

    with patch(_ASYNC_TARGET, side_effect=_boom):
        with _client() as client:
            response = client.post("/v1/climo/dataset", json=_request_body())
    assert response.status_code == 502
    assert response.json()["error_code"] == "provider_bad_response"


# =====================================================================
# Cliente frontend + hook del dispatcher
# =====================================================================

def _mock_response(status: int, json_body=None) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status
    response.json.return_value = json_body if json_body is not None else {}
    return response


def test_frontend_client_rebuilds_dataframe() -> None:
    from utils.api_client import fetch_climo_dataset_via_api_strict

    body = {
        "dataset": SAMPLE_DF.to_json(orient="table", date_format="iso"),
        "extremes": {"x": {"y": "z"}},
        "has_data": True,
    }
    with patch(
        "utils.api_client.requests.post",
        return_value=_mock_response(200, body),
    ) as mock_post:
        from services.climograms import ClimogramPeriod

        df, extremes = fetch_climo_dataset_via_api_strict(
            "WU", "IBARCE12345", api_key="K", api_secret="S",
            periods=[ClimogramPeriod(label="jun", start=date(2025, 6, 1), end=date(2025, 6, 30))],
        )

    payload = mock_post.call_args.kwargs["json"]
    assert payload["periods"][0]["start"] == "2025-06-01"
    assert payload["api_secret"] == "S"
    assert isinstance(df, pd.DataFrame)
    assert df["temp_max"].tolist() == [27.0, 26.0]
    assert extremes == {"x": {"y": "z"}}


def test_frontend_client_uses_long_weatherlink_climo_timeout() -> None:
    from utils.api_client import fetch_climo_dataset_via_api_strict

    body = {
        "dataset": SAMPLE_DF.to_json(orient="table", date_format="iso"),
        "extremes": None,
        "has_data": True,
    }
    with patch(
        "utils.api_client.requests.post",
        return_value=_mock_response(200, body),
    ) as mock_post:
        from services.climograms import ClimogramPeriod

        fetch_climo_dataset_via_api_strict(
            "WEATHERLINK",
            "123631",
            api_key="K",
            api_secret="S",
            periods=[ClimogramPeriod(label="may", start=date(2026, 5, 1), end=date(2026, 5, 31))],
        )

    assert mock_post.call_args.kwargs["timeout"] == 180.0


def test_frontend_client_uses_long_iem_climo_timeout() -> None:
    from utils.api_client import fetch_climo_dataset_via_api_strict

    body = {
        "dataset": SAMPLE_DF.to_json(orient="table", date_format="iso"),
        "extremes": None,
        "has_data": True,
    }
    with patch(
        "utils.api_client.requests.post",
        return_value=_mock_response(200, body),
    ) as mock_post:
        from services.climograms import ClimogramPeriod

        fetch_climo_dataset_via_api_strict(
            "IEM",
            "TR__ASOS|LTFG",
            periods=[ClimogramPeriod(label="2025", start=date(2025, 1, 1), end=date(2025, 12, 31))],
        )

    assert mock_post.call_args.kwargs["timeout"] == 180.0


def test_dispatch_propagates_network_error(monkeypatch) -> None:
    from utils.api_errors import BackendApiError
    from utils import historical_dispatch

    monkeypatch.setenv("METEOLABX_USE_API", "1")
    with patch(
        "utils.api_client.requests.post",
        side_effect=requests.ConnectionError("backend down"),
    ):
        with pytest.raises(BackendApiError):
            historical_dispatch.fetch_historical_dataset(
                provider_id="WU",
                station_id="IBARCE12345",
                api_key="K",
                summary_mode="monthly",
                periods=[],
                selected_years=[],
                selected_months=[],
                frost_selected_period="",
                frost_selected_periods=[],
                api_secret="S",
            )



def test_historical_dispatch_has_only_api_contract_arguments() -> None:
    import inspect

    from utils.historical_dispatch import fetch_historical_dataset

    assert tuple(inspect.signature(fetch_historical_dataset).parameters) == (
        "provider_id",
        "station_id",
        "api_key",
        "summary_mode",
        "periods",
        "selected_years",
        "selected_months",
        "frost_selected_period",
        "frost_selected_periods",
        "api_secret",
    )


def test_climo_request_accepts_long_jwt_api_key():
    """Regresión: la api_key de AEMET es un JWT (>256 chars). El schema no
    debe rechazarla con 422 (bug histórico: max_length=256 cortaba el JWT y
    el histórico de AEMET fallaba con 'String should have at most 256')."""
    from server.schemas.climo import ClimoDatasetRequest

    req = ClimoDatasetRequest(
        provider="AEMET",
        station_id="9866C",
        api_key="j" * 350,  # JWT AEMET real ~316 chars
        summary_mode="monthly",
        periods=[{"label": "jun", "start": "2026-06-01", "end": "2026-06-30"}],
        selected_years=[2026],
        selected_months=[6],
    )
    assert len(req.api_key) == 350
