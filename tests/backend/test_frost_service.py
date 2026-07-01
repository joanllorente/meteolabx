"""
Tests del servicio puro ``server.services.frost``.

Cubre el scoring de variantes (resolución/nivel/calidad), el binning,
la heurística de precipitación (contador vs incrementos) y el fan-out
resiliente por elemento cuando la petición combinada falla (412).
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
from server.services import frost


# Estación real del catálogo: SN100 "PLASSEN", lat 61.1349,
# lon 12.5039, elev 333.0.
STATION = "SN100"
ELEVATION = 333.0
TZ = ZoneInfo("Europe/Oslo")

NOW_LOCAL = datetime(2026, 6, 10, 12, 0, tzinfo=TZ)


def _ref(hour: int, minute: int = 0) -> str:
    dt = NOW_LOCAL.replace(hour=hour, minute=minute).astimezone(ZoneInfo("UTC"))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _obs(element: str, value: float, *, resolution: str = "PT10M",
         level: float | None = None, quality: int = 0) -> dict:
    out = {
        "elementId": element,
        "value": value,
        "timeResolution": resolution,
        "qualityCode": quality,
    }
    if level is not None:
        out["level"] = {"levelType": "height_above_ground", "unit": "m", "value": level}
    return out


LATEST_PAYLOAD = {
    "data": [
        {
            "sourceId": "SN100:0",
            "referenceTime": _ref(11, 50),
            "observations": [
                _obs("air_temperature", 18.0, level=2.0),
                _obs("relative_humidity", 55.0, level=2.0),
                _obs("surface_air_pressure", 975.0),
                _obs("wind_speed", 5.0, level=10.0),
                _obs("wind_speed_of_gust", 10.0, level=10.0),
                _obs("wind_from_direction", 230.0, level=10.0),
            ],
        }
    ]
}

TODAY_PAYLOAD = {
    "data": [
        {
            "sourceId": "SN100:0",
            "referenceTime": _ref(10),
            "observations": [
                _obs("air_temperature", 16.0, level=2.0),
                _obs("relative_humidity", 60.0, level=2.0),
                _obs("surface_air_pressure", 974.0),
                _obs("accumulated(precipitation_amount)", 1.0),
            ],
        },
        {
            "sourceId": "SN100:0",
            "referenceTime": _ref(11),
            "observations": [
                _obs("air_temperature", 17.0, level=2.0),
                _obs("accumulated(precipitation_amount)", 2.5),
            ],
        },
    ]
}


def _routing_client(
    *,
    latest=None,
    today=None,
    combined_status: int = 200,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        reftime = request.url.params.get("referencetime", "")
        if combined_status != 200:
            return httpx.Response(combined_status, json={})
        if reftime == "latest":
            return httpx.Response(200, json=latest if latest is not None else LATEST_PAYLOAD)
        return httpx.Response(200, json=today if today is not None else TODAY_PAYLOAD)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


# =====================================================================
# Pureza + scoring + precipitación
# =====================================================================

def test_frost_service_does_not_import_streamlit() -> None:
    source = Path("server/services/frost.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


def test_choose_observation_prefers_better_resolution_and_level() -> None:
    observations = [
        {**_obs("air_temperature", 20.0, resolution="PT1H", level=2.0), "_reference_epoch": 100},
        {**_obs("air_temperature", 21.0, resolution="PT1M", level=2.0), "_reference_epoch": 100},
        {**_obs("air_temperature", 22.0, resolution="PT1M", level=10.0), "_reference_epoch": 100},
    ]
    chosen = frost._choose_observation(observations, "temp_c")
    assert chosen["value"] == pytest.approx(21.0)  # PT1M + nivel 2 m


def test_precip_total_counter_mode() -> None:
    # Contador creciente: total = último - primero
    assert frost._precip_total([1.0, 2.5, 4.0], []) == pytest.approx(3.0)


def test_precip_total_counter_with_reset() -> None:
    # Mayoría de diffs negativos (ratio < 0.65) → modo segmentos:
    # diff -4.5 aporta max(0, 0.5)=0.5; diff +1.0 aporta 1.0.
    total = frost._precip_total([5.0, 0.5, 1.5], [])
    assert total == pytest.approx(1.5)


def test_precip_total_steps_fallback() -> None:
    assert frost._precip_total([], [0.2, 0.3, float("nan")]) == pytest.approx(0.5)


def test_station_meta_from_catalog() -> None:
    lat, lon, elevation, name = frost._station_meta(STATION)
    assert lat == pytest.approx(61.1349)
    assert elevation == pytest.approx(ELEVATION)
    assert name == "PLASSEN"


def test_fetch_current_requires_credentials() -> None:
    with pytest.raises(ProviderError) as excinfo:
        _run(frost.fetch_current(STATION, "", ""))
    assert excinfo.value.error_code == "provider_unauthorized"


# =====================================================================
# fetch_current
# =====================================================================

def test_fetch_current_latest_plus_today_precip() -> None:
    client = _routing_client()
    result = _run(
        frost.fetch_current(STATION, "ID", "SECRET", client=client, now=NOW_LOCAL)
    )

    assert result["Tc"] == pytest.approx(18.0)
    assert result["RH"] == pytest.approx(55.0)
    assert result["wind"] == pytest.approx(18.0)   # 5 m/s
    assert result["gust"] == pytest.approx(36.0)
    assert result["p_abs_hpa"] == pytest.approx(975.0)
    assert result["p_hpa"] == pytest.approx(975.0 * math.exp(ELEVATION / 8000.0))

    # Contador acumulado del día: 2.5 - 1.0
    assert result["precip_total"] == pytest.approx(1.5)

    assert result["station_name"] == "PLASSEN"
    assert not math.isnan(result["Td"])


def test_fetch_current_unauthorized_propagates() -> None:
    client = _routing_client(combined_status=401)
    with pytest.raises(ProviderError) as excinfo:
        _run(frost.fetch_current(STATION, "ID", "SECRET", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_unauthorized"


def test_resilient_fanout_on_412() -> None:
    """Si la combinada da 412, se reintenta por elemento y se mergea."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        elements = request.url.params.get("elements", "")
        calls.append(elements)
        if "," in elements:
            return httpx.Response(412, json={})
        if elements == "air_temperature":
            return httpx.Response(200, json={
                "data": [{
                    "sourceId": "SN100:0",
                    "referenceTime": _ref(11),
                    "observations": [_obs("air_temperature", 19.0, level=2.0)],
                }]
            })
        return httpx.Response(412, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    payload = _run(
        frost._request_observations_resilient(
            STATION, "ID", "SECRET", client,
            referencetime="latest", elements=frost.LATEST_ELEMENTS,
            timeout_s=5.0,
        )
    )
    assert len(payload["data"]) == 1
    assert any("," in c for c in calls)          # intentó la combinada
    assert "air_temperature" in calls            # y el fan-out


# =====================================================================
# fetch_today_series
# =====================================================================

def test_fetch_today_series_bins_and_converts() -> None:
    client = _routing_client()
    result = _run(
        frost.fetch_today_series(STATION, "ID", "SECRET", client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is True
    assert len(result["epochs"]) == 2
    assert result["temps"] == [pytest.approx(16.0), pytest.approx(17.0)]
    # MSL derivada de absoluta; segunda fila sin presión → NaN
    assert result["pressures"][0] == pytest.approx(974.0 * math.exp(ELEVATION / 8000.0))
    assert math.isnan(result["pressures"][1])
    assert result["lat"] == pytest.approx(61.1349)


def test_fetch_today_series_empty() -> None:
    client = _routing_client(latest={"data": []}, today={"data": []})
    result = _run(
        frost.fetch_today_series(STATION, "ID", "SECRET", client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is False
