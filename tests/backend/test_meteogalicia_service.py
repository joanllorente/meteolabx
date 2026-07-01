"""
Tests del servicio puro ``server.services.meteogalicia``.

MeteoGalicia mezcla dos endpoints (10-minutal + horario) y un sistema
de medidas heterogéneo con scoring. Los mocks rutean por path.
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
from server.services import meteogalicia


LOCAL_TZ = ZoneInfo("Europe/Madrid")

# Estación real del catálogo: 10045 "Mabegondo", lat 43.241367,
# lon -8.262225, altitude 94.0.
STATION = "10045"
ELEVATION = 94.0

# Hoy a mediodía local: los tests de servicio pasan now=NOW_LOCAL (son
# deterministas igualmente) y los de endpoint (/processed), que usan el
# reloj real, encuentran las lecturas dentro del día local en curso.
NOW_LOCAL = datetime.now(LOCAL_TZ).replace(hour=12, minute=0, second=0, microsecond=0)


def _iso_utc(hour: int, minute: int = 0) -> str:
    """Instante de HOY (día local del test) en formato del feed."""
    dt_local = NOW_LOCAL.replace(hour=hour, minute=minute)
    return dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _measure(code: str, value: float, unit: str = "", validation: int = 1) -> dict:
    return {
        "codigoParametro": code,
        "nomeParametro": "",
        "valor": value,
        "unidade": unit,
        "lnCodigoValidacion": validation,
    }


TENMIN_PAYLOAD = {
    "listUltimos10min": [
        {
            "idEstacion": 10045,
            "estacion": "Mabegondo",
            "instanteLecturaUTC": _iso_utc(11, 50),
            "listaMedidas": [
                _measure("TA_AVG_1.5m", 21.5),
                _measure("HR_AVG_1.5m", 60.0),
                _measure("PA_AVG_1.5m", 1002.0),
                _measure("VV_AVG_10m", 4.0, unit="m/s"),
                _measure("VV_MAX_10m", 8.0, unit="m/s"),
                _measure("DV_AVG_10m", 200.0),
                _measure("BIO_AVG_1.5m", 0.12, unit="W/m2"),
            ],
        }
    ]
}

HOURLY_PAYLOAD = {
    "listHorarios": [
        {
            "idEstacion": 10045,
            "listaInstantes": [
                {
                    "instanteLecturaUTC": _iso_utc(10),
                    "listaMedidas": [
                        _measure("TA_AVG_1.5m", 20.0),
                        _measure("HR_AVG_1.5m", 65.0),
                        _measure("PA_AVG_1.5m", 1001.0),
                        _measure("PP_SUM_1.5m", 0.4),
                        _measure("VV_AVG_10m", 3.0, unit="m/s"),
                        _measure("BIO_AVG_1.5m", 0.10, unit="W/m2"),
                    ],
                },
                {
                    "instanteLecturaUTC": _iso_utc(11),
                    "listaMedidas": [
                        _measure("TA_AVG_1.5m", 21.0),
                        _measure("HR_AVG_1.5m", 62.0),
                        _measure("PA_AVG_1.5m", 1001.5),
                        _measure("PP_SUM_1.5m", 0.2),
                        _measure("BIO_AVG_1.5m", 0.11, unit="W/m2"),
                    ],
                },
            ],
        }
    ]
}

DAILY_PAYLOAD = {
    "listDatosDiarios": [
        {
            "listaEstacions": [
                {
                    "idEstacion": 10045,
                    "estacion": "Mabegondo",
                    "listaMedidas": [
                        _measure("TA_MAX_1.5m", 23.2),
                        _measure("TA_MIN_1.5m", 17.16),
                        _measure("HR_MAX_1.5m", 96.0),
                        _measure("HR_MIN_1.5m", 41.0),
                        _measure("VV_MAX_10m", 12.0, unit="m/s"),
                    ],
                }
            ]
        }
    ]
}


def _routing_client(
    *,
    tenmin=None,
    hourly=None,
    daily=None,
    tenmin_status: int = 200,
    hourly_status: int = 200,
    daily_status: int = 200,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "ultimos10min" in path:
            return httpx.Response(tenmin_status, json=tenmin or TENMIN_PAYLOAD)
        if "ultimosHorarios" in path:
            return httpx.Response(hourly_status, json=hourly or HOURLY_PAYLOAD)
        if "datosDiarios" in path:
            return httpx.Response(daily_status, json=daily or DAILY_PAYLOAD)
        return httpx.Response(404, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


# =====================================================================
# Pureza + helpers
# =====================================================================

def test_meteogalicia_service_does_not_import_streamlit() -> None:
    source = Path("server/services/meteogalicia.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


def test_measure_scoring_prefers_avg_and_discards_invalidated() -> None:
    measures = meteogalicia._extract_measures([
        _measure("TA_MAX_1.5m", 25.0),
        _measure("TA_AVG_1.5m", 21.0),
        _measure("TA_AVG_1.5m", 99.0, validation=3),  # invalidada
        _measure("PP_SUM_1.5m", -9999.0),              # centinela
    ])
    assert measures["temp"] == pytest.approx(21.0)
    assert "precip" not in measures


def test_wind_unit_normalization() -> None:
    measures = meteogalicia._extract_measures([
        _measure("VV_AVG_10m", 5.0, unit="m/s"),
        _measure("VV_MAX_10m", 36.0, unit="km/h"),
    ])
    assert measures["wind"] == pytest.approx(18.0)
    assert measures["gust"] == pytest.approx(36.0)


def test_uv_unit_normalization() -> None:
    measures = meteogalicia._extract_measures([
        _measure("BIO_AVG_1.5m", 0.1367, unit="W/m2"),
    ])
    assert measures["uv"] == pytest.approx(5.468)


def test_station_meta_from_catalog() -> None:
    lat, lon, elevation, name = meteogalicia._station_meta(STATION)
    assert lat == pytest.approx(43.241367)
    assert lon == pytest.approx(-8.262225)
    assert elevation == pytest.approx(ELEVATION)
    assert name == "Mabegondo"


# =====================================================================
# fetch_current
# =====================================================================

def test_fetch_current_prefers_tenmin_with_hourly_fallback() -> None:
    client = _routing_client()
    result = _run(
        meteogalicia.fetch_current(STATION, client=client, now=NOW_LOCAL)
    )

    # Valores 10-minutales (más frescos que el horario)
    assert result["Tc"] == pytest.approx(21.5)
    assert result["RH"] == pytest.approx(60.0)
    assert result["wind"] == pytest.approx(14.4)  # 4 m/s → km/h
    assert result["gust"] == pytest.approx(28.8)
    assert result["uv"] == pytest.approx(4.8)

    # Presión MSL derivada de absoluta + altitud catálogo
    assert result["p_abs_hpa"] == pytest.approx(1002.0)
    assert result["p_hpa"] == pytest.approx(1002.0 * math.exp(ELEVATION / 8000.0))

    # Precipitación del día: suma de la serie horaria (0.4 + 0.2)
    assert result["precip_total"] == pytest.approx(0.6)
    assert result["daily_extremes"]["temp_max"] == pytest.approx(23.2)
    assert result["daily_extremes"]["temp_min"] == pytest.approx(17.16)
    assert result["daily_extremes"]["rh_max"] == pytest.approx(96.0)
    assert result["daily_extremes"]["rh_min"] == pytest.approx(41.0)
    assert result["daily_extremes"]["gust_max"] == pytest.approx(43.2)

    assert result["station_name"] == "Mabegondo"
    assert not math.isnan(result["Td"])


def test_fetch_current_uses_official_daily_extremes_not_hourly_means() -> None:
    hourly = {
        "listHorarios": [
            {
                "idEstacion": 10045,
                "listaInstantes": [
                    {
                        "instanteLecturaUTC": _iso_utc(10),
                        "listaMedidas": [
                            _measure("TA_AVG_1.5m", 18.5),
                            _measure("PP_SUM_1.5m", 0.0),
                        ],
                    },
                    {
                        "instanteLecturaUTC": _iso_utc(11),
                        "listaMedidas": [
                            _measure("TA_AVG_1.5m", 22.3),
                            _measure("PP_SUM_1.5m", 0.0),
                        ],
                    },
                ],
            }
        ]
    }
    client = _routing_client(hourly=hourly)
    result = _run(
        meteogalicia.fetch_current(STATION, client=client, now=NOW_LOCAL)
    )

    assert result["Tc"] == pytest.approx(21.5)
    assert result["daily_extremes"]["temp_max"] == pytest.approx(23.2)
    assert result["daily_extremes"]["temp_min"] == pytest.approx(17.16)


def test_fetch_current_falls_back_to_hourly_when_tenmin_fails() -> None:
    client = _routing_client(tenmin_status=500)
    result = _run(
        meteogalicia.fetch_current(STATION, client=client, now=NOW_LOCAL)
    )
    # Último valor horario válido
    assert result["Tc"] == pytest.approx(21.0)
    assert result["RH"] == pytest.approx(62.0)


def test_fetch_current_fails_when_both_endpoints_fail() -> None:
    client = _routing_client(tenmin_status=500, hourly_status=500)
    with pytest.raises(ProviderError):
        _run(meteogalicia.fetch_current(STATION, client=client, now=NOW_LOCAL))


def test_fetch_current_empty_payloads_is_bad_response() -> None:
    client = _routing_client(
        tenmin={"listUltimos10min": []}, hourly={"listHorarios": []},
    )
    with pytest.raises(ProviderError) as excinfo:
        _run(meteogalicia.fetch_current(STATION, client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_bad_response"


# =====================================================================
# fetch_today_series
# =====================================================================

def test_fetch_today_series_filters_to_local_day_and_converts() -> None:
    client = _routing_client()
    result = _run(
        meteogalicia.fetch_today_series(STATION, client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is True
    assert len(result["epochs"]) == 2
    assert result["temps"] == [pytest.approx(20.0), pytest.approx(21.0)]
    # MSL derivada por punto
    assert result["pressures"][0] == pytest.approx(1001.0 * math.exp(ELEVATION / 8000.0))
    # Viento m/s → km/h; segunda hora sin viento → NaN
    assert result["winds"][0] == pytest.approx(10.8)
    assert math.isnan(result["winds"][1])
    assert result["uv_indexes"] == [pytest.approx(4.0), pytest.approx(4.4)]
    assert result["lat"] == pytest.approx(43.241367)


def test_fetch_today_series_empty() -> None:
    client = _routing_client(hourly={"listHorarios": []})
    result = _run(
        meteogalicia.fetch_today_series(STATION, client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is False
    assert result["epochs"] == []
