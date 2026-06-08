"""
Tests del servicio puro ``server.services.wu``.

Estos tests no levantan FastAPI; ejercitan ``fetch_current`` directamente
contra un ``httpx.MockTransport``. Sirven para fijar el contrato del
servicio antes de que la capa HTTP lo serialice.

También testan la función pura ``_normalize_current_observation`` (sin
red ni HTTP) para no depender del transporte cuando lo que validamos es
la lógica meteo.
"""

from __future__ import annotations

import math

import httpx
import pytest

from server.schemas.errors import ProviderError
from server.services import wu

from .conftest import WU_OK_OBSERVATION, make_mock_client


# =====================================================================
# Pureza: el módulo no debe arrastrar streamlit por importación
# =====================================================================

def test_service_does_not_import_streamlit() -> None:
    """
    Garantiza que el servicio se mantiene libre de Streamlit.

    Hacemos chequeo textual del fuente (no de ``sys.modules``): en una
    suite mixta otros tests pueden cargar Streamlit antes de que este
    corra, así que ``"streamlit" in sys.modules`` no prueba nada. El
    fuente sí.
    """
    from pathlib import Path

    source = Path("server/services/wu.py").read_text(encoding="utf-8")

    assert "import streamlit" not in source, (
        "server/services/wu.py importa streamlit; el backend debe ser puro."
    )
    assert "from streamlit" not in source, (
        "server/services/wu.py importa de streamlit; el backend debe ser puro."
    )


# =====================================================================
# Normalizador: lógica meteo pura, sin red
# =====================================================================

def test_normalize_basic_shape() -> None:
    """El normalizador devuelve exactamente las claves del contrato legacy."""
    obs = WU_OK_OBSERVATION["observations"][0]
    metric = obs["metric"]

    result = wu._normalize_current_observation(obs, metric)

    expected_keys = {
        "Tc", "RH", "p_hpa", "Td", "wind", "gust",
        "feels_like", "heat_index", "wind_chill",
        "precip_rate", "precip_total", "wind_dir_deg",
        "solar_radiation", "uv",
        "epoch", "time_local", "time_utc",
        "lat", "lon", "elevation",
    }
    assert set(result.keys()) == expected_keys


def test_normalize_heat_index_filter_low_temp() -> None:
    """
    Si Tc < HEAT_INDEX_MIN_TEMP (25 °C), el heat_index reportado por WU
    se descarta porque queda fuera del rango válido NOAA.
    """
    obs = {"epoch": 0, "humidity": 50, "winddir": 0}
    # Aunque WU reporte heatIndex en su payload, lo IGNORAMOS y lo calculamos
    # nosotros con Rothfusz. Para Tc=20 y RH=50, Rothfusz da un valor positivo
    # razonable (la fórmula es polinómica y no tiene "filtro" duro a 25 °C —
    # solo es físicamente significativa con calor + humedad altos).
    metric = {"temp": 20.0, "heatIndex": 99.0, "windSpeed": 0, "windGust": 0}

    result = wu._normalize_current_observation(obs, metric)

    # heat_index calculado nuestro != el inventado en metric ("99.0")
    assert not math.isnan(result["heat_index"])
    assert result["heat_index"] != pytest.approx(99.0)


def test_normalize_wind_chill_is_always_nan_in_stateless_current() -> None:
    """
    No calculamos ``wind_chill`` en el servicio stateless ``/current``
    (queda NaN). El pipeline lo deja igualmente vacío; podría añadirse
    en una iteración futura si se quiere exponer wind chill calculado.
    Lo importante es: si WU devuelve un valor, NO lo propagamos.
    """
    obs = {"epoch": 0, "humidity": 50, "winddir": 0}
    metric = {"temp": 5.0, "windChill": -3.0, "windSpeed": 20.0, "windGust": 0}

    result = wu._normalize_current_observation(obs, metric)

    assert math.isnan(result["wind_chill"])


