"""El día al que pertenecen los agregados de Meteocat.

De madrugada el día en curso tiene muy pocas estaciones con datos, así que el
adaptador vuelve a pedir el día anterior. Ese agregado es de ayer y tiene que
archivarse en ayer: sin la marca, el store le ponía la fecha del momento del
ciclo y a las 00:15 el ranking mundial se llenaba con las máximas de la tarde
anterior en Cataluña.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from server.services import meteocat, meteocat_open_data, ranking


@pytest.fixture
def dias_pedidos(monkeypatch):
    """Simula Dades Obertes y registra las ventanas temporales solicitadas."""
    pedidos: list[tuple[datetime, datetime]] = []
    poblacion: dict[date, int] = {}

    async def _maps(start, end, variable_codes, **kwargs):
        pedidos.append((start, end))
        out = {}
        tz = ZoneInfo(ranking.PROVIDER_TZ["METEOCAT"])
        for day, count in poblacion.items():
            epoch = int(datetime.combine(day, time(0, 30), tzinfo=tz).timestamp())
            if int(start.timestamp()) <= epoch <= int(end.timestamp()):
                for i in range(count):
                    out[f"X{i}"] = {
                        meteocat.V_TEMP: [(epoch, 20.0 + i)],
                        meteocat.V_TEMP_MAX: [(epoch, 21.0 + i)],
                        meteocat.V_TEMP_MIN: [(epoch, 19.0 + i)],
                    }
        return out

    monkeypatch.setattr(meteocat_open_data, "fetch_variable_maps", _maps)
    return pedidos, poblacion


@pytest.mark.asyncio
async def test_the_fallback_records_belong_to_the_day_they_came_from(dias_pedidos):
    pedidos, poblacion = dias_pedidos
    hoy = ranking.datetime.now(ranking.ZoneInfo(ranking.PROVIDER_TZ["METEOCAT"])).date()
    ayer = hoy - timedelta(days=1)
    # Madrugada: hoy casi vacío, ayer completo.
    poblacion[hoy] = 2
    poblacion[ayer] = 40

    recs = await ranking.fetch_meteocat_daily("clave-falsa")

    assert len(pedidos) == 2, "con hoy vacío tiene que completar la ventana de ayer"
    assert recs, "el fallback tiene que devolver el día anterior"
    assert {r.local_date for r in recs} == {ayer.isoformat()}


@pytest.mark.asyncio
async def test_a_normal_cycle_belongs_to_today(dias_pedidos):
    pedidos, poblacion = dias_pedidos
    hoy = ranking.datetime.now(ranking.ZoneInfo(ranking.PROVIDER_TZ["METEOCAT"])).date()
    poblacion[hoy] = 40

    recs = await ranking.fetch_meteocat_daily("clave-falsa")

    assert recs
    assert {r.local_date for r in recs} == {hoy.isoformat()}


def test_the_store_respects_the_day_the_record_carries():
    """Es lo que impide que un agregado de ayer acabe en el bucket de hoy."""
    store = ranking.RankingStore()
    ayer = "2026-09-06"
    rec = ranking.StationDaily(
        provider="METEOCAT", station_id="X4", name="Vinebre",
        tmax=42.5, local_date=ayer,
    )
    assert store._bucket_day("METEOCAT", rec, fallback_day="2026-09-07") == ayer
