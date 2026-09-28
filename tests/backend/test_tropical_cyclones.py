from datetime import datetime, timezone

from server.services.tropical_cyclones import parse_forecast_advisory


AVISO = """
TROPICAL DEPRESSION CENTER LOCATED NEAR 28.9N  44.0W AT 27/2100Z
POSITION ACCURATE WITHIN  20 NM

REPEAT...CENTER LOCATED NEAR 28.9N  44.0W AT 27/2100Z
AT 27/1800Z CENTER WAS LOCATED NEAR 29.1N  43.9W

FORECAST VALID 28/0600Z 28.1N  44.1W
MAX WIND  30 KT...GUSTS  40 KT.

FORECAST VALID 29/0600Z 25.9N  45.5W...POST-TROP/REMNT LOW
FORECAST VALID 30/1800Z 24.5N  49.8W...POST-TROP/EXTRATROP
OUTLOOK VALID 01/1800Z...DISSIPATED
"""


def test_trayectoria_del_aviso_en_orden_y_con_cambio_de_mes():
    track = parse_forecast_advisory(AVISO, datetime(2026, 9, 27, 21, tzinfo=timezone.utc))
    assert [p["time"] for p in track] == [
        "2026-09-27T18:00:00Z", "2026-09-27T21:00:00Z", "2026-09-28T06:00:00Z",
        "2026-09-29T06:00:00Z", "2026-09-30T18:00:00Z",
    ]
    assert track[1]["latitude"] == 28.9 and track[1]["longitude"] == -44.0
    assert [p["stage"] for p in track] == ["tropical", "tropical", "tropical", "remnant", "extratropical"]


def test_dia_de_otro_mes():
    texto = "HURRICANE CENTER LOCATED NEAR 20.0N 110.0W AT 30/1800Z\nFORECAST VALID 02/0600Z 22.0N 112.0W\n"
    track = parse_forecast_advisory(texto, datetime(2026, 9, 30, 18, tzinfo=timezone.utc))
    assert track[-1]["time"] == "2026-10-02T06:00:00Z"


def _punto(clave, lon, lat):
    anillo = [[lon + dx, lat + dy] for dx, dy in ((0, -0.03), (0.03, 0), (0, 0.03), (-0.03, 0))]
    return {"type": "Feature", "properties": {"Class": "Point_Polygon_Point_0", "key": clave},
            "geometry": {"type": "Polygon", "coordinates": [anillo]}}


def test_trayectoria_de_gdacs_con_centro_y_fecha_de_la_clave():
    from server.services.tropical_cyclones import _gdacs_track

    geometria = {"features": [
        _punto("09281800", 133.4, 28.07),
        _punto("09271800", 130.0, 26.27),
        {"properties": {"Class": "Poly_Green"}, "geometry": {"coordinates": [[[0, 0]]]}},
        _punto("01010000", 150.0, 40.0),
    ]}
    track = _gdacs_track(geometria, datetime(2026, 12, 31, 18, tzinfo=timezone.utc))
    assert [p["time"] for p in track] == [
        "2026-09-27T18:00:00Z", "2026-09-28T18:00:00Z", "2027-01-01T00:00:00Z",
    ]
    assert track[0]["latitude"] == 26.27 and track[0]["longitude"] == 130.0


def test_cuencas_del_nhc():
    from server.services.tropical_cyclones import en_cuencas_nhc

    assert en_cuencas_nhc(21.3, -114.0)      # Pacífico oriental
    assert en_cuencas_nhc(29.3, -43.8)       # Atlántico
    assert not en_cuencas_nhc(26.3, 130.0)   # Pacífico occidental
    assert not en_cuencas_nhc(-15.0, 150.0)  # Australia
    assert not en_cuencas_nhc(-20.0, -170.0) # Pacífico sur
