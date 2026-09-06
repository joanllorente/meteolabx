"""Qué red sobrevive cuando el catálogo del mapa se recorta.

El mapa carga como mucho 60.000 estaciones por país. Estados Unidos tiene
194.848, así que el recorte no es un detalle: decide qué se ve. Con el orden
alfabético anterior, IEM —que va antes que NWS— se llevaba el cupo entero y
en el mapa de Estados Unidos no quedaba ni una estación de las demás redes.
"""
from server.services.stations import catalog_sort_key


def _estacion(provider: str, station_id: str = "x") -> dict:
    return {"provider": provider, "station_id": station_id}


def test_the_aggregator_never_pushes_out_a_real_network():
    catalogo = [
        _estacion("IEM", f"{indice:05d}") for indice in range(5)
    ] + [_estacion("NWS", "LIB11"), _estacion("NETATMO", "a"), _estacion("WINDY", "b")]
    catalogo.sort(key=catalog_sort_key)
    # Con un tope de tres sobrevive una de cada red, no tres del agregador.
    assert [fila["provider"] for fila in catalogo[:3]] == ["NWS", "NETATMO", "WINDY"]


def test_official_networks_come_before_amateurs_and_amateurs_before_the_aggregator():
    catalogo = [_estacion(nombre) for nombre in ("IEM", "WINDY", "NWS", "NETATMO", "AEMET")]
    catalogo.sort(key=catalog_sort_key)
    assert [fila["provider"] for fila in catalogo] == [
        "AEMET", "NWS", "NETATMO", "WINDY", "IEM",
    ]


def test_within_a_network_the_order_is_stable_by_identifier():
    catalogo = [_estacion("NWS", ident) for ident in ("lib11", "AAA01", "zzz99")]
    catalogo.sort(key=catalog_sort_key)
    assert [fila["station_id"] for fila in catalogo] == ["AAA01", "lib11", "zzz99"]


def test_an_unknown_provider_is_treated_as_an_official_network():
    # Una red nueva no puede caer al fondo por no estar en ninguna lista.
    catalogo = [_estacion("IEM"), _estacion("ZZZNUEVA"), _estacion("NETATMO")]
    catalogo.sort(key=catalog_sort_key)
    assert [fila["provider"] for fila in catalogo] == ["ZZZNUEVA", "NETATMO", "IEM"]


def test_iem_is_dropped_where_the_country_has_its_own_bulk():
    """En España IEM era casi todo duplicado de AEMET, con otro identificador.

    Mientras no exista la deduplicación, en los países con proveedor propio y
    bulk se enseña solo la red del país.
    """
    from server.services.stations import drop_redundant_iem

    catalogo = [
        {"provider": "AEMET", "country": "ES", "station_id": "0076"},
        {"provider": "IEM", "country": "ES", "station_id": "ES_ASOS|LEBL"},
        {"provider": "NETATMO", "country": "ES", "station_id": "n1"},
    ]
    assert [f["provider"] for f in drop_redundant_iem(catalogo)] == ["AEMET", "NETATMO"]


def test_having_bulk_or_not_does_not_change_the_catalogue():
    """Que la red propia tenga bulk decide de dónde salen los datos del
    ranking, no qué estaciones se enseñan. NWS y el Met Office no lo tienen y
    aun así IEM no aparece en su mapa ni en su búsqueda."""
    from server.services.stations import drop_redundant_iem

    catalogo = [
        {"provider": "IEM", "country": "US", "station_id": "IA_ASOS|DSM"},
        {"provider": "IEM", "country": "GB", "station_id": "GB_ASOS|EGLL"},
        {"provider": "NWS", "country": "US", "station_id": "LIB11"},
    ]
    assert [f["provider"] for f in drop_redundant_iem(catalogo)] == ["NWS"]


def test_iem_stays_where_no_network_covers_the_country():
    """Alemania no tiene proveedor propio: sin IEM no quedaría casi nada."""
    from server.services.stations import drop_redundant_iem

    catalogo = [
        {"provider": "IEM", "country": "DE", "station_id": "DE_ASOS|EDDF"},
        {"provider": "NETATMO", "country": "DE", "station_id": "n1"},
    ]
    assert len(drop_redundant_iem(catalogo)) == 2


def test_asking_for_iem_by_name_returns_iem():
    """El filtro esconde duplicados de las vistas generales; no bloquea una
    consulta dirigida, que es como se revisan las correcciones de país."""
    from server.services.stations import drop_redundant_iem

    catalogo = [{"provider": "IEM", "country": "ES", "station_id": "ES__ASOS|LEBL"}]
    assert drop_redundant_iem(catalogo, requested_providers=["IEM"]) == catalogo
    assert drop_redundant_iem(catalogo, requested_providers=["AEMET"]) == []


def test_antarctica_keeps_both_networks():
    """CLIMANTARTIDE solo cubre las bases italianas —11 de 51—: quitar IEM
    dejaría la Antártida casi vacía. Allí el solapamiento se resuelve estación
    por estación."""
    from server.services.stations import drop_redundant_iem

    catalogo = [
        {"provider": "IEM", "country": "AQ", "station_id": "WMO_BUFR_SRF|0-380-0-1"},
        {"provider": "CLIMANTARTIDE", "country": "AQ", "station_id": "Zoraida"},
    ]
    assert len(drop_redundant_iem(catalogo)) == 2


def test_a_border_search_judges_each_station_by_its_own_country():
    """Cerca de la frontera la búsqueda devuelve estaciones de varios países:
    la francesa se descarta y la andorrana no."""
    from server.services.stations import drop_redundant_iem

    catalogo = [
        {"provider": "IEM", "country": "FR", "station_id": "FR_ASOS|LFBT"},
        {"provider": "IEM", "country": "AD", "station_id": "AD_ASOS|LEXX"},
    ]
    assert [f["country"] for f in drop_redundant_iem(catalogo)] == ["AD"]
