"""
Tests del servicio puro ``server.services.geosphere``.

GeoSphere sirve el dataset TAWES de 10 min con ``timestamps`` globales y
columnas por parámetro; los tests cubren la alineación de columnas, las
conversiones m/s → km/h, el punto de rocío nativo y el binning horario
de la serie reciente.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import geosphere


# Estación real del catálogo local (data/data_estaciones_geosphere.json):
# 11035 Wien/Hohe Warte, 198 m.
STATION = "11035"
TZ = ZoneInfo("Europe/Vienna")
NOW_LOCAL = datetime(2026, 7, 15, 12, 30, tzinfo=TZ)


def _ts(hour: int, minute: int = 0) -> str:
    dt = NOW_LOCAL.replace(hour=hour, minute=minute).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M+00:00")


def _payload():
    # Tres muestras: 10:00, 10:10 y 12:20 local. La última sin presión.
    return {
        "timestamps": [_ts(10, 0), _ts(10, 10), _ts(12, 20)],
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "station": STATION,
                    "parameters": {
                        "TL": {"unit": "°C", "data": [20.0, 20.4, 24.1]},
                        "RF": {"unit": "%", "data": [60.0, 59.0, 48.0]},
                        "PRED": {"unit": "hPa", "data": [1015.0, 1015.2, None]},
                        "P": {"unit": "hPa", "data": [991.0, 991.1, None]},
                        "FF": {"unit": "m/s", "data": [2.0, 2.5, 3.0]},
                        "FFX": {"unit": "m/s", "data": [4.0, 5.0, 7.5]},
                        "DD": {"unit": "°", "data": [180.0, 190.0, 200.0]},
                        "RR": {"unit": "mm", "data": [0.0, 0.3, 0.1]},
                        "GLOW": {"unit": "W/m²", "data": [450.0, 500.0, 780.0]},
                    },
                },
            }
        ],
    }


def _client(payload=None, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload if payload is not None else _payload())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


def test_geosphere_service_does_not_import_streamlit() -> None:
    source = Path("server/services/geosphere.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


def test_fetch_current_canonical() -> None:
    async def _test():
        async with _client() as client:
            return await geosphere.fetch_current(STATION, client=client, now=NOW_LOCAL)

    current = _run(_test())
    assert current["Tc"] == pytest.approx(24.1)
    assert current["RH"] == pytest.approx(48.0)
    # Presión de la última muestra a None → fallback a la anterior.
    assert current["p_hpa"] == pytest.approx(1015.2)
    assert current["p_abs_hpa"] == pytest.approx(991.1)
    # m/s → km/h y racha nativa.
    assert current["wind"] == pytest.approx(10.8)
    assert current["gust"] == pytest.approx(27.0)
    # Punto de rocío DERIVADO de T/HR por el pipeline (criterio de la app),
    # no el TP del proveedor.
    from domain.observation_pipeline import add_basic_derived

    expected_td = add_basic_derived({"Tc": 24.1, "RH": 48.0})["Td"]
    assert current["Td"] == pytest.approx(expected_td)
    assert current["solar_radiation"] == pytest.approx(780.0)
    assert current["precip_total"] == pytest.approx(0.4)
    assert current["station_name"] == "WIEN/HOHE WARTE"
    assert current["elevation"] == pytest.approx(198.0)


def test_fetch_today_series_canonical() -> None:
    async def _test():
        async with _client() as client:
            return await geosphere.fetch_today_series(STATION, client=client, now=NOW_LOCAL)

    series = _run(_test())
    assert series["has_data"] is True
    assert len(series["epochs"]) == 3
    assert series["temps"] == pytest.approx([20.0, 20.4, 24.1])
    assert all(math.isnan(v) for v in series["dewpts"])  # Td lo deriva la app
    assert series["winds"] == pytest.approx([7.2, 9.0, 10.8])
    assert series["gusts"][2] == pytest.approx(27.0)
    assert math.isnan(series["pressures"][2])


def test_fetch_recent_series_bins_hourly() -> None:
    async def _test():
        async with _client() as client:
            return await geosphere.fetch_recent_series(STATION, client=client, now=NOW_LOCAL)

    series = _run(_test())
    assert series["has_data"] is True
    # Las muestras de las 10:00 y 10:10 colapsan en el bucket de las 10.
    assert len(series["epochs"]) == 2
    assert series["temps"] == pytest.approx([20.4, 24.1])
    assert series["pressures"][0] == pytest.approx(1015.2)


def test_fetch_current_without_data_raises() -> None:
    async def _test():
        async with _client(payload={"timestamps": [], "features": []}) as client:
            return await geosphere.fetch_current(STATION, client=client, now=NOW_LOCAL)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_bad_response"


def test_fetch_current_http_error() -> None:
    async def _test():
        async with _client(payload={}, status=503) as client:
            return await geosphere.fetch_current(STATION, client=client, now=NOW_LOCAL)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_http_error"


# ----------------------------------------------------------------------
# Rama KLIMA (estaciones convencionales, dato diario)
# ----------------------------------------------------------------------

def _klima_payload():
    return {
        "timestamps": ["2026-07-13T00:00+00:00", "2026-07-14T00:00+00:00"],
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "station": 18617,
                    "parameters": {
                        "tl_mittel": {"data": [22.4, 23.8]},
                        "tlmax": {"data": [28.0, 30.1]},
                        "tlmin": {"data": [16.2, 17.0]},
                        "rr": {"data": [-1.0, 4.2]},
                        "ffx": {"data": [7.0, 10.0]},
                        "rf_mittel": {"data": [55.0, 60.0]},
                        "p_mittel": {"data": [960.0, 958.5]},
                    },
                },
            }
        ],
    }


def test_klima_station_current_serves_latest_day() -> None:
    async def _test():
        async with _client(payload=_klima_payload()) as client:
            return await geosphere.fetch_current("K18617", client=client, now=NOW_LOCAL)

    current = _run(_test())
    assert current["Tc"] == pytest.approx(23.8)
    assert current["RH"] == pytest.approx(60.0)
    assert current["gust"] == pytest.approx(36.0)
    assert current["precip_total"] == pytest.approx(4.2)
    # p_mittel es presión de estación → MSL derivada por encima.
    assert current["p_abs_hpa"] == pytest.approx(958.5)
    assert current["p_hpa"] > current["p_abs_hpa"]
    assert current["station_name"] == "Treibach-Althofen"


def test_klima_station_rain_sentinel_clamped() -> None:
    async def _test():
        async with _client(payload=_klima_payload()) as client:
            return await geosphere.fetch_recent_series("K18617", client=client, now=NOW_LOCAL)

    series = _run(_test())
    assert series["has_data"] is True
    assert len(series["epochs"]) == 2
    assert series["temps"] == pytest.approx([22.4, 23.8])


def test_klima_station_has_no_today_series() -> None:
    async def _test():
        async with _client(payload=_klima_payload()) as client:
            return await geosphere.fetch_today_series("K18617", client=client, now=NOW_LOCAL)

    series = _run(_test())
    assert series["has_data"] is False
