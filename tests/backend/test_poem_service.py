"""
Tests del servicio puro ``server.services.poem``.

POEM usa endpoints por estación (catálogo local) y feeds con escalas
peculiares; el parsing pesado vive en ``domain/parsing/poem.py``
y aquí se prueba el ensamblado canónico.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import poem
from domain.parsing.common import parse_epoch


# Estación real del catálogo: 1103 "Boya Costera de Bilbao",
# tr_endpoint=/doris/boyas/redcos_tr, parametros_meteo_cols=['ts'].
STATION = "1103"
LOCAL_TZ = ZoneInfo("Europe/Madrid")
NOW_LOCAL = datetime(2026, 6, 10, 12, 0, tzinfo=LOCAL_TZ)


def _fecha(hour: int, minute: int = 0) -> str:
    dt = NOW_LOCAL.replace(hour=hour, minute=minute).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _epoch_of(fecha: str) -> int:
    return int(parse_epoch(fecha))


TR_PAYLOAD = {
    "datos": [
        {"codigo": 1103, "fecha": _fecha(9, 0), "ts": 180},   # décimas → 18.0 °C
        {"codigo": 1103, "fecha": _fecha(10, 0), "ts": 185},  # → 18.5 °C
    ]
}


def _client(payload=None, status: int = 200) -> httpx.AsyncClient:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["codigo"] = request.url.params.get("codigo", "")
        return httpx.Response(status, json=payload if payload is not None else TR_PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    client._captured = captured  # type: ignore[attr-defined]
    return client


def _run(coro):
    return asyncio.run(coro)


def test_poem_service_does_not_import_streamlit() -> None:
    for path in ("server/services/poem.py", "domain/parsing/poem.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert "import streamlit" not in source, path
        assert "from streamlit" not in source, path


def test_find_station_normalizes_token() -> None:
    station = poem._find_station("1103")
    assert station.get("nombre") == "Boya Costera de Bilbao"
    assert station.get("tr_endpoint") == "/doris/boyas/redcos_tr"


def test_unknown_station_is_station_not_found() -> None:
    client = _client()
    with pytest.raises(ProviderError) as excinfo:
        _run(poem.fetch_current("99999", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "station_not_found"


def test_fetch_current_parses_tr_feed_with_scales() -> None:
    client = _client()
    result = _run(poem.fetch_current(STATION, client=client, now=NOW_LOCAL))

    assert client._captured["path"] == "/doris/boyas/redcos_tr"
    assert client._captured["codigo"] == "1103"

    # ts en décimas de grado → última lectura 18.5 °C
    assert result["Tc"] == pytest.approx(18.5)
    assert result["epoch"] == _epoch_of(_fecha(10, 0))
    assert result["elevation"] == pytest.approx(0.0)
    assert result["station_name"] == "Boya Costera de Bilbao"
    # Catálogo limita las métricas a ts → resto NaN
    assert math.isnan(result["RH"])
    assert math.isnan(result["wind"])


def test_fetch_current_empty_feed_is_bad_response() -> None:
    client = _client(payload={"datos": []})
    with pytest.raises(ProviderError) as excinfo:
        _run(poem.fetch_current(STATION, client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_bad_response"


def test_fetch_current_unauthorized_propagates() -> None:
    client = _client(status=401)
    with pytest.raises(ProviderError) as excinfo:
        _run(poem.fetch_current(STATION, client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_unauthorized"


def test_fetch_current_stale_series_is_bad_response() -> None:
    stale = {
        "datos": [
            {"codigo": 1103, "fecha": "2020-01-01T10:00:00", "ts": 180},
        ]
    }
    client = _client(payload=stale)
    with pytest.raises(ProviderError) as excinfo:
        _run(poem.fetch_current(STATION, client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_bad_response"


def test_fetch_today_series_clips_to_local_day() -> None:
    payload = {
        "datos": [
            {"codigo": 1103, "fecha": _fecha(9, 0), "ts": 180},
            {"codigo": 1103, "fecha": _fecha(10, 0), "ts": 185},
        ]
    }
    client = _client(payload=payload)
    result = _run(poem.fetch_today_series(STATION, client=client, now=NOW_LOCAL))

    assert result["has_data"] is True
    assert len(result["epochs"]) == 2
    assert result["temps"] == [pytest.approx(18.0), pytest.approx(18.5)]
    assert result["lat"] == pytest.approx(43.397)


def test_fetch_today_series_empty() -> None:
    client = _client(payload={"datos": []})
    result = _run(poem.fetch_today_series(STATION, client=client, now=NOW_LOCAL))
    assert result["has_data"] is False
