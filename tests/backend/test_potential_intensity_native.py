import numpy as np
import pytest

native = pytest.importorskip('server.services._dcape_native')

# Sondeo del ejemplo de la documentación de tcpyPI: presión (hPa), T (°C), r (g/kg).
SONDEO = np.array([
    [1000, 28, 18], [975, 25, 18], [950, 24, 16], [925, 23, 13], [900, 22, 12],
    [875, 20, 11], [850, 19, 10], [825, 18, 10], [800, 16, 9], [775, 15, 8],
    [750, 13, 7], [700, 11, 4], [650, 8, 3], [600, 5, 1.7], [550, 2, 1.2],
    [500, -2, 1.7], [450, -6, .7], [400, -11, .2], [350, -18, .15], [300, -27, .1],
    [250, -37, .11], [225, -43, .08], [200, -49, .05], [175, -57, .03], [150, -65, .014],
    [125, -73, .005], [100, -79, .003], [70, -73, .002], [50, -64, .002],
], dtype=float)


def _pi(sst, msl=1010., surface=np.nan, sondeo=SONDEO):
    columna = lambda a: np.asarray(a, dtype=float)[:, None, None]
    vmax, pmin = native.potential_intensity(
        [[sst]], [[msl]], [[surface]], sondeo[:, 0], columna(sondeo[:, 1]), columna(sondeo[:, 2]))
    return vmax[0, 0], pmin[0, 0]


def test_reproduces_the_tcpypi_reference_sounding():
    vmax, pmin = _pi(30.)
    assert vmax == pytest.approx(82.4845, abs=1e-4)
    assert pmin == pytest.approx(900.2039, abs=1e-4)


def test_cold_or_missing_sea_gives_no_potential_intensity():
    assert all(np.isnan(_pi(sst)).all() for sst in (np.nan, 5., 300.))


def test_levels_below_the_surface_are_dropped():
    """Con la superficie a 960 hPa cuenta como si el sondeo empezara en 950."""
    recortado = _pi(30., sondeo=SONDEO[2:])
    assert _pi(30., surface=960.) == pytest.approx(recortado, abs=0)


def test_a_gap_in_the_temperature_profile_gives_nan():
    sondeo = SONDEO.copy()
    sondeo[10, 1] = np.nan
    assert np.isnan(_pi(30., sondeo=sondeo)).all()


def test_rejects_increasing_pressure():
    with pytest.raises(ValueError):
        _pi(30., sondeo=SONDEO[::-1])
