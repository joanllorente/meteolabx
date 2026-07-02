"""Tests del servicio puro ``server.services.iem``."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import iem


STATION = "ES__ASOS|LEBL"
TZ = ZoneInfo("Europe/Madrid")
NOW_LOCAL = datetime(2026, 6, 10, 12, 0, tzinfo=TZ)


def _row(hour: int, **values) -> dict:
    ts = NOW_LOCAL.replace(hour=hour, minute=0).astimezone(ZoneInfo("UTC"))
    return {
        "utc_valid": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tmpf": 68.0,
        "dwpf": 50.0,
        "relh": 52.0,
        "alti": 29.92,
        "sknt": 10.0,
        "gust": 20.0,
        "drct": 180.0,
        "p01i": 0.01,
        **values,
    }


TODAY_ROWS = [
    _row(8, p01i=0.01),
    _row(9, tmpf=70.0, relh=55.0, p01i=0.02),
]


def _client(text: str | None = None, status: int = 200) -> httpx.AsyncClient:
    captured = {"calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        captured["calls"].append({"url": str(request.url), "params": params})
        if text is not None:
            return httpx.Response(status, text=text)
        # obhistory por día: solo la fecha local "de hoy" trae observaciones.
        rows = TODAY_ROWS if params.get("date") == NOW_LOCAL.date().isoformat() else []
        return httpx.Response(status, json={"data": rows})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    client._captured = captured  # type: ignore[attr-defined]
    return client


def _run(coro):
    return asyncio.run(coro)


def test_iem_requires_network_station_id() -> None:
    client = _client()
    with pytest.raises(ProviderError) as excinfo:
        _run(iem.fetch_current("LEBL", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "station_not_found"


def test_fetch_current_parses_obhistory_units() -> None:
    client = _client()
    result = _run(iem.fetch_current(STATION, client=client, now=NOW_LOCAL))

    obhistory_calls = [
        call for call in client._captured["calls"]
        if "obhistory" in call["url"]
    ]
    assert obhistory_calls[-1]["params"]["network"] == "ES__ASOS"
    assert obhistory_calls[-1]["params"]["station"] == "LEBL"

    assert result["Tc"] == pytest.approx((70.0 - 32.0) * 5.0 / 9.0)
    assert not math.isnan(result["Td"])
    assert result["RH"] == pytest.approx(55.0)
    assert result["p_hpa"] == pytest.approx(1013.25, abs=0.05)
    assert result["wind"] == pytest.approx(18.52)
    assert result["gust"] == pytest.approx(37.04)
    assert result["wind_dir_deg"] == pytest.approx(180.0)
    assert result["precip_rate"] == pytest.approx(0.508)
    assert result["precip_total"] == pytest.approx(0.762)
    assert result["station_name"] == "Barcelona"
    assert not math.isnan(result["feels_like"])


def test_fetch_today_series_is_canonical() -> None:
    client = _client()
    result = _run(iem.fetch_today_series(STATION, client=client, now=NOW_LOCAL))

    assert result["has_data"] is True
    assert len(result["epochs"]) == 2
    assert result["temps"][0] == pytest.approx(20.0)
    assert result["pressures"][0] == pytest.approx(1013.25, abs=0.05)
    assert result["winds"][0] == pytest.approx(18.52)
    assert result["precips"][-1] == pytest.approx(0.762)


def test_fetch_today_series_falls_back_to_previous_local_day_when_today_empty() -> None:
    today = NOW_LOCAL.date().isoformat()
    yesterday = (NOW_LOCAL - timedelta(days=1)).date().isoformat()
    yesterday_row = dict(_row(9, tmpf=90.0))
    yesterday_row["utc_valid"] = yesterday_row["utc_valid"].replace(today, yesterday)
    client = _client_with_days({
        today: [],
        yesterday: [yesterday_row],
    })

    result = _run(iem.fetch_today_series(STATION, client=client, now=NOW_LOCAL))

    assert result["has_data"] is True
    assert len(result["epochs"]) == 1
    assert result["temps"][0] == pytest.approx((90.0 - 32.0) * 5.0 / 9.0)


def test_fetch_recent_series_bins_hourly() -> None:
    client = _client()
    result = _run(iem.fetch_recent_series(STATION, days_back=1, client=client, now=NOW_LOCAL))

    assert result["has_data"] is True
    assert len(result["epochs"]) == 2
    # Un request obhistory por día: ayer + hoy.
    assert len(client._captured["calls"]) == 2


def _client_with_days(rows_by_date: dict[str, list[dict]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        rows = rows_by_date.get(request.url.params.get("date", ""), [])
        return httpx.Response(200, json={"data": rows})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def test_fetch_current_precip_total_excludes_yesterday() -> None:
    yesterday = (NOW_LOCAL - timedelta(days=1)).date().isoformat()
    yesterday_row = dict(_row(10, p01i=1.0))
    yesterday_row["utc_valid"] = yesterday_row["utc_valid"].replace(
        NOW_LOCAL.date().isoformat(), yesterday
    )
    client = _client_with_days({
        yesterday: [yesterday_row],
        NOW_LOCAL.date().isoformat(): [_row(8, p01i=0.01), _row(9, p01i=0.02)],
    })
    result = _run(iem.fetch_current(STATION, client=client, now=NOW_LOCAL))
    # 0.01" (hora 08) + 0.02" (hora 09) = 0.762 mm; la 1.0" de ayer no cuenta.
    assert result["precip_total"] == pytest.approx(0.762)


def test_fetch_current_manual_coop_uses_summary_extremes_and_precip(monkeypatch) -> None:
    station_id = "MN_COOP|GMDM5"
    now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    obhistory_row = {
        "utc_valid": "2026-07-02T10:30:00Z",
        "tmpf": 72.0,
        "dwpf": None,
        "relh": None,
        "alti": None,
        "sknt": None,
        "gust": None,
        "drct": None,
        "p01i": None,
    }
    current_row = {
        "station": "GMDM5",
        "tmpf": 72.0,
        "max_tmpf": 81.0,
        "min_tmpf": 66.0,
        "pday": 7.3,
    }

    monkeypatch.setattr(
        iem,
        "_station_meta",
        lambda station_id: {
            "name": "Grand Meadow",
            "tz": "America/Chicago",
            "lat": 43.7047,
            "lon": -92.5645,
            "elevation": 406.0,
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "currents.json" in str(request.url):
            return httpx.Response(200, json={"data": [current_row]})
        rows = [obhistory_row] if request.url.params.get("date") == now.date().isoformat() else []
        return httpx.Response(200, json={"data": rows})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(iem.fetch_current(station_id, client=client, now=now))

    assert result["Tc"] == pytest.approx((72.0 - 32.0) * 5.0 / 9.0)
    assert result["daily_extremes"]["temp_max"] == pytest.approx((81.0 - 32.0) * 5.0 / 9.0)
    assert result["daily_extremes"]["temp_min"] == pytest.approx((66.0 - 32.0) * 5.0 / 9.0)
    assert result["precip_total"] == pytest.approx(7.3 * 25.4)


def test_fetch_current_asos_uses_summary_extremes(monkeypatch) -> None:
    station_id = "KW__ASOS|OKKK"
    now = datetime(2026, 7, 2, 23, 0, tzinfo=ZoneInfo("Asia/Kuwait"))
    obhistory_row = {
        "utc_valid": "2026-07-02T20:00:00Z",
        "tmpf": 105.8,
        "dwpf": 37.4,
        "relh": 9.767469,
        "alti": 29.441313,
        "sknt": 12.0,
        "gust": None,
        "drct": 350.0,
        "p01i": 0.0,
    }
    current_row = {
        "station": "OKKK",
        "tmpf": 105.8,
        "max_tmpf": 116.6,
        "min_tmpf": 86.0,
        "pday": None,
    }

    monkeypatch.setattr(
        iem,
        "_station_meta",
        lambda station_id: {
            "name": "Kuwait",
            "tz": "Asia/Kuwait",
            "lat": 29.2167,
            "lon": 47.9833,
            "elevation": None,
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "currents.json" in str(request.url):
            return httpx.Response(200, json={"data": [current_row]})
        rows = [obhistory_row] if request.url.params.get("date") == now.date().isoformat() else []
        return httpx.Response(200, json={"data": rows})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(iem.fetch_current(station_id, client=client, now=now))

    assert result["Tc"] == pytest.approx((105.8 - 32.0) * 5.0 / 9.0)
    assert result["daily_extremes"]["temp_max"] == pytest.approx((116.6 - 32.0) * 5.0 / 9.0)
    assert result["daily_extremes"]["temp_min"] == pytest.approx((86.0 - 32.0) * 5.0 / 9.0)


def test_fetch_current_iem_drops_temperature_extremes_without_temperature_evidence(monkeypatch) -> None:
    station_id = "WMO_BUFR_SRF|0-208-0-55"
    now = datetime(2026, 7, 3, 5, 30, tzinfo=ZoneInfo("Asia/Pyongyang"))
    obhistory_row = {
        "utc_valid": "2026-07-02T20:00:00Z",
        "tmpf": None,
        "dwpf": None,
        "relh": None,
        "alti": None,
        "mslp": None,
        "sknt": 38.099354,
        "gust": None,
        "drct": 294.0,
        "p01i": None,
    }
    current_row = {
        "station": "0-208-0-55",
        "tmpf": None,
        "max_tmpf": 70.0,
        "min_tmpf": 70.0,
        "max_gust": 50.0,
    }

    monkeypatch.setattr(
        iem,
        "_station_meta",
        lambda station_id: {
            "name": "WONSAN",
            "tz": "Asia/Pyongyang",
            "lat": 39.18,
            "lon": 127.43,
            "elevation": 36.0,
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "currents.json" in str(request.url):
            return httpx.Response(200, json={"data": [current_row]})
        rows = [obhistory_row] if request.url.params.get("date") == now.date().isoformat() else []
        return httpx.Response(200, json={"data": rows})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(iem.fetch_current(station_id, client=client, now=now))

    assert "temp_max" not in result["daily_extremes"]
    assert "temp_min" not in result["daily_extremes"]
    assert result["daily_extremes"]["gust_max"] == pytest.approx(50.0 * 1.852)


def test_iem_daily_extremes_prefer_current_summary_over_single_series_point() -> None:
    from server.routers.observations import _build_daily_extremes

    result = _build_daily_extremes(
        {
            "Tc": 22.2,
            "precip_total": 185.42,
            "daily_extremes": {"temp_max": 27.2, "temp_min": 18.9},
        },
        {"temps": [22.2], "humidities": [], "gusts": []},
        provider="IEM",
    )

    assert result.temp_max == pytest.approx(27.2)
    assert result.temp_min == pytest.approx(18.9)
    assert result.precip_total == pytest.approx(185.42)


def test_precip_repeated_within_hour_not_double_counted() -> None:
    # p01i es acumulado intra-horario: los especiales repiten/incrementan el
    # mismo valor. 0.01"→0.03" en la hora 08 y 0.02" en la hora 09 = 0.05".
    rows = [
        _row(8, p01i=0.01),
        {**_row(8, p01i=0.03), "utc_valid": _row(8)["utc_valid"].replace("T06:00", "T06:30")},
        _row(9, p01i=0.02),
    ]
    client = _client_with_days({NOW_LOCAL.date().isoformat(): rows})
    result = _run(iem.fetch_today_series(STATION, client=client, now=NOW_LOCAL))
    assert result["precips"][-1] == pytest.approx(0.05 * 25.4)


def test_trace_precip_from_iem_p0000_is_zero(monkeypatch) -> None:
    station_id = "GU_ASOS|PWAK"
    now = datetime(2026, 7, 3, 8, 55, tzinfo=ZoneInfo("Pacific/Wake"))
    rows = [
        {
            "utc_valid": "2026-07-02T18:55:00Z",
            "tmpf": 82.8,
            "dwpf": 77.0,
            "relh": 82.74214,
            "alti": 29.89,
            "sknt": 10.0,
            "gust": None,
            "drct": 60.0,
            "p01i": 0.0001,
            "raw": "PWAK 021855Z AUTO 06010KT 10SM RMK AO2 P0000",
        },
        {
            "utc_valid": "2026-07-02T20:55:00Z",
            "tmpf": 87.8,
            "dwpf": 76.3,
            "relh": 68.9184,
            "alti": 29.91,
            "sknt": 11.0,
            "gust": None,
            "drct": 60.0,
            "p01i": 0.0,
            "raw": "PWAK 022055Z AUTO 06011KT 8SM CLR RMK AO2 60000",
        },
    ]
    current_row = {
        "station": "PWAK",
        "tmpf": 87.8,
        "max_tmpf": 87.8,
        "min_tmpf": 82.4,
        "pday": 0.0001,
        "ob_pday": 0.0001,
    }

    monkeypatch.setattr(
        iem,
        "_station_meta",
        lambda station_id: {
            "name": "Wake island",
            "tz": "Pacific/Wake",
            "lat": 19.28,
            "lon": 166.6419,
            "elevation": 7.0,
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "currents.json" in str(request.url):
            return httpx.Response(200, json={"data": [current_row]})
        return httpx.Response(200, json={"data": rows})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    result = _run(iem.fetch_current(station_id, client=client, now=now))

    assert result["precip_rate"] == pytest.approx(0.0)
    assert result["precip_total"] == pytest.approx(0.0)

    series_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    series = _run(iem.fetch_today_series(station_id, client=series_client, now=now))
    assert series["precips"][-1] == pytest.approx(0.0)


def test_fetch_current_empty_is_no_current_data() -> None:
    client = _client(text='{"data": []}')
    with pytest.raises(ProviderError) as excinfo:
        _run(iem.fetch_current(STATION, client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_no_current_data"


def test_fetch_current_empty_reports_archived_station_window(monkeypatch) -> None:
    client = _client(text='{"data": []}')
    monkeypatch.setattr(
        iem.stations,
        "raw_metadata",
        lambda provider, station_id: {
            "archive_begin": "1936-03-10",
            "archive_end": "2020-11-09",
        },
    )

    with pytest.raises(ProviderError) as excinfo:
        _run(iem.fetch_current("DE__ASOS|EDDT", client=client, now=NOW_LOCAL))

    assert excinfo.value.error_code == "provider_no_current_data"
    assert "1936-03-10" in excinfo.value.detail
    assert "2020-11-09" in excinfo.value.detail


def test_fetch_current_archived_station_does_not_call_iem(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(
        iem,
        "_station_meta",
        lambda station_id: {"is_historical_only": True, "tz": "Europe/Berlin"},
    )
    monkeypatch.setattr(
        iem.stations,
        "raw_metadata",
        lambda provider, station_id: {
            "archive_begin": "1936-03-10",
            "archive_end": "2020-11-09",
        },
    )

    with pytest.raises(ProviderError) as excinfo:
        _run(iem.fetch_current("DE__ASOS|EDDT", client=client, now=NOW_LOCAL))

    assert excinfo.value.error_code == "provider_no_current_data"
    assert client._captured["calls"] == []
