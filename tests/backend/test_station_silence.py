"""Ocultación de estaciones que el bulk del ranking dejó de recibir.

El caso que lo motivó: Monte Carpegna (MeteoHub, ``dpcn-marche``) se pasó dos
días sin publicar —última observación el 4/9/26 a las 11:00 UTC— mientras sus
119 vecinas de red seguían reportando. Ocupaba su sitio en el mapa y en la
búsqueda para servir una ficha vacía.
"""

from __future__ import annotations

import pytest

from server.services import station_silence as silence

DAY = 24 * 3600
T0 = 1_000_000_000.0

MARCHE = "dpcn-marche"
# El bulk escribe el nombre con espacio y las coordenadas del feed; el catálogo
# lo slugifica y redondea a cinco decimales. Son la misma estación.
CARPEGNA_BULK = "dpcn-marche|43.80088|12.32015|monte carpegna"
CARPEGNA_CATALOGO = "dpcn-marche|43.80088|12.32015|monte-carpegna"
VECINA = "dpcn-marche|43.78058|12.34062|carpegna"


@pytest.fixture(autouse=True)
def _clean():
    silence.clear()
    yield
    silence.clear()


def test_a_station_that_stops_appearing_goes_silent_and_comes_back() -> None:
    silence.record_sweep("METEOHUB_IT", [CARPEGNA_BULK, VECINA], network=MARCHE, now=T0)
    assert not silence.is_silent("METEOHUB_IT", CARPEGNA_CATALOGO)

    # Su red sigue contestando, pero ya sin ella.
    silence.record_sweep("METEOHUB_IT", [VECINA], network=MARCHE, now=T0 + 30 * 3600)
    assert silence.is_silent("METEOHUB_IT", CARPEGNA_CATALOGO)
    assert not silence.is_silent("METEOHUB_IT", VECINA)

    # Vuelve a enviar: reaparece sola, sin intervención.
    silence.record_sweep(
        "METEOHUB_IT", [CARPEGNA_BULK, VECINA], network=MARCHE, now=T0 + 31 * 3600,
    )
    assert not silence.is_silent("METEOHUB_IT", CARPEGNA_CATALOGO)


def test_identities_are_matched_canonically() -> None:
    """El catálogo y el bulk escriben el id de otra forma. Compararlos
    literalmente daría por muda a una estación viva —y la ocultaría para
    siempre, porque su reaparición tampoco casaría—. Es el desajuste que rompió
    21 de los 30 enlaces del ranking italiano."""
    assert silence.canonical_key("METEOHUB_IT", CARPEGNA_BULK) == silence.canonical_key(
        "METEOHUB_IT", CARPEGNA_CATALOGO,
    )
    silence.record_sweep("METEOHUB_IT", [CARPEGNA_BULK], network=MARCHE, now=T0)
    silence.record_sweep("METEOHUB_IT", [], network=MARCHE, now=T0 + 30 * 3600)
    assert silence.is_silent("METEOHUB_IT", CARPEGNA_CATALOGO)


def test_a_network_that_goes_down_hides_nobody() -> None:
    """Si una red deja de contestar, sus estaciones dejan de aparecer sin tener
    culpa. El reloj de cada estación solo avanza cuando SU red ha contestado,
    así que una caída —de una hora o de una semana— no oculta a nadie."""
    silence.record_sweep("METEOHUB_IT", [CARPEGNA_BULK, VECINA], network=MARCHE, now=T0)

    # dpcn-marche no vuelve a contestar en una semana; otras redes sí.
    for day in range(1, 8):
        silence.record_sweep(
            "METEOHUB_IT", ["dpcn-lombardia|45.0|9.0|milano"],
            network="dpcn-lombardia", now=T0 + day * DAY,
        )
    assert not silence.is_silent("METEOHUB_IT", CARPEGNA_CATALOGO)
    assert not silence.is_silent("METEOHUB_IT", VECINA)


def test_networks_are_independent_within_a_provider() -> None:
    """El bulk consulta cada red por separado y devuelve lista vacía tanto si
    falla como si no hay nadie publicando. Sin separar por red, una sola red
    italiana con un mal día se llevaría por delante a sus estaciones sanas."""
    silence.record_sweep("METEOHUB_IT", [VECINA], network=MARCHE, now=T0)
    silence.record_sweep(
        "METEOHUB_IT", ["dpcn-lombardia|45.0|9.0|milano"],
        network="dpcn-lombardia", now=T0,
    )
    # Solo Lombardía sigue contestando durante dos días.
    silence.record_sweep(
        "METEOHUB_IT", [], network="dpcn-lombardia", now=T0 + 2 * DAY,
    )
    assert silence.is_silent("METEOHUB_IT", "dpcn-lombardia|45.00000|9.00000|milano")
    assert not silence.is_silent("METEOHUB_IT", VECINA)


