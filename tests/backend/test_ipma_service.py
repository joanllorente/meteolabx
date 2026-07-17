"""
Tests del servicio puro ``server.services.ipma``.

IPMA sirve un feed global de 24 h anidado por ``timestamp → idEstacao``;
los tests cubren el centinela -99.0, la conversión de clases de rumbo a
grados, la radiación kJ/m² → W/m², la presión absoluta derivada y el
recorte al día local.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import ipma


# Estación real del catálogo local (data/data_estaciones_ipma.json):
# 1210883 Tavira, tz Europe/Lisbon, elevación DEM ~2 m.
STATION = "1210883"
TZ = ZoneInfo("Europe/Lisbon")
NOW_LOCAL = datetime(2026, 7, 15, 12, 30, tzinfo=TZ)


def _ts(hour: int) -> str:
    """Timestamp del feed (UTC sin sufijo) para la hora local dada."""
    dt = NOW_LOCAL.replace(hour=hour, minute=0).astimezone(ZoneInfo("UTC"))
    return dt.strftime("%Y-%m-%dT%H:%M")


PAYLOAD = {
    # Hora vieja (día anterior local): no debe entrar en today/precip_total.
    "2026-07-14T20:00": {
        STATION: {
            "temperatura": 18.0, "humidade": 80.0, "pressao": 1015.0,
            "intensidadeVentoKM": 5.0, "intensidadeVento": 1.4,
            "idDireccVento": 8, "precAcumulada": 1.5, "radiacao": 0.0,
        },
    },
    _ts(10): {
        STATION: {
            "temperatura": 24.0, "humidade": 60.0, "pressao": 1018.0,
            "intensidadeVentoKM": 10.1, "intensidadeVento": 2.8,
            "idDireccVento": 3, "precAcumulada": 0.4, "radiacao": 1800.0,
        },
        "9999999": {
            "temperatura": 30.0, "humidade": 10.0, "pressao": 900.0,
            "intensidadeVentoKM": 0.0, "intensidadeVento": 0.0,
            "idDireccVento": 0, "precAcumulada": 0.0, "radiacao": 0.0,
        },
    },
    _ts(11): {
        STATION: {
            # Presión y humedad a nodata; radiación válida.
            "temperatura": 25.5, "humidade": -99.0, "pressao": -99.0,
            "intensidadeVentoKM": -99.0, "intensidadeVento": 3.0,
            "idDireccVento": 5, "precAcumulada": 0.2, "radiacao": 3600.0,
        },
    },
    # Estación completamente muda en la última hora: la fila se descarta.
    _ts(12): {
        STATION: {
            "temperatura": -99.0, "humidade": -99.0, "pressao": -99.0,
            "intensidadeVentoKM": -99.0, "intensidadeVento": 0.0,
            "idDireccVento": 0, "precAcumulada": -99.0, "radiacao": -99.0,
        },
    },
}


def _client(payload=None, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload if payload is not None else PAYLOAD)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


def test_ipma_service_does_not_import_streamlit() -> None:
    source = Path("server/services/ipma.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


def test_field_sentinel_and_wind_classes() -> None:
    assert math.isnan(ipma._field(-99.0))
    assert ipma._field(12.5) == pytest.approx(12.5)
    assert ipma._wind_dir_deg(1) == pytest.approx(0.0)
    assert ipma._wind_dir_deg(9) == pytest.approx(0.0)
    assert ipma._wind_dir_deg(4) == pytest.approx(135.0)
    assert math.isnan(ipma._wind_dir_deg(0))
    assert math.isnan(ipma._wind_dir_deg(None))


def test_fetch_current_canonical() -> None:
    async def _test():
        async with _client() as client:
            return await ipma.fetch_current(STATION, client=client, now=NOW_LOCAL)

    current = _run(_test())
    # La última hora está muda → current es la fila de las 11 local.
    assert current["Tc"] == pytest.approx(25.5)
    # Humedad/presión de las 11 son nodata → fallback a las 10.
    assert current["RH"] == pytest.approx(60.0)
    assert current["p_hpa"] == pytest.approx(1018.0)
    # Absoluta derivada con la elevación DEM (~2 m) < MSL.
    assert current["p_abs_hpa"] < current["p_hpa"]
    # intensidadeVentoKM nodata a las 11 → fallback m/s × 3.6.
    assert current["wind"] == pytest.approx(10.8)
    assert current["wind_dir_deg"] == pytest.approx(180.0)
    # Radiación: 3600 kJ/m² en la hora → 1000 W/m² medios.
    assert current["solar_radiation"] == pytest.approx(1000.0)
    # Precipitación del día local: 0.4 + 0.2 (la del día anterior no).
    assert current["precip_total"] == pytest.approx(0.6)
    assert math.isnan(current["gust"])
    assert current["station_name"] == "Tavira"
    assert current["elevation"] == pytest.approx(2.0)


def test_fetch_today_series_filters_local_day() -> None:
    async def _test():
        async with _client() as client:
            return await ipma.fetch_today_series(STATION, client=client, now=NOW_LOCAL)

    series = _run(_test())
    assert series["has_data"] is True
    # Solo las 10 y las 11 locales (la del día anterior fuera; la muda, descartada).
    assert len(series["epochs"]) == 2
    assert series["temps"] == pytest.approx([24.0, 25.5])
    assert series["winds"][0] == pytest.approx(10.1)
    assert series["solar_radiations"] == pytest.approx([500.0, 1000.0])
    assert math.isnan(series["pressures"][1])


def test_fetch_recent_series_returns_full_window() -> None:
    async def _test():
        async with _client() as client:
            return await ipma.fetch_recent_series(STATION, client=client, now=NOW_LOCAL)

    series = _run(_test())
    assert series["has_data"] is True
    # Ventana completa del feed: incluye la hora del día anterior.
    assert len(series["epochs"]) == 3
    assert series["temps"][0] == pytest.approx(18.0)
    assert series["pressures"][0] == pytest.approx(1015.0)


def test_fetch_current_without_station_raises() -> None:
    async def _test():
        async with _client() as client:
            return await ipma.fetch_current("0000000", client=client, now=NOW_LOCAL)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_bad_response"


def test_fetch_current_http_error() -> None:
    async def _test():
        async with _client(payload={}, status=503) as client:
            return await ipma.fetch_current(STATION, client=client, now=NOW_LOCAL)

    with pytest.raises(ProviderError) as excinfo:
        _run(_test())
    assert excinfo.value.error_code == "provider_http_error"
