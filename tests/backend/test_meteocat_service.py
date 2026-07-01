"""
Tests del servicio puro ``server.services.meteocat``.

Estructura paralela a ``test_aemet_service.py``: unit tests de helpers
+ tests del fetcher con ``httpx.MockTransport`` ruteando por URL.

Particularidades de Meteocat cubiertas:
- ``fetch_current`` deriva del endpoint de día (no fan-out /ultimes).
- Fallback a ``/ultimes`` cuando el día local viene sin lecturas.
- 404 en TODAS las fechas UTC → ``station_not_found``.
- Conversiones m/s → km/h y presión absoluta → MSL.
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
from server.services import meteocat


CAT_TZ = ZoneInfo("Europe/Madrid")

# Estación real del catálogo local (data/data_estaciones_meteocat.json):
# C6 "Castellnou de Seana", lat 41.6566, lon 0.95172, altitud 264 m.
STATION = "C6"
ELEVATION = 264.0

# Mediodía local del 2026-06-10 (CEST, UTC+2). El día local cubre
# [2026-06-09T22:00Z, 2026-06-10T22:00Z) → 2 fechas UTC: 06-09 y 06-10.
NOW_LOCAL = datetime(2026, 6, 10, 12, 0, tzinfo=CAT_TZ)

# Lecturas del día (naive → interpretadas en CAT_TZ, como el legacy).
DAY_PAYLOAD = [
    {
        "codi": STATION,
        "variables": [
            {"codi": 32, "lectures": [
                {"data": "2026-06-10T10:00", "valor": 22.0},
                {"data": "2026-06-10T10:30", "valor": 23.5},
            ]},
            {"codi": 33, "lectures": [
                {"data": "2026-06-10T10:00", "valor": 60.0},
                {"data": "2026-06-10T10:30", "valor": 55.0},
            ]},
            {"codi": 40, "lectures": [
                {"data": "2026-06-10T10:00", "valor": 22.8},
                {"data": "2026-06-10T10:30", "valor": 24.1},
            ]},
            {"codi": 42, "lectures": [
                {"data": "2026-06-10T10:00", "valor": 21.4},
                {"data": "2026-06-10T10:30", "valor": 22.9},
            ]},
            {"codi": 3, "lectures": [
                {"data": "2026-06-10T10:00", "valor": 64.0},
                {"data": "2026-06-10T10:30", "valor": 58.0},
            ]},
            {"codi": 44, "lectures": [
                {"data": "2026-06-10T10:00", "valor": 57.0},
                {"data": "2026-06-10T10:30", "valor": 52.0},
            ]},
            {"codi": 34, "lectures": [
                {"data": "2026-06-10T10:30", "valor": 985.0},
            ]},
            {"codi": 30, "lectures": [
                {"data": "2026-06-10T10:30", "valor": 5.0},   # m/s → 18 km/h
            ]},
            {"codi": 50, "lectures": [
                {"data": "2026-06-10T10:00", "valor": 11.2},
                {"data": "2026-06-10T10:30", "valor": 10.0},  # m/s → 36 km/h
            ]},
            {"codi": 31, "lectures": [
                {"data": "2026-06-10T10:30", "valor": 180.0},
            ]},
            {"codi": 36, "lectures": [
                {"data": "2026-06-10T10:30", "valor": 800.0},
            ]},
            {"codi": 39, "lectures": [
                {"data": "2026-06-10T10:30", "valor": 6.0},
            ]},
            {"codi": 35, "lectures": [
                {"data": "2026-06-10T09:00", "valor": 0.2},
                {"data": "2026-06-10T10:00", "valor": 0.1},
            ]},
        ],
    }
]


def _day_routing_client(
    *,
    today_payload=None,
    yesterday_payload=None,
    today_status: int = 200,
    yesterday_status: int = 200,
    ultimes_handler=None,
) -> httpx.AsyncClient:
    """Mock que rutea por URL: día de hoy/ayer UTC y /ultimes."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/estacions/mesurades/" in path:
            if path.endswith("/2026/06/10"):
                return httpx.Response(today_status, json=today_payload or [])
            if path.endswith("/2026/06/09"):
                return httpx.Response(yesterday_status, json=yesterday_payload or [])
            return httpx.Response(404, json={"message": "no data"})
        if "/variables/mesurades/" in path and path.endswith("/ultimes"):
            if ultimes_handler is not None:
                return ultimes_handler(request)
            return httpx.Response(404, json={"message": "no data"})
        return httpx.Response(500, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


# =====================================================================
# Pureza
# =====================================================================

def test_meteocat_service_does_not_import_streamlit() -> None:
    """Garantía estática: server/services/meteocat.py es puro."""
    source = Path("server/services/meteocat.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


# =====================================================================
# Helpers
# =====================================================================

def test_absolute_to_msl_uses_barometric_factor() -> None:
    result = meteocat._absolute_to_msl(985.0, 264.0)
    assert result == pytest.approx(985.0 * math.exp(264.0 / 8000.0))


def test_absolute_to_msl_nan_inputs() -> None:
    assert math.isnan(meteocat._absolute_to_msl(float("nan"), 264.0))
    assert math.isnan(meteocat._absolute_to_msl(985.0, float("nan")))


def test_parse_measurement_epoch_naive_is_cat_tz() -> None:
    epoch = meteocat._parse_measurement_epoch("2026-06-10T10:30")
    expected = int(datetime(2026, 6, 10, 10, 30, tzinfo=CAT_TZ).timestamp())
    assert epoch == expected


def test_parse_measurement_epoch_zulu() -> None:
    epoch = meteocat._parse_measurement_epoch("2026-06-10T10:30Z")
    expected = int(datetime.fromisoformat("2026-06-10T10:30+00:00").timestamp())
    assert epoch == expected


def test_station_meta_resolves_from_local_catalog() -> None:
    lat, lon, elevation, name = meteocat._station_meta(STATION)
    assert lat == pytest.approx(41.6566)
    assert lon == pytest.approx(0.95172)
    assert elevation == pytest.approx(ELEVATION)
    assert name == "Castellnou de Seana"


def test_station_meta_unknown_station_is_nan() -> None:
    lat, lon, elevation, name = meteocat._station_meta("ZZZZ")
    assert math.isnan(lat) and math.isnan(lon) and math.isnan(elevation)
    assert name == ""


# =====================================================================
# fetch_current
# =====================================================================

def test_fetch_current_requires_api_key() -> None:
    with pytest.raises(ProviderError) as excinfo:
        _run(meteocat.fetch_current(STATION, ""))
    assert excinfo.value.error_code == "provider_unauthorized"
    assert excinfo.value.status_code == 401


def test_fetch_current_happy_path_from_day_endpoint() -> None:
    client = _day_routing_client(
        today_payload=DAY_PAYLOAD,
        yesterday_status=404,  # ayer UTC sin datos: tolerado
    )
    result = _run(
        meteocat.fetch_current(STATION, "K", client=client, now=NOW_LOCAL)
    )

    # Última lectura de cada variable
    assert result["Tc"] == pytest.approx(23.5)
    assert result["RH"] == pytest.approx(55.0)
    assert result["wind"] == pytest.approx(18.0)   # 5 m/s → km/h
    assert result["gust"] == pytest.approx(36.0)   # 10 m/s → km/h
    assert result["wind_dir_deg"] == pytest.approx(180.0)
    assert result["solar_radiation"] == pytest.approx(800.0)
    assert result["uv"] == pytest.approx(6.0)

    # Presión: absoluta nativa + MSL derivada con la altitud del catálogo
    assert result["p_abs_hpa"] == pytest.approx(985.0)
    assert result["p_hpa"] == pytest.approx(985.0 * math.exp(ELEVATION / 8000.0))

    # Precipitación del día = suma de PPT (codi 35)
    assert result["precip_total"] == pytest.approx(0.3)

    # Epoch = lectura más reciente
    assert result["epoch"] == int(datetime(2026, 6, 10, 10, 30, tzinfo=CAT_TZ).timestamp())

    # Metadatos del catálogo
    assert result["lat"] == pytest.approx(41.6566)
    assert result["elevation"] == pytest.approx(ELEVATION)
    assert "station_code" not in result
    assert result["station_name"] == "Castellnou de Seana"

    # Derivadas básicas calculadas (add_basic_derived)
    assert not math.isnan(result["Td"])
    assert not math.isnan(result["feels_like"])


def test_fetch_current_and_series_use_lower_height_wind_codes() -> None:
    lower_height_payload = [
        {
            "codi": STATION,
            "variables": [
                {"codi": 32, "lectures": [
                    {"data": "2026-06-10T10:30", "valor": 22.0},
                ]},
                {"codi": 46, "lectures": [
                    {"data": "2026-06-10T10:30", "valor": 2.8},
                ]},
                {"codi": 47, "lectures": [
                    {"data": "2026-06-10T10:30", "valor": 252.0},
                ]},
                {"codi": 56, "lectures": [
                    {"data": "2026-06-10T10:30", "valor": 6.4},
                ]},
            ],
        }
    ]
    client = _day_routing_client(today_payload=lower_height_payload, yesterday_status=404)

    current = _run(meteocat.fetch_current(STATION, "K", client=client, now=NOW_LOCAL))
    assert current["wind"] == pytest.approx(10.08)
    assert current["gust"] == pytest.approx(23.04)
    assert current["wind_dir_deg"] == 252.0

    client = _day_routing_client(today_payload=lower_height_payload, yesterday_status=404)
    series = _run(meteocat.fetch_today_series(STATION, "K", client=client, now=NOW_LOCAL))
    assert series["winds"][-1] == pytest.approx(10.08)
    assert series["gusts"][-1] == pytest.approx(23.04)
    assert series["wind_dirs"][-1] == 252.0


def test_wind_height_priority_never_mixes_10m_6m_and_2m() -> None:
    epoch_10m = int(datetime(2026, 6, 10, 10, 0, tzinfo=CAT_TZ).timestamp())
    epoch_6m = int(datetime(2026, 6, 10, 10, 30, tzinfo=CAT_TZ).timestamp())
    var_map = {
        meteocat.V_GUST: [(epoch_10m, 5.0)],
        meteocat.V_GUST_6M: [(epoch_6m, 20.0)],
        meteocat.V_GUST_2M: [(epoch_6m, 30.0)],
    }

    series = meteocat._normalize_today_series(STATION, var_map)

    # Existe racha a 10 m: se ignoran por completo 6 m y 2 m aunque sean mayores.
    assert series["epochs"] == [epoch_10m]
    assert series["gusts"] == [pytest.approx(18.0)]
    assert series["daily_extremes"]["gust_max"] == pytest.approx(18.0)


def test_fetch_current_falls_back_to_ultimes_when_day_empty() -> None:
    def ultimes(request: httpx.Request) -> httpx.Response:
        # Solo la variable 32 (temp) responde; el resto 404.
        if "/variables/mesurades/32/" in request.url.path:
            return httpx.Response(200, json={
                "lectures": [{"data": "2026-06-10T10:30", "valor": 21.0}],
            })
        return httpx.Response(404, json={})

    client = _day_routing_client(
        today_payload=[{"codi": STATION, "variables": []}],
        yesterday_payload=[{"codi": STATION, "variables": []}],
        ultimes_handler=ultimes,
    )
    result = _run(
        meteocat.fetch_current(STATION, "K", client=client, now=NOW_LOCAL)
    )
    assert result["Tc"] == pytest.approx(21.0)
    assert math.isnan(result["precip_total"])  # sin day-window no hay acumulado


def test_fetch_current_station_not_found_when_all_days_404() -> None:
    client = _day_routing_client(today_status=404, yesterday_status=404)
    with pytest.raises(ProviderError) as excinfo:
        _run(meteocat.fetch_current(STATION, "K", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "station_not_found"


def test_fetch_current_uses_previous_utc_day_when_today_returns_400() -> None:
    client = _day_routing_client(today_status=400, yesterday_payload=DAY_PAYLOAD)

    result = _run(meteocat.fetch_current(STATION, "K", client=client, now=NOW_LOCAL))

    assert result["Tc"] == pytest.approx(23.5)
    assert result["RH"] == pytest.approx(55.0)


def test_fetch_current_unauthorized_propagates() -> None:
    client = _day_routing_client(today_status=403, yesterday_status=403)
    with pytest.raises(ProviderError) as excinfo:
        _run(meteocat.fetch_current(STATION, "K", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_unauthorized"
    assert excinfo.value.status_code == 401


def test_fetch_current_ratelimit_propagates() -> None:
    client = _day_routing_client(today_status=429, yesterday_status=429)
    with pytest.raises(ProviderError) as excinfo:
        _run(meteocat.fetch_current(STATION, "K", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_ratelimit"


def test_fetch_current_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    with pytest.raises(ProviderError) as excinfo:
        _run(meteocat.fetch_current(STATION, "K", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_network_error"


# =====================================================================
# fetch_today_series
# =====================================================================

def test_fetch_today_series_normalizes_units() -> None:
    client = _day_routing_client(today_payload=DAY_PAYLOAD, yesterday_status=404)
    result = _run(
        meteocat.fetch_today_series(STATION, "K", client=client, now=NOW_LOCAL)
    )

    assert result["has_data"] is True
    # 2 epochs: 10:00 y 10:30. El de las 09:00 solo trae precip (codi
    # 35), que no forma parte de la serie canónica, así que no aparece.
    assert len(result["epochs"]) == 2
    assert result["epochs"] == sorted(result["epochs"])

    # Último punto (10:30): todo presente
    assert result["temps"][-1] == pytest.approx(23.5)
    assert result["winds"][-1] == pytest.approx(18.0)     # km/h
    assert result["gusts"][-1] == pytest.approx(36.0)     # km/h
    assert result["pressures"][-1] == pytest.approx(985.0 * math.exp(ELEVATION / 8000.0))
    assert result["pressures_abs"][-1] == pytest.approx(985.0)
    assert result["daily_extremes"] == {
        "temp_max": pytest.approx(24.1),
        "temp_min": pytest.approx(21.4),
        "rh_max": pytest.approx(64.0),
        "rh_min": pytest.approx(52.0),
        "gust_max": pytest.approx(40.32),
    }

    # Primer punto (10:00): sin lectura de presión → NaN en esa posición
    assert result["temps"][0] == pytest.approx(22.0)
    assert math.isnan(result["pressures"][0])
    assert math.isnan(result["pressures_abs"][0])

    # Dewpoint no se mide: NaN alineado
    assert all(math.isnan(v) for v in result["dewpts"])

    assert result["lat"] == pytest.approx(41.6566)


def test_fetch_today_series_empty_day() -> None:
    client = _day_routing_client(
        today_payload=[{"codi": STATION, "variables": []}],
        yesterday_payload=[{"codi": STATION, "variables": []}],
    )
    result = _run(
        meteocat.fetch_today_series(STATION, "K", client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is False
    assert result["epochs"] == []


def test_fetch_today_series_uses_previous_utc_day_when_today_returns_400() -> None:
    client = _day_routing_client(today_status=400, yesterday_payload=DAY_PAYLOAD)

    result = _run(
        meteocat.fetch_today_series(STATION, "K", client=client, now=NOW_LOCAL)
    )

    assert result["has_data"] is True
    assert result["temps"] == [pytest.approx(22.0), pytest.approx(23.5)]


def test_meteocat_daily_extremes_never_fall_back_to_chart_series() -> None:
    from server.routers.observations import _build_daily_extremes

    with_official = _build_daily_extremes(
        {"Tc": 23.5, "RH": 55.0},
        {
            "temps": [10.0, 30.0],
            "humidities": [20.0, 90.0],
            "gusts": [],
            "daily_extremes": {"temp_max": 31.2, "temp_min": 9.4},
        },
        provider="METEOCAT",
    )
    assert with_official.temp_max == pytest.approx(31.2)
    assert with_official.temp_min == pytest.approx(9.4)

    without_official = _build_daily_extremes(
        {"Tc": 23.5},
        {"temps": [10.0, 30.0], "gusts": []},
        provider="METEOCAT",
    )
    assert without_official.temp_max is None
    assert without_official.temp_min is None
    assert without_official.gust_max is None


def test_fetch_today_series_filters_outside_local_day() -> None:
    """Lecturas de ayer (antes de 00:00 local) quedan fuera de la serie."""
    payload = [
        {
            "codi": STATION,
            "variables": [
                {"codi": 32, "lectures": [
                    {"data": "2026-06-09T23:50", "valor": 18.0},  # ayer local
                    {"data": "2026-06-10T00:10", "valor": 17.5},  # hoy local
                ]},
            ],
        }
    ]
    client = _day_routing_client(
        today_payload=payload, yesterday_payload=payload,
    )
    result = _run(
        meteocat.fetch_today_series(STATION, "K", client=client, now=NOW_LOCAL)
    )
    assert len(result["epochs"]) == 1
    assert result["temps"] == [pytest.approx(17.5)]
