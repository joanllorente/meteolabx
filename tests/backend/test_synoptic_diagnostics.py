import numpy as np
from numpy.testing import assert_allclose
from server.services.synoptic_diagnostics import (
    EARTH_RADIUS as R, GRAVITY as G, OMEGA, RD,
    coordinates, relative_vorticity, divergence, q_vectors,
)

BOUNDS = (-20, 25, 40, 75)
SHAPE = (201, 241)


def test_spherical_solid_body_rotation_and_divergence():
    _, lat = coordinates(SHAPE, BOUNDS)
    rate = 2e-5
    flow = np.broadcast_to(rate * R * np.cos(lat[:, None]), SHAPE).copy()
    zero = np.zeros(SHAPE)
    expected = np.broadcast_to(2 * rate * np.sin(lat[:, None]), SHAPE)
    assert_allclose(relative_vorticity(flow, zero, BOUNDS), expected, rtol=1e-5)
    assert_allclose(divergence(zero, flow, BOUNDS), -expected, rtol=1e-5)


def test_q_is_zero_without_temperature_gradient():
    lon, lat = coordinates(SHAPE, BOUNDS)
    height = 3000 + 300 * np.cos(3 * lon[None, :]) * np.sin(lat[:, None])
    qx, qy, div = q_vectors(np.full(SHAPE, 270.), height, BOUNDS)
    assert_allclose(qx, 0, atol=1e-22)
    assert_allclose(qy, 0, atol=1e-22)
    assert_allclose(div, 0, atol=1e-26)


def test_q_and_its_divergence_match_analytic_geostrophic_shear():
    lon, lat = coordinates(SHAPE, BOUNDS)
    a, b = 1000., 20.
    height = np.broadcast_to(3000 + a * lat[:, None] ** 2, SHAPE)
    temperature = np.broadcast_to(270 + b * lon[None, :], SHAPE)
    qx, qy, div = q_vectors(temperature, height, BOUNDS, sigma=0)

    def analytic_qy(phi):
        # ug = -g A phi / (a Omega sin(phi)); Qy = -Rd/p dug/dy dT/dx.
        derivative = -G * a / (R**2 * OMEGA) * (1/np.sin(phi) - phi*np.cos(phi)/np.sin(phi)**2)
        return -RD / 70000 * derivative * b / (R * np.cos(phi))

    expected = analytic_qy(lat)
    epsilon = 1e-6
    expected_div = ((analytic_qy(lat+epsilon)-analytic_qy(lat-epsilon))/(2*epsilon*R)
                    - expected*np.tan(lat)/R)
    core = np.s_[4:-4, 4:-4]
    assert_allclose(qx[core], 0, atol=1e-23)
    assert_allclose(qy[core], np.broadcast_to(expected[:, None], SHAPE)[core], rtol=2e-4)
    assert_allclose(div[core], np.broadcast_to(expected_div[:, None], SHAPE)[core], rtol=1e-3)


def test_q_preserves_missing_data_and_excludes_equator():
    shape = (41, 61)
    bounds = (-20, -20, 40, 20)
    t = np.full(shape, 270.)
    z = np.full(shape, 3000.)
    t[4, 10] = np.nan
    qx, qy, div = q_vectors(t, z, bounds)
    assert np.isnan(div[4, 10])
    assert np.isnan(qx[20]).all()
    assert np.isnan(qy[20]).all()


def test_frontogenesis_matches_pure_deformation_and_convergence():
    from server.services.synoptic_diagnostics import frontogenesis
    lon, lat = coordinates(SHAPE, BOUNDS)
    # Isentrópicas zonales (θ crece hacia el norte) y un flujo que solo tiene
    # ∂v/∂y = −k: confluencia norte-sur que las aprieta. Con u = 0 y v función
    # de la latitud, la fórmula da F = k|∇θ| exactamente en la esfera.
    k = 1e-5
    y = R * lat[:, None]
    theta = np.broadcast_to(280 + 2e-5 * y, SHAPE)
    v = np.broadcast_to(-k * y, SHAPE)
    u = np.zeros(SHAPE)
    core = np.s_[4:-4, 4:-4]
    f = frontogenesis(theta, u, v, BOUNDS, sigma=0)
    assert_allclose(f[core], k * 2e-5, rtol=1e-6)
    # El flujo contrario las separa: frontólisis del mismo valor.
    assert_allclose(frontogenesis(theta, u, -v, BOUNDS, sigma=0)[core], -k * 2e-5, rtol=1e-6)
    # Sin gradiente de θ no hay frente que intensificar.
    assert_allclose(frontogenesis(np.full(SHAPE, 280.), u, v, BOUNDS, sigma=0), 0, atol=1e-30)


def test_eady_growth_rate_matches_its_definition_and_masks_neutral_layers():
    from server.services.synoptic_diagnostics import eady_growth_rate, EADY_MIN_N2
    _, lat = coordinates(SHAPE, BOUNDS)
    z8, z5 = np.full(SHAPE, 1500.), np.full(SHAPE, 5600.)
    t8, t5 = np.full(SHAPE, 283.), np.full(SHAPE, 258.)
    u8, u5 = np.full(SHAPE, 5.), np.full(SHAPE, 25.)
    cero = np.zeros(SHAPE)
    e = eady_growth_rate(u8, cero, u5, cero, z8, z5, t8, t5, BOUNDS)
    th8, th5 = 283 * (1000/850)**.2857, 258 * (1000/500)**.2857
    n = np.sqrt(G / ((th8 + th5) / 2) * (th5 - th8) / 4100)
    f = 2 * OMEGA * np.sin(lat[:, None])
    assert_allclose(e, np.broadcast_to(0.31 * f * (20 / 4100) / n, SHAPE), rtol=1e-6)
    # Capa casi neutra: sin valor en vez de un índice disparado.
    neutra = eady_growth_rate(u8, cero, u5, cero, z8, z5, t8, np.full(SHAPE, 283. * (500/850)**.2857 + .01), BOUNDS)
    assert np.isnan(neutra).all()
