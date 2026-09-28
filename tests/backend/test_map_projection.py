import numpy as np

from server.services import ecmwf_forecast
from server.services.map_projection import covering_bounds, lcc, rectangle


def test_directa_e_inversa_se_deshacen_en_los_dos_hemisferios():
    for lon0, lat0, lat1, lat2 in ((12, 52, 35, 65), (-62, -35, -20, -50)):
        forward, inverse = lcc(lon0, lat0, lat1, lat2)
        lon = np.array([lon0 - 40, lon0, lon0 + 35])
        lat = np.array([lat0 - 15, lat0, lat0 + 12])
        x, y = forward(lon, lat)
        lon2, lat2_ = inverse(x, y)
        assert np.allclose(lon2, lon, atol=1e-9) and np.allclose(lat2_, lat, atol=1e-9)


def test_conforme_la_escala_es_igual_en_las_dos_direcciones():
    forward, _ = lcc(12, 52, 35, 65)
    h = 1e-4
    for lat in (30.0, 50.0, 70.0):
        x0, y0 = forward(20.0, lat)
        xe, ye = forward(20.0 + h, lat)
        xn, yn = forward(20.0, lat + h)
        este = np.hypot(xe - x0, ye - y0) / (h * np.cos(np.radians(lat)))
        norte = np.hypot(xn - x0, yn - y0) / h
        assert abs(este / norte - 1) < 1e-4


def test_cada_dominio_cabe_en_la_rejilla_y_cubre_su_rectangulo():
    for clave, dominio in ecmwf_forecast.DOMAINS.items():
        oeste, sur, este, norte = dominio["bounds"]
        assert -180.125 <= oeste < este <= 179.875, clave
        # Las esquinas y los puntos medios del rectángulo caen dentro del recuadro.
        p = dominio["projection"]
        _, inverse = lcc(p["lon0"], p["lat0"], p["lat1"], p["lat2"])
        xmin, ymin, xmax, ymax = rectangle(p)
        xs = np.linspace(xmin, xmax, 50)
        for x, y in [(x, ymin) for x in xs] + [(x, ymax) for x in xs]:
            lon, lat = inverse(x, y)
            assert oeste <= lon <= este and sur <= lat <= norte, clave
        # Y los bordes caen en bordes de celda de 0,25°.
        for borde in dominio["bounds"]:
            assert abs((borde + 0.125) / 0.25 - round((borde + 0.125) / 0.25)) < 1e-9


def test_europa_cambiada_a_mano_se_pinta_sin_proyeccion(monkeypatch):
    assert ecmwf_forecast.domain_projection("europe")["lon0"] == 12.0
    monkeypatch.setenv("METEOLABX_ECMWF_DOMAIN", "-10,35,5,45")
    assert ecmwf_forecast.domain_projection("europe") is None
    assert ecmwf_forecast.domain_projection("east-asia")["lon0"] == 120.0
