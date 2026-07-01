"""
Tests del servicio puro ``server.services.euskalmet``.

Euskalmet no tiene endpoint de observación actual: cada medida se lee
por sensor y hora local. Los mocks rutean por path para responder según
sensor/medida/hora.
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
from server.services import euskalmet


LOCAL_TZ = ZoneInfo("Europe/Madrid")

# Estación real del mapa de sensores (data_station_sensor_map_euskalmet.json):
# B090 "Puerto de Bilbao", lat 43.3774903, lon -3.08474, altitude_m 0.
# Sensores: TA05 (temp), HA05 (rh), SP05 (presión), PA05 (precip),
# VV05 (gust/max_speed), DV05 (wind mean_speed + dirección).
STATION = "B090"

# Día local con 3 horas transcurridas (02:30 → horas 0, 1, 2).
NOW_LOCAL = datetime(2026, 6, 10, 2, 30, tzinfo=LOCAL_TZ)


def _run(coro):
    return asyncio.run(coro)


def _routing_client(responses=None, default_status: int = 404) -> httpx.AsyncClient:
    """
    Mock por substring del path: ``responses`` es lista de
    ``(needle, json_body)``; la primera coincidencia gana.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for needle, body in (responses or []):
            if needle in path:
                return httpx.Response(200, json=body)
        return httpx.Response(default_status, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


# =====================================================================
# Pureza
# =====================================================================

def test_euskalmet_service_does_not_import_streamlit() -> None:
    source = Path("server/services/euskalmet.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


# =====================================================================
# Helpers
# =====================================================================

def test_hour_points_are_ten_minute_slots_in_local_tz() -> None:
    points = euskalmet._hour_points(2026, 6, 10, 2, [10.0, 11.0, 12.0])
    base = int(datetime(2026, 6, 10, 2, 0, tzinfo=LOCAL_TZ).timestamp())
    assert points == [(base, 10.0), (base + 600, 11.0), (base + 1200, 12.0)]


def test_station_meta_from_catalog() -> None:
    lat, lon, elevation, name = euskalmet._station_meta(STATION)
    assert lat == pytest.approx(43.3774903)
    assert lon == pytest.approx(-3.08474)
    assert elevation == pytest.approx(0.0)
    assert name == "Puerto de Bilbao"


def test_sensor_map_resolves_measure() -> None:
    assert euskalmet._sensor_for(STATION, "measuresForAir", "temperature") == "TA05"
    assert euskalmet._sensor_for(STATION, "measuresForSun", "irradiance") == ""


def test_local_day_hours_until_current_hour() -> None:
    hours = euskalmet._local_day_hours(NOW_LOCAL)
    assert hours == [(2026, 6, 10, 0), (2026, 6, 10, 1), (2026, 6, 10, 2)]


# =====================================================================
# Credenciales
# =====================================================================

def test_fetch_current_requires_jwt() -> None:
    with pytest.raises(ProviderError) as excinfo:
        _run(euskalmet.fetch_current(STATION, "", now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_unauthorized"


def test_unmapped_station_is_station_not_found() -> None:
    with pytest.raises(ProviderError) as excinfo:
        _run(euskalmet.fetch_current("ZZZZ", "JWT", now=NOW_LOCAL))
    assert excinfo.value.error_code == "station_not_found"


def test_resolve_jwt_prefers_manual_token() -> None:
    assert euskalmet.resolve_jwt("  manual.jwt.token  ") == "manual.jwt.token"


# =====================================================================
# fetch_current
# =====================================================================

def test_fetch_current_happy_path() -> None:
    # Hora 02 con lecturas; horas anteriores 404 (toleradas).
    responses = [
        ("/TA05/measures/measuresForAir/temperature/at/2026/06/10/02",
         {"values": [15.0, 15.5, 16.0]}),
        ("/HA05/measures/measuresForAir/humidity/at/2026/06/10/02",
         {"values": [80.0, 78.0]}),
        ("/SP05/measures/measuresForAtmosphere/pressure/at/2026/06/10/02",
         {"values": [1012.0]}),
        ("/DV05/measures/measuresForWind/mean_speed/at/2026/06/10/02",
         {"values": [5.0]}),
        ("/VV05/measures/measuresForWind/max_speed/at/2026/06/10/02",
         {"values": [10.0]}),
        ("/DV05/measures/measuresForWind/mean_direction/at/2026/06/10/02",
         {"values": [270.0]}),
        ("/PA05/measures/measuresForWater/precipitation/at/2026/06/10/01",
         {"values": [0.2, 0.1]}),
        ("/PA05/measures/measuresForWater/precipitation/at/2026/06/10/02",
         {"values": [0.3]}),
    ]
    client = _routing_client(responses)
    result = _run(
        euskalmet.fetch_current(STATION, "JWT", client=client, now=NOW_LOCAL)
    )

    # Último valor válido de cada medida
    assert result["Tc"] == pytest.approx(16.0)
    assert result["RH"] == pytest.approx(78.0)
    assert result["wind"] == pytest.approx(18.0)   # 5 m/s → km/h
    assert result["gust"] == pytest.approx(36.0)   # 10 m/s → km/h
    assert result["wind_dir_deg"] == pytest.approx(270.0)

    # B090 está a 0 m: MSL derivada == absoluta
    assert result["p_abs_hpa"] == pytest.approx(1012.0)
    assert result["p_hpa"] == pytest.approx(1012.0)

    # Precipitación: suma de incrementos de TODO el día (horas 0-2)
    assert result["precip_total"] == pytest.approx(0.6)

    # Sin sensor solar mapeado en B090 → NaN
    assert math.isnan(result["solar_radiation"])
    assert math.isnan(result["uv"])

    # Epoch del último slot de la hora 02 (índice 2 → 02:20)
    expected = int(datetime(2026, 6, 10, 2, 20, tzinfo=LOCAL_TZ).timestamp())
    assert result["epoch"] == expected

    assert "station_code" not in result
    assert result["station_name"] == "Puerto de Bilbao"
    assert not math.isnan(result["Td"])  # add_basic_derived aplicado


def test_fetch_current_no_data_at_all_raises_bad_response() -> None:
    client = _routing_client(responses=[], default_status=404)
    with pytest.raises(ProviderError) as excinfo:
        _run(euskalmet.fetch_current(STATION, "JWT", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_bad_response"


def test_fetch_current_unauthorized_propagates() -> None:
    client = _routing_client(responses=[], default_status=401)
    with pytest.raises(ProviderError) as excinfo:
        _run(euskalmet.fetch_current(STATION, "JWT", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_unauthorized"


# =====================================================================
# fetch_today_series
# =====================================================================

def test_fetch_today_series_merges_measures_by_epoch() -> None:
    responses = [
        ("/TA05/measures/measuresForAir/temperature/at/2026/06/10/01",
         {"values": [14.0, 14.2]}),
        ("/TA05/measures/measuresForAir/temperature/at/2026/06/10/02",
         {"values": [15.0]}),
        ("/HA05/measures/measuresForAir/humidity/at/2026/06/10/02",
         {"values": [80.0]}),
        ("/SP05/measures/measuresForAtmosphere/pressure/at/2026/06/10/02",
         {"values": [1012.0]}),
    ]
    client = _routing_client(responses)
    result = _run(
        euskalmet.fetch_today_series(STATION, "JWT", client=client, now=NOW_LOCAL)
    )

    assert result["has_data"] is True
    # Epochs: 01:00, 01:10, 02:00
    assert len(result["epochs"]) == 3
    assert result["temps"] == [
        pytest.approx(14.0), pytest.approx(14.2), pytest.approx(15.0),
    ]
    # Humedad solo en 02:00; huecos NaN alineados
    assert math.isnan(result["humidities"][0])
    assert result["humidities"][2] == pytest.approx(80.0)
    # Presión MSL derivada de absoluta (elevación 0 → igual)
    assert result["pressures"][2] == pytest.approx(1012.0)
    assert result["lat"] == pytest.approx(43.3774903)


def test_fetch_today_series_empty_day() -> None:
    client = _routing_client(responses=[], default_status=404)
    result = _run(
        euskalmet.fetch_today_series(STATION, "JWT", client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is False
    assert result["epochs"] == []
