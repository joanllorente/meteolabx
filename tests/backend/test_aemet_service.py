"""
Tests del servicio puro ``server.services.aemet``.

Estructura paralela a ``test_wu_service.py``: unit tests del
normalizador + tests del fetcher con ``httpx.MockTransport``.

AEMET tiene patrón **2-step** (response 1: URL temporal; response 2:
datos). Los mocks deben rutear por URL para distinguir paso 1 de paso 2.
"""

from __future__ import annotations

import math
from pathlib import Path

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import aemet


# =====================================================================
# Pureza
# =====================================================================

def test_aemet_service_does_not_import_streamlit() -> None:
    """Garantía estática: server/services/aemet.py es puro."""
    source = Path("server/services/aemet.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


# =====================================================================
# _parse_num
# =====================================================================

@pytest.mark.parametrize(
    "value,expected",
    [
        ("22.4", 22.4),
        ("22,4", 22.4),         # coma decimal
        (22.4, 22.4),
        (22, 22.0),
        ("37.4(27)", 37.4),     # extremo con día entre paréntesis
        ("99/21.1", 21.1),      # dir/vel del viento
        ("Ip", float("nan")),   # no numérico
        ("", float("nan")),
        (None, float("nan")),
        ("--", float("nan")),
    ],
)
def test_parse_num_handles_aemet_quirks(value, expected) -> None:
    result = aemet._parse_num(value)
    if isinstance(expected, float) and math.isnan(expected):
        assert math.isnan(result)
    else:
        assert result == pytest.approx(expected)


# =====================================================================
# _parse_wind_dir_deg
# =====================================================================

@pytest.mark.parametrize(
    "value,expected",
    [
        (180.0, 180.0),
        ("180", 180.0),
        (450.0, 90.0),       # módulo 360
        ("NNE", 22.5),
        ("nne", 22.5),       # case-insensitive
        ("E", 90.0),
        ("ENE", 67.5),
        ("calma", 0.0),
        ("CALM", 0.0),
        ("xyz", float("nan")),
        ("", float("nan")),
        (None, float("nan")),
    ],
)
def test_parse_wind_dir_deg(value, expected) -> None:
    result = aemet._parse_wind_dir_deg(value)
    if isinstance(expected, float) and math.isnan(expected):
        assert math.isnan(result)
    else:
        assert result == pytest.approx(expected)


# =====================================================================
# Normalizador
# =====================================================================

REAL_AEMET_RECORD = {
    "idema": "0201X",
    "fint": "2026-06-01T15:00:00+0000",
    "ubi": "BARCELONA AEROPUERTO",
    "lat": 41.297,
    "lon": 2.07,
    "alt": 4,
    "ta": "22.4",         # temperatura °C
    "tamax": "26.8",
    "tamin": "14.2",
    "hr": "65",            # humedad %
    "pres_nmar": "1015.2", # presión MSL hPa
    "pres": "1014.8",      # presión absoluta hPa
    "vv": "5.0",           # m/s
    "vmax": "8.2",         # m/s
    "dv": "180",
    "prec": "0.4",
}


def test_normalize_aemet_record_basic_shape() -> None:
    result = aemet._normalize_aemet_record(REAL_AEMET_RECORD)

    # Conversión m/s → km/h
    assert result["wind"] == pytest.approx(5.0 * 3.6)
    assert result["gust"] == pytest.approx(8.2 * 3.6)
    assert result["wind_dir_deg"] == pytest.approx(180.0)

    # Presiones: MSL y absoluta nativa
    assert result["p_hpa"] == pytest.approx(1015.2)
    assert result["p_abs_hpa"] == pytest.approx(1014.8)

    # Primarios
    assert result["Tc"] == pytest.approx(22.4)
    assert result["RH"] == pytest.approx(65.0)
    assert result["daily_extremes"]["temp_max"] == pytest.approx(26.8)
    assert result["daily_extremes"]["temp_min"] == pytest.approx(14.2)

    # Derivados (calculados por add_basic_derived, no del API)
    assert not math.isnan(result["Td"]),  "Td debe calcularse desde Tc+RH"
    assert not math.isnan(result["feels_like"]),  "feels_like (Steadman) debe calcularse"
    # heat_index Rothfusz es polinómico, devuelve un float (no NaN aunque T sea baja)
    assert not math.isnan(result["heat_index"])

    # AEMET no reporta radiación
    assert math.isnan(result["solar_radiation"])
    assert math.isnan(result["uv"])

    # Wind chill y precip_rate stateless → NaN
    assert math.isnan(result["wind_chill"])
    assert math.isnan(result["precip_rate"])

    # Metadatos
    assert "idema" not in result
    assert result["station_name"] == "BARCELONA AEROPUERTO"
    assert result["lat"] == pytest.approx(41.297)
    assert result["lon"] == pytest.approx(2.07)
    assert result["elevation"] == 4.0
    assert result["epoch"] > 0


def test_normalize_aemet_record_does_not_propagate_unwanted_fields() -> None:
    """
    Los campos ajenos al contrato canónico no contaminan Td/feels_like/
    heat_index ni precip_rate. Esos siempre se calculan vía
    add_basic_derived (o son NaN en stateless).
    """
    record = {
        **REAL_AEMET_RECORD,
        # Si AEMET devolviera estos (no es típico), los ignoramos.
        "td": "-99.0",
        "feels_like": "999.0",
        "heat_index": "-50.0",
    }
    result = aemet._normalize_aemet_record(record)
    # Td calculado != lo absurdo del payload
    assert abs(result["Td"]) < 50.0


def test_aemet_daily_extremes_never_fall_back_to_ten_minute_series() -> None:
    from server.routers.observations import _build_daily_extremes

    official = _build_daily_extremes(
        {
            "Tc": 50.0,
            "precip_total": 2.6,
            "daily_extremes": {"temp_max": 38.9, "temp_min": 22.4},
        },
        {"temps": [10.0, 55.0], "gusts": [99.0]},
        provider="AEMET",
    )
    assert official.temp_max == pytest.approx(38.9)
    assert official.temp_min == pytest.approx(22.4)
    assert official.precip_total == pytest.approx(2.6)

    missing = _build_daily_extremes(
        {"Tc": 50.0, "precip_total": 2.6, "daily_extremes": {}},
        {"temps": [10.0, 55.0], "gusts": [99.0]},
        provider="AEMET",
    )
    assert missing.temp_max is None
    assert missing.temp_min is None


def test_daily_extremes_aggregate_aemet_official_records() -> None:
    records = [
        {**REAL_AEMET_RECORD, "fint": "2026-06-25T06:30:00+0000", "ta": "22.4", "tamax": "24.0", "tamin": "22.4"},
        {**REAL_AEMET_RECORD, "fint": "2026-06-25T16:40:00+0000", "ta": "38.9", "tamax": "38.9", "tamin": "30.0"},
        {**REAL_AEMET_RECORD, "fint": "2026-06-25T22:00:00+0000", "ta": "31.1", "tamax": "32.1", "tamin": "28.4"},
    ]

    extremes = aemet._daily_extremes_from_aemet_records(records)

    assert extremes["temp_max"] == pytest.approx(38.9)
    assert extremes["temp_min"] == pytest.approx(22.4)


def test_normalize_aemet_record_missing_fields_become_nan() -> None:
    """Record con campos mínimos: lo que falta queda NaN."""
    record = {
        "fint": "2026-06-01T15:00:00+0000",
        "ta": "22.4",
        "hr": "65",
    }
    result = aemet._normalize_aemet_record(record)
    assert result["Tc"] == pytest.approx(22.4)
    assert math.isnan(result["p_hpa"])
    assert math.isnan(result["p_abs_hpa"])
    assert math.isnan(result["wind"])


# =====================================================================
# fetch_current: errores de auth
# =====================================================================

@pytest.mark.asyncio
async def test_fetch_current_empty_api_key_raises_unauthorized() -> None:
    with pytest.raises(ProviderError) as excinfo:
        await aemet.fetch_current("0201X", "")
    assert excinfo.value.error_code == "provider_unauthorized"
    assert excinfo.value.status_code == 401


# =====================================================================
# fetch_current: patrón 2-step con MockTransport
# =====================================================================

def _two_step_handler(
    step1_status: int = 200,
    step1_body=None,
    step2_status: int = 200,
    step2_body=None,
    daily_step1_body=None,
    daily_step2_body=None,
    step1_raise=None,
    step2_raise=None,
):
    """
    Crea un handler de MockTransport que distingue paso 1 (URL termina
    en ``/datos/estacion/{idema}``) del paso 2 (cualquier otra URL).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/valores/climatologicos/diarios/datos/" in url:
            return httpx.Response(step1_status, json=daily_step1_body or STEP1_DAILY_OK)
        if "/observacion/convencional/datos/estacion/" in url:
            if step1_raise is not None:
                raise step1_raise
            return httpx.Response(step1_status, json=step1_body or {})
        if url == STEP1_DAILY_OK["datos"]:
            return httpx.Response(step2_status, json=daily_step2_body or [])
        if step2_raise is not None:
            raise step2_raise
        return httpx.Response(step2_status, json=step2_body or {})
    return handler


# Step 1 OK + Step 2 OK
STEP1_OK = {"estado": 200, "datos": "https://opendata.aemet.es/datos/abc"}
STEP1_DAILY_OK = {"estado": 200, "datos": "https://opendata.aemet.es/datos/daily"}
STEP2_OK = [REAL_AEMET_RECORD]


@pytest.mark.asyncio
async def test_fetch_current_two_step_success() -> None:
    transport = httpx.MockTransport(_two_step_handler(
        step1_body=STEP1_OK,
        step2_body=STEP2_OK,
        daily_step2_body=[{"tmax": "26.8", "tmin": "14.2"}],
    ))
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    try:
        result = await aemet.fetch_current("0201X", "FAKE_KEY", client=client)
    finally:
        await client.aclose()

    assert result["Tc"] == pytest.approx(22.4)
    assert result["RH"] == pytest.approx(65.0)
    assert "idema" not in result


@pytest.mark.asyncio
async def test_fetch_current_uses_climatological_daily_extremes() -> None:
    records = [
        {**REAL_AEMET_RECORD, "fint": "2026-06-25T22:00:00+0000", "ta": "31.1", "tamax": "38.9", "tamin": "26.5"},
    ]
    transport = httpx.MockTransport(_two_step_handler(
        step1_body=STEP1_OK,
        step2_body=records,
        daily_step2_body=[{"tmax": "38.9", "tmin": "22.4"}],
    ))
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    try:
        result = await aemet.fetch_current("9434", "FAKE_KEY", client=client)
    finally:
        await client.aclose()

    assert result["Tc"] == pytest.approx(31.1)
    assert result["daily_extremes"]["temp_max"] == pytest.approx(38.9)
    assert result["daily_extremes"]["temp_min"] == pytest.approx(22.4)


@pytest.mark.asyncio
async def test_fetch_current_falls_back_to_conventional_extremes_when_daily_is_empty() -> None:
    records = [
        {**REAL_AEMET_RECORD, "fint": "2026-06-25T22:00:00+0000", "ta": "31.1", "tamax": "38.9", "tamin": "26.5"},
    ]
    transport = httpx.MockTransport(_two_step_handler(
        step1_body=STEP1_OK,
        step2_body=records,
        daily_step2_body=[],
    ))
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    try:
        result = await aemet.fetch_current("9999X", "FAKE_KEY", client=client)
    finally:
        await client.aclose()

    assert result["daily_extremes"]["temp_max"] == pytest.approx(38.9)
    assert result["daily_extremes"]["temp_min"] == pytest.approx(26.5)


# =====================================================================
# fetch_current: estado AEMET dentro del body
# =====================================================================

@pytest.mark.parametrize(
    "aemet_estado,expected_code",
    [
        (401, "provider_unauthorized"),
        (404, "station_not_found"),
        (429, "provider_ratelimit"),
        (500, "provider_http_error"),
    ],
)
@pytest.mark.asyncio
async def test_fetch_current_aemet_estado_in_body(
    aemet_estado: int, expected_code: str,
) -> None:
    """
    AEMET puede responder HTTP 200 pero con ``estado=401/404/429/500``
    en el JSON del paso 1. El servicio mapea cada caso a su ProviderError.
    """
    transport = httpx.MockTransport(_two_step_handler(
        step1_body={"estado": aemet_estado, "descripcion": "test"},
    ))
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await aemet.fetch_current("0201X", "FAKE_KEY", client=client)
    finally:
        await client.aclose()
    assert excinfo.value.error_code == expected_code


@pytest.mark.asyncio
async def test_fetch_current_aemet_no_datos_url_returns_bad_response() -> None:
    """Estado=200 pero sin URL de datos → bad_response."""
    transport = httpx.MockTransport(_two_step_handler(
        step1_body={"estado": 200},  # falta "datos"
    ))
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await aemet.fetch_current("0201X", "FAKE_KEY", client=client)
    finally:
        await client.aclose()
    assert excinfo.value.error_code == "provider_bad_response"


# =====================================================================
# fetch_current: errores de red en paso 1 vs paso 2
# =====================================================================

@pytest.mark.asyncio
async def test_fetch_current_step1_timeout_raises_provider_timeout() -> None:
    transport = httpx.MockTransport(_two_step_handler(
        step1_raise=httpx.TimeoutException("step1 slow"),
    ))
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await aemet.fetch_current("0201X", "FAKE_KEY", client=client)
    finally:
        await client.aclose()
    assert excinfo.value.error_code == "provider_timeout"


@pytest.mark.asyncio
async def test_fetch_current_step2_timeout_raises_provider_timeout() -> None:
    transport = httpx.MockTransport(_two_step_handler(
        step1_body=STEP1_OK,
        step2_raise=httpx.TimeoutException("step2 slow"),
    ))
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await aemet.fetch_current("0201X", "FAKE_KEY", client=client)
    finally:
        await client.aclose()
    assert excinfo.value.error_code == "provider_timeout"


@pytest.mark.asyncio
async def test_fetch_current_step1_network_error() -> None:
    transport = httpx.MockTransport(_two_step_handler(
        step1_raise=httpx.ConnectError("dns fail"),
    ))
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await aemet.fetch_current("0201X", "FAKE_KEY", client=client)
    finally:
        await client.aclose()
    assert excinfo.value.error_code == "provider_network_error"


# =====================================================================
# Latin-1 fallback en paso 2
# =====================================================================

@pytest.mark.asyncio
async def test_fetch_current_step2_latin1_fallback() -> None:
    """
    Si el paso 2 devuelve bytes que NO son UTF-8 válido pero sí latin-1,
    el servicio debe decodificar con latin-1 y parsear el JSON.
    """
    record_latin1 = {
        "idema": "0201X",
        "fint": "2026-06-01T15:00:00+0000",
        "ubi": "AÑEJA STATION",   # carácter no-ASCII
        "ta": "22.4", "hr": "65",
    }
    import json as _json
    payload_bytes = _json.dumps([record_latin1], ensure_ascii=False).encode("latin-1")

    def handler(request: httpx.Request) -> httpx.Response:
        if "/datos/estacion/" in str(request.url):
            return httpx.Response(200, json=STEP1_OK)
        # Paso 2: devolvemos bytes en latin-1 sin content-type JSON-able
        return httpx.Response(
            200,
            content=payload_bytes,
            headers={"content-type": "text/plain; charset=latin-1"},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, timeout=5.0)
    try:
        result = await aemet.fetch_current("0201X", "FAKE_KEY", client=client)
    finally:
        await client.aclose()
    assert result["station_name"] == "AÑEJA STATION"
