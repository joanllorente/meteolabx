"""
Tests del servicio puro ``server.services.meteofrance``.

DPObs sirve filas con Kelvin/Pa/m/s; los tests cubren conversiones,
el fan-out horario del día y el fallback 6-minutal → horario.
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
from server.services import meteofrance


# Estación real del catálogo: 01014002 "ARBENT", lat 46.278167,
# lon 5.669, elev 534.0.
STATION = "01014002"
ELEVATION = 534.0
TZ = ZoneInfo("Europe/Paris")

NOW_LOCAL = datetime(2026, 6, 10, 2, 30, tzinfo=TZ)  # 3 horas locales (0-2)


def _validity(hour: int, minute: int = 0) -> str:
    dt = NOW_LOCAL.replace(hour=hour, minute=minute).astimezone(ZoneInfo("UTC"))
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _date_param(hour: int) -> str:
    """Formato exacto del parámetro ``date`` que envía el servicio (Z)."""
    dt = NOW_LOCAL.replace(hour=hour, minute=0).astimezone(ZoneInfo("UTC"))
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(hour: int, minute: int = 0, **fields) -> dict:
    return {"validity_time": _validity(hour, minute), **fields}


LATEST_6M = [
    _row(
        2, 24,
        t=295.15,        # 22 °C
        td=287.15,       # 14 °C
        u=60.0,
        pres=95500.0,    # 955 hPa absoluta
        ff=5.0,          # 18 km/h
        fxi10=10.0,      # 36 km/h
        dd=180.0,
    )
]

HOURLY_BY_HOUR = {
    0: _row(0, t=293.15, tx=293.45, tn=292.85, u=70.0, ux=72.0, un=68.0, pres=95400.0, rr1=0.4, fxi3s=7.0, fxy=20.0),
    1: _row(1, t=293.65, tx=294.25, tn=293.15, u=68.0, ux=71.0, un=64.0, pmer=101800.0, rr_per=0.2, ff=4.0, fxi3s=8.0, fxy=30.0),
    2: _row(2, t=294.15, tx=294.55, tn=293.55, u=65.0, ux=69.0, un=61.0, pres=95500.0, rr1=0.0, fxi3s=9.0, fxy=40.0),
}


def _routing_client(
    *,
    latest=None,
    latest_status: int = 200,
    hourly_status: int = 200,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "infrahoraire-6m" in path:
            return httpx.Response(latest_status, json=latest if latest is not None else LATEST_6M)
        if "/station/horaire" in path:
            if hourly_status != 200:
                return httpx.Response(hourly_status, json={})
            date = request.url.params.get("date", "")
            for hour, row in HOURLY_BY_HOUR.items():
                if date == _date_param(hour):
                    return httpx.Response(200, json=[row])
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


# =====================================================================
# Pureza + conversiones
# =====================================================================

def test_meteofrance_service_does_not_import_streamlit() -> None:
    source = Path("server/services/meteofrance.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


def test_parse_obs_row_converts_units_and_derives_pressures() -> None:
    row = meteofrance._parse_obs_row(
        _row(2, t=295.15, td=287.15, u=60.0, pres=95500.0, ff=5.0, dd=180.0),
        elevation_m=ELEVATION,
    )
    assert row["temp_c"] == pytest.approx(22.0)
    assert row["dewpoint_c"] == pytest.approx(14.0)
    assert row["p_abs_hpa"] == pytest.approx(955.0)
    # MSL derivada de la absoluta
    assert row["p_msl_hpa"] == pytest.approx(955.0 * math.exp(ELEVATION / 8000.0))
    assert row["wind_kmh"] == pytest.approx(18.0)


def test_station_meta_from_catalog() -> None:
    lat, lon, elevation, name = meteofrance._station_meta(STATION)
    assert lat == pytest.approx(46.278167)
    assert elevation == pytest.approx(ELEVATION)
    assert name == "ARBENT"


def test_fetch_current_requires_api_key() -> None:
    with pytest.raises(ProviderError) as excinfo:
        _run(meteofrance.fetch_current(STATION, ""))
    assert excinfo.value.error_code == "provider_unauthorized"


# =====================================================================
# fetch_current
# =====================================================================

def test_fetch_current_prefers_6m_with_hourly_precip() -> None:
    client = _routing_client()
    result = _run(
        meteofrance.fetch_current(STATION, "K", client=client, now=NOW_LOCAL)
    )

    # Valores del 6-minutal
    assert result["Tc"] == pytest.approx(22.0)
    assert result["Td"] == pytest.approx(14.0)  # nativo preservado
    assert result["RH"] == pytest.approx(60.0)
    assert result["wind"] == pytest.approx(18.0)
    assert result["gust"] == pytest.approx(36.0)
    assert result["p_abs_hpa"] == pytest.approx(955.0)

    # Precipitación del día: suma horaria (0.4 + 0.2 + 0.0)
    assert result["precip_total"] == pytest.approx(0.6)

    assert result["station_name"] == "ARBENT"
    assert result["elevation"] == pytest.approx(ELEVATION)


def test_fetch_current_falls_back_to_hourly_when_6m_empty() -> None:
    client = _routing_client(latest=[])
    result = _run(
        meteofrance.fetch_current(STATION, "K", client=client, now=NOW_LOCAL)
    )
    # Última fila horaria (hora 2): 294.15 K = 21 °C
    assert result["Tc"] == pytest.approx(21.0)


def test_fetch_current_unauthorized_propagates() -> None:
    client = _routing_client(latest_status=401, hourly_status=401)
    with pytest.raises(ProviderError) as excinfo:
        _run(meteofrance.fetch_current(STATION, "K", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_unauthorized"


def test_fetch_current_no_data_is_bad_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    with pytest.raises(ProviderError) as excinfo:
        _run(meteofrance.fetch_current(STATION, "K", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_bad_response"


# =====================================================================
# fetch_today_series
# =====================================================================

def test_fetch_today_series_hourly_fanout() -> None:
    client = _routing_client()
    result = _run(
        meteofrance.fetch_today_series(STATION, "K", client=client, now=NOW_LOCAL)
    )

    assert result["has_data"] is True
    assert len(result["epochs"]) == 3
    assert result["temps"] == [
        pytest.approx(20.0), pytest.approx(20.5), pytest.approx(21.0),
    ]
    # Hora 1 solo trae pmer → MSL nativa
    assert result["pressures"][1] == pytest.approx(1018.0)
    # Hora 0: MSL derivada de pres
    assert result["pressures"][0] == pytest.approx(954.0 * math.exp(ELEVATION / 8000.0))
    # Hora 1 con viento 4 m/s
    assert result["winds"][1] == pytest.approx(14.4)
    assert result["lat"] == pytest.approx(46.278167)
    assert result["daily_extremes"] == {
        "temp_max": pytest.approx(21.4),
        "temp_min": pytest.approx(19.7),
        "rh_max": pytest.approx(72.0),
        "rh_min": pytest.approx(61.0),
        "gust_max": pytest.approx(32.4),
    }


def test_fetch_today_series_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(
        meteofrance.fetch_today_series(STATION, "K", client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is False


def test_meteofrance_daily_extremes_never_fall_back_to_graph() -> None:
    from server.routers.observations import _build_daily_extremes

    official = _build_daily_extremes(
        {"Tc": 39.3, "gust": float("nan")},
        {
            "temps": [17.7, 39.3],
            "gusts": [],
            "daily_extremes": {"temp_max": 39.5, "temp_min": 17.5},
        },
        provider="METEOFRANCE",
    )
    assert official.temp_max == pytest.approx(39.5)
    assert official.temp_min == pytest.approx(17.5)

    missing = _build_daily_extremes(
        {"Tc": 39.3, "gust": float("nan")},
        {"temps": [17.7, 39.3], "gusts": []},
        provider="METEOFRANCE",
    )
    assert missing.temp_max is None
    assert missing.temp_min is None
    assert missing.gust_max is None


def test_meteofrance_gust_max_prefers_fxi3s_for_the_whole_day() -> None:
    rows = [
        meteofrance._parse_obs_row(_row(0, fxi3s=5.0, fxi10=20.0, fxi=30.0, fxy=40.0), ELEVATION),
        meteofrance._parse_obs_row(_row(1, fxi3s=7.0, fxi10=25.0, fxi=35.0, fxy=45.0), ELEVATION),
    ]
    var = {
        "epochs": [row["epoch"] for row in rows],
        "temps": [], "humidities": [], "gusts": [row["gust_kmh"] for row in rows],
        "daily_extremes": {
            "gust_max": max(row["gust_3s_kmh"] for row in rows),
        },
    }
    from server.routers.observations import _build_daily_extremes
    result = _build_daily_extremes({}, var, provider="METEOFRANCE")

    assert rows[0]["gust_kmh"] == pytest.approx(18.0)
    assert result.gust_max == pytest.approx(25.2)


def test_meteofrance_ranking_includes_current_hour_packet(monkeypatch) -> None:
    """El ranking no debe quedarse una hora por detrás si H ya está publicado."""
    from server.services import ranking

    now = datetime(2026, 6, 26, 16, 30, tzinfo=TZ)

    def date_param(hour: int) -> str:
        return now.replace(hour=hour, minute=0, second=0, microsecond=0).astimezone(
            ZoneInfo("UTC")
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        date = request.url.params.get("date", "")
        requested.append(date)
        if date == date_param(15):
            return httpx.Response(200, json=[{"geo_id_insee": "10261001", "tx": 314.55}])
        if date == date_param(16):
            return httpx.Response(200, json=[{"geo_id_insee": "10261001", "tx": 315.25}])
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        meteofrance,
        "_load_stations",
        lambda: [{"id_station": "10261001", "name": "MUSSY-SUR-SEINE"}],
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    store = ranking.RankingStore()

    records = _run(
        ranking.fetch_meteofrance_records(store, "K", client=client, now=now)
    )
    station = next(r for r in records if r.station_id == "10261001")

    assert date_param(16) in requested
    assert station.tmax == pytest.approx(42.1)


def test_fetch_current_solar_radiation_from_ray_glo01() -> None:
    """ray_glo01 (J/m² del periodo) → W/m² según cadencia (360s en 6-min)."""
    latest_with_solar = [
        _row(2, 24, t=295.15, u=60.0, ray_glo01=180000.0),  # 180 kJ/6min → 500 W/m²
    ]
    client = _routing_client(latest=latest_with_solar)
    result = _run(
        meteofrance.fetch_current(STATION, "K", client=client, now=NOW_LOCAL)
    )
    assert result["solar_radiation"] == pytest.approx(500.0)


def test_fetch_today_series_solar_radiations_hourly() -> None:
    """En la serie horaria el divisor es 3600 s."""
    global HOURLY_BY_HOUR
    original = dict(HOURLY_BY_HOUR)
    HOURLY_BY_HOUR[1] = {**HOURLY_BY_HOUR[1], "ray_glo01": 1800000.0}  # → 500 W/m²
    try:
        client = _routing_client()
        result = _run(
            meteofrance.fetch_today_series(STATION, "K", client=client, now=NOW_LOCAL)
        )
        assert result["solar_radiations"][1] == pytest.approx(500.0)
        assert math.isnan(result["solar_radiations"][0])
    finally:
        HOURLY_BY_HOUR.clear()
        HOURLY_BY_HOUR.update(original)
