"""
Tests del servicio puro ``server.services.nws``.

NWS sirve GeoJSON con unidades por campo; los tests cubren conversión
de unidades, derivación de presiones y la combinación latest + ventana
del día.
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
from server.services import nws


# Estación real del catálogo: HPN09 "Plymouth", lat 44.98992,
# lon -93.47913, elev 297.4848, tz America/Chicago.
STATION = "HPN09"
ELEVATION = 297.4848
TZ = ZoneInfo("America/Chicago")

NOW_LOCAL = datetime(2026, 6, 10, 12, 0, tzinfo=TZ)


def _q(value, unit):
    return {"value": value, "unitCode": unit}


def _feature(hour: int, minute: int = 0, **props) -> dict:
    ts = NOW_LOCAL.replace(hour=hour, minute=minute).astimezone(ZoneInfo("UTC"))
    base = {
        "properties": {
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            **props,
        },
        "geometry": {"type": "Point", "coordinates": [-93.47913, 44.98992]},
    }
    return base


LATEST_FEATURE = _feature(
    11, 55,
    temperature=_q(22.0, "wmoUnit:degC"),
    dewpoint=_q(14.0, "wmoUnit:degC"),
    relativeHumidity=_q(60.0, "wmoUnit:percent"),
    seaLevelPressure=_q(101500.0, "wmoUnit:Pa"),
    windSpeed=_q(5.0, "wmoUnit:m_s-1"),
    windGust=_q(10.0, "wmoUnit:m_s-1"),
    windDirection=_q(180.0, "wmoUnit:degree_(angle)"),
)

DAY_OBSERVATIONS = {
    "features": [
        _feature(
            10,
            temperature=_q(68.0, "wmoUnit:degF"),  # 20 °C
            relativeHumidity=_q(65.0, "wmoUnit:percent"),
            barometricPressure=_q(98000.0, "wmoUnit:Pa"),  # 980 hPa abs
            precipitationLastHour=_q(0.1, "wmoUnit:in"),   # 2.54 mm
        ),
        _feature(
            11,
            temperature=_q(21.0, "wmoUnit:degC"),
            relativeHumidity=_q(62.0, "wmoUnit:percent"),
            seaLevelPressure=_q(101450.0, "wmoUnit:Pa"),
            precipitationLastHour=_q(1.0, "wmoUnit:mm"),
            windSpeed=_q(10.0, "wmoUnit:km_h-1"),
        ),
    ]
}


def _routing_client(
    *,
    latest=None,
    observations=None,
    latest_status: int = 200,
    observations_status: int = 200,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/observations/latest"):
            return httpx.Response(latest_status, json=latest or LATEST_FEATURE)
        if "/observations" in path:
            return httpx.Response(observations_status, json=observations or DAY_OBSERVATIONS)
        return httpx.Response(404, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


# =====================================================================
# Pureza + conversiones
# =====================================================================

def test_nws_service_does_not_import_streamlit() -> None:
    source = Path("server/services/nws.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


@pytest.mark.parametrize(
    "value,unit,expected",
    [
        (22.0, "wmoUnit:degC", 22.0),
        (68.0, "wmoUnit:degF", 20.0),
        (295.15, "wmoUnit:K", 22.0),
    ],
)
def test_to_celsius(value, unit, expected) -> None:
    assert nws._to_celsius(value, unit) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value,unit,expected",
    [
        (5.0, "wmoUnit:m_s-1", 18.0),
        (36.0, "wmoUnit:km_h-1", 36.0),
        (10.0, "wmoUnit:knot", 18.52),
    ],
)
def test_to_kmh(value, unit, expected) -> None:
    assert nws._to_kmh(value, unit) == pytest.approx(expected)


def test_to_hpa_from_pascal() -> None:
    assert nws._to_hpa(101500.0, "wmoUnit:Pa") == pytest.approx(1015.0)


def test_station_meta_includes_tz() -> None:
    lat, lon, elev, name, tz = nws._station_meta(STATION)
    assert lat == pytest.approx(44.98992)
    assert elev == pytest.approx(ELEVATION)
    assert name == "Plymouth"
    assert tz == "America/Chicago"


def test_parse_feature_derives_missing_pressure() -> None:
    row = nws._parse_feature(
        _feature(10, seaLevelPressure=_q(101500.0, "wmoUnit:Pa")),
        elevation_m=ELEVATION,
    )
    assert row["p_msl_hpa"] == pytest.approx(1015.0)
    assert row["p_abs_hpa"] == pytest.approx(1015.0 / math.exp(ELEVATION / 8000.0))


# =====================================================================
# fetch_current
# =====================================================================

def test_fetch_current_prefers_latest_with_day_fallback() -> None:
    client = _routing_client()
    result = _run(nws.fetch_current(STATION, client=client, now=NOW_LOCAL))

    # Valores del feature "latest"
    assert result["Tc"] == pytest.approx(22.0)
    assert result["Td"] == pytest.approx(14.0)  # dewpoint nativo
    assert result["wind"] == pytest.approx(18.0)
    assert result["p_hpa"] == pytest.approx(1015.0)
    # Absoluta derivada de MSL con la altitud
    assert result["p_abs_hpa"] == pytest.approx(1015.0 / math.exp(ELEVATION / 8000.0))

    # Precipitación: suma de precipitationLastHour del día (2.54 + 1.0)
    assert result["precip_total"] == pytest.approx(3.54)

    assert result["station_name"] == "Plymouth"
    assert math.isnan(result["solar_radiation"])


def test_fetch_current_falls_back_to_day_rows_when_latest_fails() -> None:
    client = _routing_client(latest_status=500)
    result = _run(nws.fetch_current(STATION, client=client, now=NOW_LOCAL))
    # Última fila del día (hora 11)
    assert result["Tc"] == pytest.approx(21.0)
    assert result["wind"] == pytest.approx(10.0)  # ya en km/h


def test_fetch_current_station_not_found() -> None:
    client = _routing_client(latest_status=404, observations_status=404)
    with pytest.raises(ProviderError) as excinfo:
        _run(nws.fetch_current(STATION, client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "station_not_found"


def test_fetch_current_empty_everything_is_bad_response() -> None:
    client = _routing_client(latest={"properties": {}}, observations={"features": []})
    with pytest.raises(ProviderError) as excinfo:
        _run(nws.fetch_current(STATION, client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_bad_response"


# =====================================================================
# fetch_today_series
# =====================================================================

def test_fetch_today_series_normalizes_units_and_dewpoints() -> None:
    client = _routing_client()
    result = _run(nws.fetch_today_series(STATION, client=client, now=NOW_LOCAL))

    assert result["has_data"] is True
    assert len(result["epochs"]) == 2
    # °F convertido
    assert result["temps"][0] == pytest.approx(20.0)
    # MSL derivada de absoluta en la primera fila
    assert result["pressures"][0] == pytest.approx(980.0 * math.exp(ELEVATION / 8000.0))
    # MSL nativa en la segunda
    assert result["pressures"][1] == pytest.approx(1014.5)
    assert result["lat"] == pytest.approx(44.98992)


def test_fetch_today_series_empty() -> None:
    client = _routing_client(observations={"features": []})
    result = _run(nws.fetch_today_series(STATION, client=client, now=NOW_LOCAL))
    assert result["has_data"] is False
