"""
Tests del servicio puro ``server.services.meteohub``.

MeteoHub sirve estación + series del día en una petición con query DSL;
los tests cubren conversiones BUFR (Kelvin/Pa/m/s), la resolución del
id codificado y el saneado de presiones no barométricas.
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
from server.services import meteohub


# Estación real del catálogo: agrmet|44.08903|12.27459|carpineta.
STATION = "agrmet|44.08903|12.27459|carpineta"
TZ = ZoneInfo("Europe/Rome")
NOW_LOCAL = datetime(2026, 6, 10, 12, 0, tzinfo=TZ)


def _ref(hour: int, minute: int = 0) -> str:
    dt = NOW_LOCAL.replace(hour=hour, minute=minute).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _product(var: str, values: list, lev: str = "103,2000,0,0") -> dict:
    return {"var": var, "lev": lev, "val": values}


PAYLOAD = {
    "data": [
        {
            "stat": {
                "details": [
                    {"var": "B01019", "val": "Carpineta"},
                    {"var": "B05001", "val": 44.08903},
                    {"var": "B06001", "val": 12.27459},
                    {"var": "B07030", "val": 165.0},
                ],
            },
            "prod": [
                _product("B12101", [
                    {"ref": _ref(10), "val": 293.15},   # 20 °C
                    {"ref": _ref(11), "val": 295.15},   # 22 °C
                ]),
                _product("B13003", [
                    {"ref": _ref(11), "val": 60.0},
                ]),
                _product("B10004", [
                    {"ref": _ref(11), "val": 99500.0},  # Pa → 995 hPa
                ]),
                _product("B11002", [
                    {"ref": _ref(11), "val": 5.0},      # m/s → 18 km/h
                ], lev="103,10000,0,0"),
                _product("B11001", [
                    {"ref": _ref(11), "val": 180.0},
                ], lev="103,10000,0,0"),
                _product("B13011", [
                    {"ref": _ref(10), "val": 0.4},
                    {"ref": _ref(11), "val": 0.2},
                ]),
            ],
        }
    ]
}


def _client(payload=None, status: int = 200) -> httpx.AsyncClient:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(status, json=payload if payload is not None else PAYLOAD)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    client._captured = captured  # type: ignore[attr-defined]
    return client


def _run(coro):
    return asyncio.run(coro)


def test_meteohub_service_does_not_import_streamlit() -> None:
    source = Path("server/services/meteohub.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


def test_pa_to_hpa_sanitizes_non_barometric() -> None:
    assert meteohub._pa_to_hpa(99500.0) == pytest.approx(995.0)
    assert meteohub._pa_to_hpa(995.0) == pytest.approx(995.0)
    assert math.isnan(meteohub._pa_to_hpa(150.0))   # fuera de rango
    assert math.isnan(meteohub._pa_to_hpa(200000.0))


def test_resolve_station_handles_uppercased_id() -> None:
    # El schema de la API normaliza a mayúsculas
    station = meteohub._resolve_station(STATION.upper())
    assert station.get("network") == "agrmet"
    assert station.get("lat") == pytest.approx(44.08903)


def test_resolve_station_falls_back_to_encoded_id() -> None:
    station = meteohub._resolve_station("foo|41.0|2.0|mi-estacion")
    assert station.get("network") == "foo"
    assert station.get("name") == "mi estacion"


def test_unknown_station_is_station_not_found() -> None:
    client = _client()
    with pytest.raises(ProviderError) as excinfo:
        _run(meteohub.fetch_current("no-encoded-id", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "station_not_found"


def test_fetch_current_parses_bufr_units() -> None:
    client = _client()
    result = _run(meteohub.fetch_current(STATION.upper(), client=client, now=NOW_LOCAL))

    # La petición usa la red en minúsculas del catálogo
    assert client._captured["params"]["networks"] == "agrmet"

    assert result["Tc"] == pytest.approx(22.0)
    assert result["RH"] == pytest.approx(60.0)
    assert result["wind"] == pytest.approx(18.0)
    assert result["wind_dir_deg"] == pytest.approx(180.0)

    # Presión: abs 995 hPa → MSL con la elevación de los detalles (165 m)
    assert result["p_abs_hpa"] == pytest.approx(995.0)
    assert result["p_hpa"] == pytest.approx(995.0 * math.exp(165.0 / 8000.0))
    assert result["elevation"] == pytest.approx(165.0)

    # Precipitación: suma del día (0.4 + 0.2)
    assert result["precip_total"] == pytest.approx(0.6)

    assert result["station_name"] == "Carpineta"
    assert not math.isnan(result["Td"])


def test_fetch_current_empty_is_bad_response() -> None:
    client = _client(payload={"data": []})
    with pytest.raises(ProviderError) as excinfo:
        _run(meteohub.fetch_current(STATION, client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_bad_response"


def test_fetch_today_series_canonical() -> None:
    client = _client()
    result = _run(meteohub.fetch_today_series(STATION, client=client, now=NOW_LOCAL))

    assert result["has_data"] is True
    assert len(result["epochs"]) == 2
    assert result["temps"] == [pytest.approx(20.0), pytest.approx(22.0)]
    # MSL por punto; primer punto sin presión → NaN
    assert math.isnan(result["pressures"][0])
    assert result["pressures"][1] == pytest.approx(995.0 * math.exp(165.0 / 8000.0))
    assert result["lat"] == pytest.approx(44.08903)


def test_fetch_today_series_empty() -> None:
    client = _client(payload={"data": []})
    result = _run(meteohub.fetch_today_series(STATION, client=client, now=NOW_LOCAL))
    assert result["has_data"] is False