def test_normalize_feels_like_uses_steadman_independent_of_wu_payload() -> None:
    """
    ``feels_like`` se calcula SIEMPRE con Steadman 1984
    (T + 0.33·e − 0.70·v − 4), ignorando el valor que WU exponga.
    """
    # WU reporta valores absurdos en feelsLike/heatIndex/windChill;
    # nuestro feels_like calculado NO los toma — usa Tc/RH/wind primarios.
    obs = {"epoch": 0, "humidity": 50, "winddir": 0}
    metric = {
        "temp": 20.0, "windSpeed": 10.0, "windGust": 0,
        "windChill": -50.0, "heatIndex": 99.0,  # intentamos contaminar
    }
    result = wu._normalize_current_observation(obs, metric)

    # Aproximación: T + 0.33·e − 0.70·(wind_kmh/3.6) − 4
    # Con Tc=20, RH=50 → e ≈ 11.7 hPa; wind_ms ≈ 2.78
    # feels_like ≈ 20 + 0.33*11.7 - 0.70*2.78 - 4 ≈ 17.92
    assert result["feels_like"] == pytest.approx(17.92, abs=0.2)


def test_normalize_td_calculated_with_magnus_tetens_not_from_wu_payload() -> None:
    """
    ``Td`` se calcula a partir de Tc + RH (Magnus-Tetens vía la presión
    de vapor de Tetens). Aunque WU exponga ``dewpt`` en el payload, lo
    ignoramos para que el valor sea consistente con el resto de proveedores.
    """
    obs = {"epoch": 0, "humidity": 65, "winddir": 0}
    metric = {"temp": 22.0, "dewpt": -99.0, "windSpeed": 0, "windGust": 0}

    result = wu._normalize_current_observation(obs, metric)

    # Magnus-Tetens para 22 °C y 65 % RH ≈ 15.13 °C; NO el -99 de WU.
    assert result["Td"] == pytest.approx(15.13, abs=0.2)


def test_normalize_precip_rate_is_nan_in_stateless_current() -> None:
    """
    ``precip_rate`` requiere historial temporal para calcularse (intervalo
    entre dos medidas consecutivas). En ``/current`` stateless no hay
    historial; queda NaN y el pipeline lo computa desde la serie del día
    en ``/processed``.
    """
    obs = {"epoch": 0, "humidity": 50, "winddir": 0}
    metric = {"temp": 22.0, "precipRate": 12.5, "windSpeed": 0, "windGust": 0}

    result = wu._normalize_current_observation(obs, metric)

    # Aunque WU reporte 12.5 mm/h, nosotros NO lo propagamos.
    assert math.isnan(result["precip_rate"])


def test_normalize_epoch_falls_back_to_now_when_missing() -> None:
    """
    Si WU no devuelve epoch válido, usamos ``time.time()`` como fallback.
    No queremos romper la app con epoch=0.
    """
    import time as _time

    obs = {"humidity": 50, "winddir": 0}  # sin epoch
    metric = {"temp": 20.0, "windSpeed": 0, "windGust": 0}

    before = int(_time.time())
    result = wu._normalize_current_observation(obs, metric)
    after = int(_time.time())

    assert before <= result["epoch"] <= after


def test_normalize_rain_total_is_quantized() -> None:
    """
    precipTotal pasa por ``_quantize_rain_mm_wu`` (factor 1.0049 y
    redondeo a múltiplos de 0.4 mm).
    """
    obs = {"epoch": 1, "humidity": 50, "winddir": 0}
    # 0.4 mm * 1.0049 = 0.40196 → round(0.40196 / 0.4) = 1 → 0.4 mm
    metric = {"temp": 20.0, "windSpeed": 0, "windGust": 0, "precipTotal": 0.4}

    result = wu._normalize_current_observation(obs, metric)

    assert result["precip_total"] == pytest.approx(0.4)


# =====================================================================
# fetch_current: cubre los 7 mapeos de error
# =====================================================================

@pytest.mark.asyncio
async def test_fetch_current_success_returns_normalized_dict() -> None:
    client = make_mock_client(status=200, json_body=WU_OK_OBSERVATION)
    try:
        result = await wu.fetch_current("ITEST123", "fake_key", client=client)
    finally:
        await client.aclose()

    assert result["Tc"] == pytest.approx(22.0)
    assert result["RH"] == pytest.approx(65.0)
    assert result["wind_dir_deg"] == pytest.approx(180.0)
    assert result["epoch"] == 1717255200


