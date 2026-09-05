"""
El identificador de MeteoHub, venga como venga.

Su id es ``red|lat|lon|nombre`` y cada fuente lo escribía a su manera: el
catálogo con las coordenadas a cinco decimales y el nombre en slug, el ranking
con lo que trae el feed. Eran la misma estación escrita de dos formas, así que
pulsarla en el ranking llevaba a un 404 —21 de los 30 enlaces del ranking
italiano estaban rotos—.
"""

from __future__ import annotations

from server.services import stations


CANONICO = "dpcn-piemonte|45.02083|7.93389|buttigliera-d-asti"
COMO_LO_ESCRIBE_EL_RANKING = "dpcn-piemonte|45.02083|7.93389|buttigliera d'asti"


def test_both_spellings_resolve_to_the_same_station() -> None:
    canonico = stations.get_station("METEOHUB_IT", CANONICO)
    del_ranking = stations.get_station("METEOHUB_IT", COMO_LO_ESCRIBE_EL_RANKING)

    assert canonico is not None
    assert del_ranking is not None
    assert del_ranking["station_id"] == canonico["station_id"]


def test_trailing_zeros_in_the_coordinates_do_not_matter() -> None:
    # El catálogo guarda 41.88050; el feed publica 41.8805.
    assert stations.get_station("METEOHUB_IT", "dpcn-puglia|41.8805|16.17583|vieste") is not None


def test_a_station_the_local_catalog_lacks_is_rebuilt_from_its_id() -> None:
    # El feed va por delante del inventario; el id ya trae red, posición y
    # nombre, así que la ficha se puede abrir igual.
    row = stations.get_station("METEOHUB_IT", "mnw|45.89022|11.00644|trn212")
    assert row is not None
    assert row["lat"] == 45.89022
    assert row["provider"] == "METEOHUB_IT"


def test_a_broken_id_is_still_rejected() -> None:
    assert stations.get_station("METEOHUB_IT", "esto-no-es-un-id") is None
