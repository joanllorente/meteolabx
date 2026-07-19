"""Tests del servicio Climantartide (AWS antárticas italianas)."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import climantartide


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso + "+00:00").timestamp()) * 1000


def _jsonp_payload() -> str:
    # Concordia es UTC+8: las 18Z del 18 caen ya en el 19 local.
    return (
        '({"par":{"Titleg":"Temperature"},"data":['
        '{"name":"Concordia","data":[[%d,-78.9],[%d,-82.9],[%d,-84.0],[%d,-79.1]]},'
        '{"name":"Rita","data":[[%d,-26.3]]}'
        ']});'
    ) % (
        _ms("2026-07-18T06:00:00"), _ms("2026-07-18T12:00:00"),
        _ms("2026-07-18T18:00:00"), _ms("2026-07-19T06:00:00"),
        _ms("2026-07-19T06:00:00"),
    )


def _client(iem_row: dict | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "climantartide" in request.url.host:
            return httpx.Response(200, text=_jsonp_payload())
        if "mesonet" in request.url.host:
            return httpx.Response(200, json={"data": [iem_row] if iem_row else []})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


def test_fetch_current_derives_extremes_from_hourly_series() -> None:
    client = _client()
    result = _run(climantartide.fetch_current("Concordia", client=client, now=NOW))

    assert result["Tc"] == pytest.approx(-79.1)
    # Día local (UTC+8) del 19: 18Z del 18 (−84.0) + 06Z del 19 (−79.1).
    assert result["daily_extremes"]["temp_min"] == pytest.approx(-84.0)
    assert result["daily_extremes"]["temp_max"] == pytest.approx(-79.1)
    assert result["station_name"] == "Concordia (Dome C)"
    assert result["time_local"].endswith("+08:00")
    # Solo temperatura: el resto de variables van a NaN.
    assert math.isnan(result["RH"])
    assert math.isnan(result["wind"])


def test_iem_extremes_merge_only_refines_never_clips() -> None:
    def _c_to_f(c: float) -> float:
        return c * 9.0 / 5.0 + 32.0

    # IEM clampa el frío (~−73): su mínima NO debe sustituir a la real más
    # baja; su máxima intrahoraria más alta SÍ afina.
    client = _client(iem_row={
        "station": "0-380-0-625",
        "max_tmpf": _c_to_f(-77.0),
        "min_tmpf": _c_to_f(-73.0),
    })
    result = _run(climantartide.fetch_current("Concordia", client=client, now=NOW))
    assert result["daily_extremes"]["temp_min"] == pytest.approx(-84.0)
    assert result["daily_extremes"]["temp_max"] == pytest.approx(-77.0)


def test_fetch_today_series_is_canonical_and_local_day_filtered() -> None:
    client = _client()
    series = _run(climantartide.fetch_today_series("Concordia", client=client, now=NOW))

    assert series["has_data"] is True
    assert len(series["epochs"]) == 2
    assert series["temps"] == [pytest.approx(-84.0), pytest.approx(-79.1)]
    assert all(math.isnan(v) for v in series["humidities"])
    assert series["lat"] == pytest.approx(-75.1)


def test_unknown_station_raises_not_found() -> None:
    with pytest.raises(ProviderError) as excinfo:
        _run(climantartide.fetch_current("Inventada", client=_client(), now=NOW))
    assert excinfo.value.error_code == "station_not_found"


def test_station_without_points_raises_no_current_data() -> None:
    # Eneide está en el catálogo pero no viene en el feed simulado.
    with pytest.raises(ProviderError) as excinfo:
        _run(climantartide.fetch_current("Eneide", client=_client(), now=NOW))
    assert excinfo.value.error_code == "provider_no_current_data"