def test_stations_the_bulk_never_sees_are_never_hidden() -> None:
    """El bulk no recorre NWS entero ni el 85 % de IEM ni las redes de
    credencial propia. Ocultar por ausencia borraría ~180 000 estaciones sanas:
    el silencio se mide contra un avistamiento previo, nunca contra la falta de
    uno."""
    silence.record_sweep("METEOHUB_IT", [VECINA], network=MARCHE, now=T0 + 10 * DAY)
    assert not silence.is_silent("NWS", "KJFK")
    assert not silence.is_silent("IEM", "AK_ASOS|PAKF")
    assert not silence.is_silent("WU", "IBARCE1")
    assert silence.silent_identities() == frozenset()


def test_an_empty_but_healthy_network_does_hide_its_stations() -> None:
    """Una red que contesta y no trae a nadie es distinta de una caída: ahí el
    silencio sí es de las estaciones."""
    silence.record_sweep("IPMA", ["1200535"], now=T0)
    silence.record_sweep("IPMA", [], now=T0 + 2 * DAY)
    assert silence.is_silent("IPMA", "1200535")


def test_silent_identities_lists_everything_hidden() -> None:
    silence.record_sweep("METEOHUB_IT", [CARPEGNA_BULK, VECINA], network=MARCHE, now=T0)
    silence.record_sweep("METEOHUB_IT", [VECINA], network=MARCHE, now=T0 + 2 * DAY)
    assert silence.silent_identities() == frozenset({
        ("METEOHUB_IT", CARPEGNA_CATALOGO),
    })
    assert silence.stats() == {"observed": 2, "networks": 1}


def test_search_and_map_drop_the_silent_station() -> None:
    """Búsqueda y mapa pasan por ``search_near``/``search_catalog``, así que
    filtrar ahí cubre las dos."""
    from server.services import stations

    def encontrada() -> bool:
        rows = stations.search_near(
            43.80088, 12.32015, radius_km=15,
            providers=["METEOHUB_IT"], limit=50,
        )
        return any(row["station_id"] == CARPEGNA_CATALOGO for row in rows)

    assert encontrada()  # sin datos del bulk no se oculta nada
    silence.record_sweep("METEOHUB_IT", [CARPEGNA_BULK, VECINA], network=MARCHE, now=T0)
    assert encontrada()
    silence.record_sweep("METEOHUB_IT", [VECINA], network=MARCHE, now=T0 + 30 * 3600)
    assert not encontrada()
    silence.record_sweep(
        "METEOHUB_IT", [CARPEGNA_BULK, VECINA], network=MARCHE, now=T0 + 31 * 3600,
    )
    assert encontrada()


def test_state_survives_a_restart_through_the_snapshot() -> None:
    """En memoria pura el registro se vaciaba en cada despliegue y la
    ocultación no llegaba a activarse nunca en un servicio que se reinicia a
    diario. Viaja dentro del snapshot del ``RankingStore``."""
    import json

    silence.record_sweep("METEOHUB_IT", [CARPEGNA_BULK, VECINA], network=MARCHE, now=T0)
    silence.record_sweep("METEOHUB_IT", [VECINA], network=MARCHE, now=T0 + 2 * DAY)
    assert silence.is_silent("METEOHUB_IT", CARPEGNA_CATALOGO)

    # Ida y vuelta por JSON, como en el snapshot comprimido.
    exported = json.loads(json.dumps(silence.export_state()))
    silence.clear()
    assert not silence.is_silent("METEOHUB_IT", CARPEGNA_CATALOGO)

    assert silence.import_state(exported) == 2
    assert silence.is_silent("METEOHUB_IT", CARPEGNA_CATALOGO)
    assert not silence.is_silent("METEOHUB_IT", VECINA)


def test_a_broken_snapshot_leaves_an_empty_registry() -> None:
    """Un estado ilegible no puede tumbar el arranque: se vuelve a llenar con
    el primer ciclo del bulk, y mientras tanto no se oculta nada."""
    assert silence.import_state(None) == 0
    assert silence.import_state("basura") == 0
    assert silence.import_state({"seen": [["incompleto"]], "sweeps": {"x": "no"}}) == 0
    assert silence.silent_identities() == frozenset()


def test_only_stations_carrying_a_value_count_as_received() -> None:
    """El bulk publica registros sin ningún valor en algunos proveedores
    (solo MeteoHub, IPMA, GeoSphere e IEM los descartan por su cuenta). Un
    registro hueco daría por viva a una estación apagada."""
    from server.services.ranking import StationDaily, _station_has_data

    viva = StationDaily(
        provider="IPMA", station_id="1200535", name="Lisboa", locality="",
        lat=38.7, lon=-9.1, tcur=21.0,
    )
    hueca = StationDaily(
        provider="IPMA", station_id="1200536", name="Apagada", locality="",
        lat=38.8, lon=-9.2,
    )
    assert _station_has_data(viva)
    assert not _station_has_data(hueca)

    recibidas = [rec.station_id for rec in (viva, hueca) if _station_has_data(rec)]
    silence.record_sweep("IPMA", recibidas, now=T0)
    silence.record_sweep("IPMA", recibidas, now=T0 + 2 * DAY)
    assert not silence.is_silent("IPMA", "1200535")
    # La hueca nunca se registró como recibida, así que sigue sin observarse:
    # no se oculta por ausencia, pero tampoco se da por viva.
    assert not silence.is_silent("IPMA", "1200536")