@pytest.mark.asyncio
async def test_fetch_current_401_maps_to_provider_unauthorized() -> None:
    client = make_mock_client(status=401)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await wu.fetch_current("ITEST", "bad_key", client=client)
    finally:
        await client.aclose()

    assert excinfo.value.error_code == "provider_unauthorized"
    assert excinfo.value.status_code == 401
    assert excinfo.value.provider == "WU"


@pytest.mark.asyncio
async def test_fetch_current_404_maps_to_station_not_found() -> None:
    client = make_mock_client(status=404)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await wu.fetch_current("INOEXISTE", "fake", client=client)
    finally:
        await client.aclose()

    assert excinfo.value.error_code == "station_not_found"
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_fetch_current_429_maps_to_provider_ratelimit() -> None:
    client = make_mock_client(status=429)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await wu.fetch_current("ITEST", "fake", client=client)
    finally:
        await client.aclose()

    assert excinfo.value.error_code == "provider_ratelimit"
    assert excinfo.value.status_code == 429


@pytest.mark.asyncio
async def test_fetch_current_5xx_maps_to_provider_http_error() -> None:
    client = make_mock_client(status=503)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await wu.fetch_current("ITEST", "fake", client=client)
    finally:
        await client.aclose()

    assert excinfo.value.error_code == "provider_http_error"
    assert excinfo.value.status_code == 502  # mapeamos cualquier upstream 5xx a 502 (Bad Gateway)


@pytest.mark.asyncio
async def test_fetch_current_timeout_maps_to_provider_timeout() -> None:
    client = make_mock_client(raise_exc=httpx.TimeoutException("read timeout"))
    try:
        with pytest.raises(ProviderError) as excinfo:
            await wu.fetch_current("ITEST", "fake", client=client)
    finally:
        await client.aclose()

    assert excinfo.value.error_code == "provider_timeout"
    assert excinfo.value.status_code == 504


@pytest.mark.asyncio
async def test_fetch_current_network_error_maps_to_provider_network_error() -> None:
    client = make_mock_client(raise_exc=httpx.ConnectError("dns fail"))
    try:
        with pytest.raises(ProviderError) as excinfo:
            await wu.fetch_current("ITEST", "fake", client=client)
    finally:
        await client.aclose()

    assert excinfo.value.error_code == "provider_network_error"
    assert excinfo.value.status_code == 502


@pytest.mark.asyncio
async def test_fetch_current_empty_observations_maps_to_provider_bad_response() -> None:
    """``observations: []`` (estación válida pero sin datos) es payload inválido."""
    client = make_mock_client(status=200, json_body={"observations": []})
    try:
        with pytest.raises(ProviderError) as excinfo:
            await wu.fetch_current("ITEST", "fake", client=client)
    finally:
        await client.aclose()

    assert excinfo.value.error_code == "provider_bad_response"


@pytest.mark.asyncio
async def test_fetch_current_missing_metric_key_maps_to_provider_bad_response() -> None:
    """Si WU devuelve ``observations[0]`` sin ``metric``, también es payload roto."""
    client = make_mock_client(
        status=200,
        json_body={"observations": [{"epoch": 1, "humidity": 50}]},
    )
    try:
        with pytest.raises(ProviderError) as excinfo:
            await wu.fetch_current("ITEST", "fake", client=client)
    finally:
        await client.aclose()

    assert excinfo.value.error_code == "provider_bad_response"


# =====================================================================
# fetch_today_series: normalizador puro
# =====================================================================

def _make_obs(epoch: int, *, temp: float = 20.0, **extra) -> dict:
    """Helper para construir observaciones de /all/1day en tests."""
    metric = {"tempAvg": temp}
    metric.update(extra.pop("metric", {}))
    obs = {"epoch": epoch, "metric": metric, "humidityAvg": 50}
    obs.update(extra)
    return obs


