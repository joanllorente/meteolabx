from datetime import datetime, timezone

import httpx
import pytest

from server.services import meteocat_open_data


@pytest.mark.asyncio
async def test_open_data_groups_rows_and_paginates(monkeypatch):
    monkeypatch.setattr(meteocat_open_data, "PAGE_SIZE", 2)
    offsets = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["$offset"])
        offsets.append(offset)
        rows = [
            {"codi_estacio": "C6", "codi_variable": "32", "data_lectura": "2026-09-12T08:00:00", "valor_lectura": "21.5"},
            {"codi_estacio": "C6", "codi_variable": "35", "data_lectura": "2026-09-12T08:00:00", "valor_lectura": "0.2"},
        ] if offset == 0 else []
        return httpx.Response(200, json=rows)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        maps = await meteocat_open_data.fetch_variable_maps(
            datetime(2026, 9, 12, tzinfo=timezone.utc),
            datetime(2026, 9, 13, tzinfo=timezone.utc),
            [32, 35], station_id="c6", client=client,
        )

    assert offsets == [0, 2]
    assert maps["C6"][32][0][1] == 21.5
    assert maps["C6"][35][0][1] == 0.2


@pytest.mark.asyncio
async def test_open_data_translates_rate_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="throttled")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Exception) as caught:
            await meteocat_open_data.fetch_variable_maps(
                datetime(2026, 9, 12, tzinfo=timezone.utc),
                datetime(2026, 9, 13, tzinfo=timezone.utc), [32], client=client,
            )
    assert caught.value.error_code == "provider_ratelimit"


@pytest.mark.asyncio
async def test_variable_codes_are_quoted_in_the_query():
    """En este dataset ``codi_variable`` es una columna de TEXTO.

    Pasarle números hacía que Socrata rechazara la consulta entera con
    «query.soql.type-mismatch: Type mismatch for #IN, is number», así que el
    fallback fallaba SIEMPRE: agotada la cuota de XEMA se propagaba el 429 y
    parecía que Dades Obertes ni se intentaba. El test anterior devolvía filas
    sin mirar la consulta, de modo que no lo veía.
    """
    consultas = []

    def handler(request: httpx.Request) -> httpx.Response:
        consultas.append(request.url.params["$where"])
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await meteocat_open_data.fetch_variable_maps(
            datetime(2026, 9, 12, tzinfo=timezone.utc),
            datetime(2026, 9, 13, tzinfo=timezone.utc),
            [32, 35, 3], station_id="X4", client=client,
        )

    where = consultas[0]
    assert "codi_variable in ('3','32','35')" in where, where
    # Y la estación, que también es texto.
    assert "codi_estacio = 'X4'" in where


@pytest.mark.asyncio
async def test_a_rejected_query_is_reported_as_such(caplog):
    """Un 400 de Socrata no es «la estación no tiene datos»: es una consulta
    mal formada, y hay que poder distinguirlo en el log."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "query.soql.type-mismatch"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Exception) as excinfo:
            await meteocat_open_data.fetch_variable_maps(
                datetime(2026, 9, 12, tzinfo=timezone.utc),
                datetime(2026, 9, 13, tzinfo=timezone.utc),
                [32], station_id="X4", client=client,
            )
    assert "400" in str(excinfo.value.detail)
