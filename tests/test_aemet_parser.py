import math

from services.aemet import _parse_wind_dir_deg


def test_parse_wind_dir_deg_handles_calm_and_sentinels():
    assert math.isnan(_parse_wind_dir_deg("CALMA"))
    assert math.isnan(_parse_wind_dir_deg("VRB"))
    assert math.isnan(_parse_wind_dir_deg("999"))
    assert math.isnan(_parse_wind_dir_deg("990"))
    assert _parse_wind_dir_deg("360") == 0.0


def test_parse_wind_dir_deg_accepts_cardinals():
    assert _parse_wind_dir_deg("N") == 0.0
    assert _parse_wind_dir_deg("SW") == 225.0
    assert _parse_wind_dir_deg("ONO") == 292.5