def test_normalize_today_series_skips_points_without_temperature() -> None:
    """Sin temperatura el punto no se incluye (la app no puede pintar sin Y)."""
    observations = [
        _make_obs(1000, temp=20.0),
        {"epoch": 2000, "metric": {}, "humidityAvg": 60},  # sin temp
        _make_obs(3000, temp=21.0),
    ]

    result = wu._normalize_today_series(observations)

    assert result["epochs"] == [1000, 3000]
    assert result["temps"] == [20.0, 21.0]
    assert result["has_data"] is True


def test_normalize_today_series_propagates_nan_for_missing_fields() -> None:
    """
    Si un campo no aparece en la observación, su slot debe ser NaN
    (la longitud del array se mantiene == len(epochs)).
    """
    observations = [_make_obs(1000, temp=20.0)]  # solo temp + humidity

    result = wu._normalize_today_series(observations)

    assert len(result["epochs"]) == 1
    # No reportamos dewpt → la posición debe ser NaN
    assert math.isnan(result["dewpts"][0])
    assert math.isnan(result["solar_radiations"][0])
    assert math.isnan(result["wind_dirs"][0])


def test_normalize_today_series_takes_first_observation_with_coords() -> None:
    """lat/lon se toman de la primera observación que las trae."""
    observations = [
        _make_obs(1000, temp=20.0),  # sin coords
        _make_obs(2000, temp=21.0, lat=41.387, lon=2.169),
        _make_obs(3000, temp=22.0, lat=40.0, lon=3.0),  # ignoradas
    ]

    result = wu._normalize_today_series(observations)

    assert result["lat"] == pytest.approx(41.387)
    assert result["lon"] == pytest.approx(2.169)


def test_normalize_today_series_empty_observations_returns_no_data() -> None:
    result = wu._normalize_today_series([])
    assert result["epochs"] == []
    assert result["has_data"] is False
    assert math.isnan(result["lat"])


def test_normalize_today_series_handles_non_dict_observation() -> None:
    """Items rotos (None, string, list) no rompen el bucle."""
    observations = [None, "garbage", [], _make_obs(1000, temp=20.0)]
    result = wu._normalize_today_series(observations)
    assert result["epochs"] == [1000]


# =====================================================================
# fetch_today_series: errores HTTP/red (mismo mapeo que fetch_current)
# =====================================================================

@pytest.mark.asyncio
async def test_fetch_today_series_success() -> None:
    body = {
        "observations": [
            _make_obs(1000, temp=20.0, lat=41.4, lon=2.2),
            _make_obs(2000, temp=21.0),
        ]
    }
    client = make_mock_client(status=200, json_body=body)
    try:
        result = await wu.fetch_today_series("ITEST", "fake", client=client)
    finally:
        await client.aclose()

    assert result["has_data"] is True
    assert result["epochs"] == [1000, 2000]
    assert result["temps"] == [20.0, 21.0]
    assert result["lat"] == pytest.approx(41.4)


@pytest.mark.asyncio
async def test_fetch_today_series_401_raises_provider_unauthorized() -> None:
    client = make_mock_client(status=401)
    try:
        with pytest.raises(ProviderError) as excinfo:
            await wu.fetch_today_series("ITEST", "bad", client=client)
    finally:
        await client.aclose()
    assert excinfo.value.error_code == "provider_unauthorized"


@pytest.mark.asyncio
async def test_fetch_today_series_timeout_raises_provider_timeout() -> None:
    client = make_mock_client(raise_exc=httpx.TimeoutException("slow"))
    try:
        with pytest.raises(ProviderError) as excinfo:
            await wu.fetch_today_series("ITEST", "fake", client=client)
    finally:
        await client.aclose()
    assert excinfo.value.error_code == "provider_timeout"


@pytest.mark.asyncio
async def test_fetch_today_series_200_with_garbage_returns_empty_series() -> None:
    """
    Si WU responde 200 con un payload sin ``observations``, no es razón
    para tirar la pestaña entera; devolvemos series vacías (mismo
    criterio que el legacy).
    """
    client = make_mock_client(status=200, json_body={"unexpected": "shape"})
    try:
        result = await wu.fetch_today_series("ITEST", "fake", client=client)
    finally:
        await client.aclose()
    assert result["has_data"] is False
    assert result["epochs"] == []
