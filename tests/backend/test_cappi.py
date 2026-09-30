"""CAPPI: la reflectividad cortada a altitud constante.

Se comprueba con columnas de juguete, sin red: la interpolación entre los dos
niveles que encierran la altitud, el corte bajo tierra, la conversión del
paquete IP4 y el reconocimiento de su parámetro local.
"""

import numpy as np
import pytest

from server.services.arome_packages import IP4_REFLECTIVITY, _band_element
from server.services.cappi import (
    CappiAccumulator,
    reflectivity_from_dbz,
    reflectivity_from_rain_rate,
)


def columna(accumulator, niveles):
    for presion, altura, dbz in niveles:
        accumulator.add(presion, np.array([altura], float), reflectivity_from_dbz(np.array([dbz], float)))
    return accumulator.dbz()[0]


def test_interpola_en_z_lineal_entre_los_niveles_que_encierran_la_altitud():
    # 1.500 m a medio camino entre 900 hPa (1.000 m) y 850 (2.000 m): la media
    # de 10 y 1.000 mm⁶/m³ es 505, unos 27 dBZ; en dBZ habría salido 20.
    valor = columna(
        CappiAccumulator(1500.0, np.array([1013.0])),
        [(925.0, 700.0, 0.0), (900.0, 1000.0, 10.0), (850.0, 2000.0, 30.0), (800.0, 2600.0, 0.0)],
    )
    assert valor == pytest.approx(10 * np.log10(505.0), abs=1e-6)


def test_bajo_tierra_no_hay_corte():
    # Terreno a unos 2.000 m: superficie a 800 hPa, por debajo de la altitud.
    valor = columna(
        CappiAccumulator(1500.0, np.array([800.0])),
        [(925.0, 700.0, 40.0), (900.0, 1000.0, 40.0), (850.0, 2000.0, 40.0), (800.0, 2600.0, 40.0)],
    )
    assert np.isnan(valor)


def test_con_el_nivel_de_abajo_bajo_tierra_se_toma_el_de_arriba():
    # Superficie a 880 hPa: 900 queda bajo tierra, 1.500 m (≈ 870 hPa) no.
    valor = columna(
        CappiAccumulator(1500.0, np.array([880.0])),
        [(925.0, 700.0, 50.0), (900.0, 1000.0, 50.0), (850.0, 2000.0, 25.0), (800.0, 2600.0, 0.0)],
    )
    assert valor == pytest.approx(25.0, abs=1e-6)


def test_sin_eco_es_cero_dbz():
    valor = columna(
        CappiAccumulator(1500.0, np.array([1013.0])),
        [(925.0, 700.0, -10.0), (900.0, 1000.0, -5.0), (850.0, 2000.0, -20.0)],
    )
    assert valor == 0.0


def test_la_lluvia_equivalente_de_ip4_vuelve_a_dbz_con_marshall_palmer():
    # R = 2,73 mm/h son 30 dBZ con Z = 200·R^1,6; cero y negativos, sin eco.
    z = reflectivity_from_rain_rate(np.array([(1000 / 200) ** (1 / 1.6), 0.0, -0.01, np.nan]))
    assert 10 * np.log10(z[0]) == pytest.approx(30.0, abs=1e-9)
    assert z[1] == 0.0 and z[2] == 0.0
    assert np.isnan(z[3])


def test_el_parametro_local_de_ip4_se_reconoce_por_su_plantilla():
    # Así lo etiqueta GDAL: sin nombre, con la categoría 16 y el número 192.
    tags = {
        "GRIB_ELEMENT": "unknown",
        "GRIB_DISCIPLINE": "0(Meteorological)",
        "GRIB_PDS_TEMPLATE_NUMBERS": "16 192 2 255 204 0 0 0 1 0 0 0 7 100",
    }
    assert _band_element(tags) == IP4_REFLECTIVITY
    assert _band_element({**tags, "GRIB_ELEMENT": "TKE"}) == "TKE"
    assert _band_element({**tags, "GRIB_PDS_TEMPLATE_NUMBERS": "16 193"}) == "unknown"
