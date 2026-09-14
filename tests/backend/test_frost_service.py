"""
Tests del servicio puro ``server.services.frost``.

Cubre el scoring de variantes (resolución/nivel/calidad), el binning,
la heurística de precipitación (contador vs incrementos) y el fan-out
resiliente por elemento cuando la petición combinada falla (412).
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
from server.services import frost


# Ficha de SN100 "PLASSEN" tal como estaba en el catálogo. Salió del
# inventario al actualizarlo (no publica sensores), así que los tests la fijan
# en vez de depender de que siga en data/data_estaciones_frost.json.
STATION = "SN100"
ELEVATION = 333.0
STATION_ROW = {
    "id": STATION, "name": "PLASSEN", "lat": 61.1349, "lon": 12.5039,
    "elev": ELEVATION, "country_code": "NO",
}
TZ = ZoneInfo("Europe/Oslo")


@pytest.fixture(autouse=True)
def _frost_catalog(monkeypatch):
    monkeypatch.setattr(frost, "_load_stations", lambda: [STATION_ROW])

NOW_LOCAL = datetime(2026, 6, 10, 12, 0, tzinfo=TZ)


def _ref(hour: int, minute: int = 0) -> str:
    dt = NOW_LOCAL.replace(hour=hour, minute=minute).astimezone(ZoneInfo("UTC"))
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _obs(element: str, value: float, *, resolution: str = "PT10M",
         level: float | None = None, quality: int = 0) -> dict:
    out = {
        "elementId": element,
        "value": value,
        "timeResolution": resolution,
        "qualityCode": quality,
    }
    if level is not None:
        out["level"] = {"levelType": "height_above_ground", "unit": "m", "value": level}
    return out


LATEST_PAYLOAD = {
    "data": [
        {
            "sourceId": "SN100:0",
            "referenceTime": _ref(11, 50),
            "observations": [
                _obs("air_temperature", 18.0, level=2.0),
                _obs("relative_humidity", 55.0, level=2.0),
                _obs("surface_air_pressure", 975.0),
                _obs("wind_speed", 5.0, level=10.0),
                _obs("wind_speed_of_gust", 10.0, level=10.0),
                _obs("wind_from_direction", 230.0, level=10.0),
            ],
        }
    ]
}

TODAY_PAYLOAD = {
    "data": [
        {
            "sourceId": "SN100:0",
            "referenceTime": _ref(10),
            "observations": [
                _obs("air_temperature", 16.0, level=2.0),
                _obs("relative_humidity", 60.0, level=2.0),
                _obs("surface_air_pressure", 974.0),
                _obs("accumulated(precipitation_amount)", 1.0),
            ],
        },
        {
            "sourceId": "SN100:0",
            "referenceTime": _ref(11),
            "observations": [
                _obs("air_temperature", 17.0, level=2.0),
                _obs("accumulated(precipitation_amount)", 2.5),
            ],
        },
    ]
}


def _routing_client(
    *,
    latest=None,
    today=None,
    combined_status: int = 200,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        reftime = request.url.params.get("referencetime", "")
        if combined_status != 200:
            return httpx.Response(combined_status, json={})
        if reftime == "latest":
            return httpx.Response(200, json=latest if latest is not None else LATEST_PAYLOAD)
        return httpx.Response(200, json=today if today is not None else TODAY_PAYLOAD)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _run(coro):
    return asyncio.run(coro)


# =====================================================================
# Pureza + scoring + precipitación
# =====================================================================

def test_frost_service_does_not_import_streamlit() -> None:
    source = Path("server/services/frost.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source


def test_choose_observation_prefers_better_resolution_and_level() -> None:
    observations = [
        {**_obs("air_temperature", 20.0, resolution="PT1H", level=2.0), "_reference_epoch": 100},
        {**_obs("air_temperature", 21.0, resolution="PT1M", level=2.0), "_reference_epoch": 100},
        {**_obs("air_temperature", 22.0, resolution="PT1M", level=10.0), "_reference_epoch": 100},
    ]
    chosen = frost._choose_observation(observations, "temp_c")
    assert chosen["value"] == pytest.approx(21.0)  # PT1M + nivel 2 m


def test_choose_observation_discards_explicit_bad_quality() -> None:
    observations = [
        {**_obs("air_temperature", 21.0, level=2.0, quality=0), "_reference_epoch": 100},
        {**_obs("air_temperature", 99.0, level=2.0, quality=7), "_reference_epoch": 100},
    ]
    chosen = frost._choose_observation(observations, "temp_c")
    assert chosen["value"] == pytest.approx(21.0)
    assert frost._choose_observation([observations[1]], "temp_c") is None


def test_choose_observation_supports_aggregated_gust_and_prefers_ten_minutes() -> None:
    observations = [
        {**_obs("max(wind_speed_of_gust PT1H)", 8.0, resolution="PT1H", level=10.0), "_reference_epoch": 100},
        {**_obs("max(wind_speed_of_gust PT10M)", 10.0, resolution="PT10M", level=10.0), "_reference_epoch": 100},
    ]
    chosen = frost._choose_observation(observations, "gust_ms")
    assert chosen["value"] == pytest.approx(10.0)


def test_precip_total_counter_mode() -> None:
    # Contador creciente: total = último - primero
    assert frost._precip_total([1.0, 2.5, 4.0], []) == pytest.approx(3.0)


def test_precip_total_counter_with_reset() -> None:
    # Mayoría de diffs negativos (ratio < 0.65) → modo segmentos:
    # diff -4.5 aporta max(0, 0.5)=0.5; diff +1.0 aporta 1.0.
    total = frost._precip_total([5.0, 0.5, 1.5], [])
    assert total == pytest.approx(1.5)


def test_precip_total_steps_fallback() -> None:
    assert frost._precip_total([], [0.2, 0.3, float("nan")]) == pytest.approx(0.5)


def test_station_meta_from_catalog() -> None:
    lat, lon, elevation, name = frost._station_meta(STATION)
    assert lat == pytest.approx(61.1349)
    assert elevation == pytest.approx(ELEVATION)
    assert name == "PLASSEN"


def test_fetch_current_requires_credentials() -> None:
    with pytest.raises(ProviderError) as excinfo:
        _run(frost.fetch_current(STATION, "", ""))
    assert excinfo.value.error_code == "provider_unauthorized"


# =====================================================================
# fetch_current
# =====================================================================

def test_fetch_current_latest_plus_today_precip() -> None:
    client = _routing_client()
    result = _run(
        frost.fetch_current(STATION, "ID", "SECRET", client=client, now=NOW_LOCAL)
    )

    assert result["Tc"] == pytest.approx(18.0)
    assert result["RH"] == pytest.approx(55.0)
    assert result["wind"] == pytest.approx(18.0)   # 5 m/s
    assert result["gust"] == pytest.approx(36.0)
    assert result["p_abs_hpa"] == pytest.approx(975.0)
    assert result["p_hpa"] == pytest.approx(975.0 * math.exp(ELEVATION / 8000.0))

    # Contador acumulado del día: 2.5 - 1.0
    assert result["precip_total"] == pytest.approx(1.5)

    assert result["station_name"] == "PLASSEN"
    assert not math.isnan(result["Td"])


def test_fetch_current_uses_aggregated_gust_variant() -> None:
    latest = {
        "data": [{
            "sourceId": "SN100:0",
            "referenceTime": _ref(11, 50),
            "observations": [
                _obs("air_temperature", 18.0, level=2.0),
                _obs("max(wind_speed_of_gust PT10M)", 12.0, level=10.0),
            ],
        }]
    }
    result = _run(frost.fetch_current(
        STATION, "ID", "SECRET", client=_routing_client(latest=latest), now=NOW_LOCAL,
    ))
    assert result["gust"] == pytest.approx(43.2)


def test_fetch_current_unauthorized_propagates() -> None:
    client = _routing_client(combined_status=401)
    with pytest.raises(ProviderError) as excinfo:
        _run(frost.fetch_current(STATION, "ID", "SECRET", client=client, now=NOW_LOCAL))
    assert excinfo.value.error_code == "provider_unauthorized"


def test_resilient_fanout_on_412() -> None:
    """Si la combinada da 412, se reintenta por elemento y se mergea."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        elements = request.url.params.get("elements", "")
        calls.append(elements)
        if "," in elements:
            return httpx.Response(412, json={})
        if elements == "air_temperature":
            return httpx.Response(200, json={
                "data": [{
                    "sourceId": "SN100:0",
                    "referenceTime": _ref(11),
                    "observations": [_obs("air_temperature", 19.0, level=2.0)],
                }]
            })
        return httpx.Response(412, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    payload = _run(
        frost._request_observations_resilient(
            STATION, "ID", "SECRET", client,
            referencetime="latest", elements=frost.LATEST_ELEMENTS,
            timeout_s=5.0,
        )
    )
    assert len(payload["data"]) == 1
    assert any("," in c for c in calls)          # intentó la combinada
    assert "air_temperature" in calls            # y el fan-out


# =====================================================================
# fetch_today_series
# =====================================================================

def test_fetch_today_series_bins_and_converts() -> None:
    client = _routing_client()
    result = _run(
        frost.fetch_today_series(STATION, "ID", "SECRET", client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is True
    assert len(result["epochs"]) == 2
    assert result["temps"] == [pytest.approx(16.0), pytest.approx(17.0)]
    # MSL derivada de absoluta; segunda fila sin presión → NaN
    assert result["pressures"][0] == pytest.approx(974.0 * math.exp(ELEVATION / 8000.0))
    assert math.isnan(result["pressures"][1])
    assert result["lat"] == pytest.approx(61.1349)


def test_fetch_today_series_empty() -> None:
    client = _routing_client(latest={"data": []}, today={"data": []})
    result = _run(
        frost.fetch_today_series(STATION, "ID", "SECRET", client=client, now=NOW_LOCAL)
    )
    assert result["has_data"] is False


# =====================================================================
# Consumo de la API: no pedir elementos que la estación no mide
# =====================================================================

def _element_counting_handler(counter: list, ausentes: tuple = ()):
    """Frost rechaza la petición ENTERA con un 412 en cuanto uno de los
    elementos pedidos no existe para esa estación; los que sí existen se
    responden con normalidad."""
    def handler(request: httpx.Request) -> httpx.Response:
        pedidos = [e for e in request.url.params.get("elements", "").split(",") if e]
        counter.append(pedidos)
        if any(element in ausentes for element in pedidos):
            return httpx.Response(412, json={"error": {"reason": "elemento inexistente"}})
        return httpx.Response(200, json={"data": [{
            "referenceTime": "2026-09-06T12:00:00.000Z",
            "observations": [
                {"elementId": element, "value": 11.0, "unit": "degC"}
                for element in pedidos
            ],
        }]})
    return handler


def test_elements_are_trimmed_to_what_the_catalogue_knows() -> None:
    """Frost rechaza la petición ENTERA con un 412 si le cuelas un elemento que
    la estación no mide, y entonces hay que preguntar uno por uno: de 2
    llamadas a 22. Con su rate limit eso se paga caro."""
    todos = frost.LATEST_ELEMENTS
    # SN1070 mide temperatura y lluvia, no presión ni viento.
    recortado = frost._elements_for_station("SN1070", todos)
    assert len(recortado) < len(todos)
    assert "air_temperature" in recortado
    assert "surface_air_pressure" not in recortado


def test_a_catalogue_that_knows_nothing_does_not_trim() -> None:
    """2.191 de las 3.462 estaciones noruegas tienen TODOS los sensores a falso.
    Eso no significa que no midan nada, sino que no lo sabemos: filtrar ahí las
    dejaría sin un solo dato."""
    todos = frost.LATEST_ELEMENTS
    assert frost._elements_for_station("SN52750", todos) == todos


def test_the_fan_out_is_learnt_so_it_only_happens_once() -> None:
    """Como el catálogo solo conoce a un tercio de la red, el resto pagaría el
    fan-out en cada visita. Lo que contestó se recuerda: la siguiente consulta
    vuelve a ser una sola llamada."""
    frost._working_elements.clear()
    counter: list = []
    # La estación mide temperatura y humedad, pero no presión.
    elementos = ("air_temperature", "relative_humidity", "surface_air_pressure")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            _element_counting_handler(counter, ausentes=("surface_air_pressure",)),
        ),
        timeout=5.0,
    )
    try:
        _run(frost._request_observations_resilient(
            "SN52750", "id", "secret", client,
            referencetime="latest", elements=elementos, timeout_s=5.0,
        ))
        llamadas_primera = len(counter)
        counter.clear()
        _run(frost._request_observations_resilient(
            "SN52750", "id", "secret", client,
            referencetime="latest", elements=elementos, timeout_s=5.0,
        ))
        llamadas_segunda = len(counter)
    finally:
        _run(client.aclose())
        frost._working_elements.clear()

    # Primera: la combinada (412 por la presión) + una por elemento.
    assert llamadas_primera == 1 + len(elementos)
    # Segunda: una sola petición, ya sin el elemento que no existe.
    assert llamadas_segunda == 1
    assert counter[0] == ["air_temperature", "relative_humidity"]


def test_stale_memory_is_dropped_and_relearnt() -> None:
    """Si lo recordado deja de valer (la estación cambia de sensores), se
    descarta y se vuelve a aprender en vez de fallar para siempre."""
    frost._working_elements.clear()
    key = ("SN52750", "air_temperature")
    frost._remember_working(key, ("air_temperature",))
    assert frost._recall_working(key) == ("air_temperature",)
    frost._forget_working(key)
    assert frost._recall_working(key) is None

    # Y caduca sola: la entrada lleva su propio vencimiento.
    frost._working_elements[key] = (0.0, ("air_temperature",))
    assert frost._recall_working(key) is None
    frost._working_elements.clear()
