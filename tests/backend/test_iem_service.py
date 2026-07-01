"""Tests del servicio puro ``server.services.iem``."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
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


CSV_TEXT = (
    "station,valid,tmpf,dwpf,relh,alti,sknt,gust,drct,p01i\n"
    "LEBL,2026-06-10 08:00,68.0,50.0,52.0,29.92,10.0,20.0,180.0,0.01\n"
    "LEBL,2026-06-10 09:00,70.0,50.0,55.0,29.92,10.0,20.0,180.0,0.02\n"
)


def _client(text: str | None = None, status: int = 200) -> httpx.AsyncClient:
    captured = {"calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        captured["calls"].append({"url": str(request.url), "params": params})
        body = CSV_TEXT if text is None else text
        return httpx.Response(status, text=body)

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


def test_fetch_current_parses_asos_csv_units() -> None:
    client = _client()
    result = _run(iem.fetch_current(STATION, client=client, now=NOW_LOCAL))

    assert client._captured["calls"][-1]["params"]["network"] == "ES__ASOS"
    assert client._captured["calls"][-1]["params"]["station"] == "LEBL"
    assert "asos.py" in client._captured["calls"][-1]["url"]

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


def test_fetch_recent_series_bins_hourly() -> None:
    client = _client()
    result = _run(iem.fetch_recent_series(STATION, days_back=1, client=client, now=NOW_LOCAL))

    assert result["has_data"] is True
    assert len(result["epochs"]) == 2
    assert len(client._captured["calls"]) == 1


def test_fetch_current_empty_is_no_current_data() -> None:
    client = _client(text="station,valid,tmpf\n")
    with pytest.raises(ProviderError) as excinfo:
        _run(iem.fetch_current(STATION, client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_no_current_data"


def test_fetch_current_empty_reports_archived_station_window(monkeypatch) -> None:
    client = _client(text="station,valid,tmpf\n")
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
