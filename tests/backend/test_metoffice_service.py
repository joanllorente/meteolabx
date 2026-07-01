"""
Tests del servicio puro ``server.services.metoffice``.

Met Office (DataHub observation-land) sirve ~24 h de observaciones de
una celda geohash en una sola petición; current y serie salen del
mismo payload.
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
from server.services import metoffice


# Estación real del catálogo: gby1kb "Guernsey: Airport",
# lat 49.44122..., lon -2.5982..., elev 92, tz Europe/Guernsey.
STATION = "gby1kb"
ELEVATION = 92.0
TZ = ZoneInfo("Europe/Guernsey")

NOW_LOCAL = datetime(2026, 6, 10, 12, 0, tzinfo=TZ)


def _dt(hour: int, day_offset: int = 0) -> str:
    base = NOW_LOCAL.replace(hour=hour, minute=0)
    if day_offset:
        from datetime import timedelta
        base = base + timedelta(days=day_offset)
    return base.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S+00:00")


OBSERVATIONS = {
    "data": [
        {  # ayer: debe quedar fuera de la serie del día
            "datetime": _dt(23, day_offset=-1),
            "temperature": 14.0, "humidity": 80.0, "mslp": 1010.0,
        },
        {
            "datetime": _dt(10),
            "temperature": 16.0, "humidity": 75.0, "mslp": 1011.0,
            "wind_speed": 5.0, "wind_gust": 10.0, "wind_direction": "SSW",
        },
        {
            "datetime": _dt(11),
            "temperature": 17.0, "humidity": 70.0, "mslp": 1012.0,
            "wind_speed": 6.0, "wind_direction": 200,
        },
    ]
}


def _client(status: int = 200, payload=None) -> httpx.AsyncClient:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["apikey"] = request.headers.get("apikey", "")
        return httpx.Response(status, json=payload if payload is not None else OBSERVATIONS)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    client._captured = captured  # type: ignore[attr-defined]
    return client


def _run(coro):
    return asyncio.run(coro)


def test_metoffice_service_does_not_import_streamlit() -> None:
    source = Path("server/services/metoffice.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


def test_station_meta_resolves_lowercase_and_tz() -> None:
    lat, lon, elevation, name, tz = metoffice._station_meta("GBY1KB")
    assert lat == pytest.approx(49.44122314453125)
    assert elevation == pytest.approx(ELEVATION)
    assert name == "Guernsey: Airport"
    assert tz == "Europe/Guernsey"


def test_wind_direction_cardinal_and_numeric() -> None:
    assert metoffice._wind_direction_degrees("SSW") == pytest.approx(202.5)
    assert metoffice._wind_direction_degrees(200) == pytest.approx(200.0)
    assert math.isnan(metoffice._wind_direction_degrees(""))


def test_fetch_current_requires_api_key() -> None:
    with pytest.raises(ProviderError) as excinfo:
        _run(metoffice.fetch_current(STATION, ""))
    assert excinfo.value.error_code == "provider_unauthorized"


def test_fetch_current_uses_last_row_and_lowercases_geohash() -> None:
    client = _client()
    # El schema normaliza a mayúsculas; el servicio debe revertirlo.
    result = _run(metoffice.fetch_current("GBY1KB", "K", client=client))

    assert client._captured["path"].endswith("/observation-land/1/gby1kb")
    assert client._captured["apikey"] == "K"

    assert result["Tc"] == pytest.approx(17.0)
    assert result["p_hpa"] == pytest.approx(1012.0)
    # Absoluta derivada de MSL
    assert result["p_abs_hpa"] == pytest.approx(1012.0 / math.exp(ELEVATION / 8000.0))
    assert result["wind"] == pytest.approx(21.6)  # 6 m/s
    # Racha: la última fila no trae → fallback a la fila anterior
    assert result["gust"] == pytest.approx(36.0)
    assert result["wind_dir_deg"] == pytest.approx(200.0)
    assert result["station_name"] == "Guernsey: Airport"
    assert math.isnan(result["precip_total"])  # no expone precipitación
    assert not math.isnan(result["Td"])  # add_basic_derived


def test_fetch_current_unauthorized() -> None:
    client = _client(status=403)
    with pytest.raises(ProviderError) as excinfo:
        _run(metoffice.fetch_current(STATION, "K", client=client))
    assert excinfo.value.error_code == "provider_unauthorized"


def test_fetch_current_empty_is_bad_response() -> None:
    client = _client(payload={"data": []})
    with pytest.raises(ProviderError) as excinfo:
        _run(metoffice.fetch_current(STATION, "K", client=client))
    assert excinfo.value.error_code == "provider_bad_response"


def test_fetch_today_series_clips_to_station_local_day() -> None:
    client = _client()
    result = _run(
        metoffice.fetch_today_series(STATION, "K", client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is True
    # La observación de ayer (23h) queda fuera
    assert len(result["epochs"]) == 2
    assert result["temps"] == [pytest.approx(16.0), pytest.approx(17.0)]
    assert result["winds"][0] == pytest.approx(18.0)
    assert result["wind_dirs"][0] == pytest.approx(202.5)  # "SSW"
    assert result["lat"] == pytest.approx(49.44122314453125)


def test_fetch_today_series_empty() -> None:
    client = _client(payload={"data": []})
    result = _run(
        metoffice.fetch_today_series(STATION, "K", client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is False
