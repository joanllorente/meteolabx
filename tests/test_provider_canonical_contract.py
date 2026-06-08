import math
from datetime import datetime, timedelta, timezone

import pytest

from api import weather_underground as wu
from services import aemet
from services import euskalmet
from services import frost
from services import meteocat
from services import meteofrance
from services import meteogalicia
from services import meteohub
from services import metoffice
from services import nws
from services import poem
from services import weatherlink


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _iso_from_epoch(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _provider_state(provider_id: str, station_id: str) -> dict:
    provider = provider_id.upper()
    prefix = provider.lower()
    return {
        "connected": True,
        "connection_type": provider,
        "provider_station_id": station_id,
        f"{prefix}_station_id": station_id,
    }


def _assert_finite(data: dict, *keys: str) -> None:
    for key in keys:
        value = data.get(key)
        assert value is not None, key
        assert not math.isnan(float(value)), key


def _assert_series_has(data: dict, *keys: str) -> None:
    series = data.get("_series")
    assert isinstance(series, dict)
    assert series.get("has_data") is True
    assert len(series.get("epochs", [])) >= 2
    for key in keys:
        values = series.get(key)
        assert isinstance(values, list), key
        assert len(values) == len(series["epochs"]), key


class _FakeResponse:
    status_code = 200
    reason = "OK"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def test_wu_current_maps_all_current_observation_variables(monkeypatch):
    epoch = _now_epoch()
    payload = {
        "observations": [
            {
                "epoch": epoch,
                "obsTimeLocal": "2026-05-18 16:00:00",
                "obsTimeUtc": _iso_from_epoch(epoch),
                "lat": 41.4,
                "lon": 2.1,
                "elev": 95,
                "humidity": 62,
                "winddir": 225,
                "solarRadiation": 720,
                "uv": 6.4,
                "metric": {
                    "temp": 22.5,
                    "pressure": 1014.2,
                    "dewpt": 14.8,
                    "heatIndex": 23.1,
                    "windChill": 22.5,
                    "windSpeed": 18.0,
                    "windGust": 31.0,
                    "precipRate": 1.2,
                    "precipTotal": 2.6,
                },
            }
        ]
    }
    monkeypatch.setattr(wu.requests, "get", lambda *args, **kwargs: _FakeResponse(payload))

    data = wu.fetch_wu_current("IWUTEST", "secret")

    assert data["Tc"] == 22.5
    assert data["RH"] == 62
    assert data["p_hpa"] == 1014.2
    assert data["wind_dir_deg"] == 225
    assert data["wind"] == 18.0
    assert data["gust"] == 31.0
    assert data["precip_total"] == wu.quantize_rain_mm_wu(2.6)
    assert data["solar_radiation"] == 720
    assert data["uv"] == 6.4
    _assert_finite(data, "epoch", "lat", "lon", "elevation")


def test_aemet_uses_wind_direction_field_not_wind_stddev(monkeypatch):
    epoch = _now_epoch()
    raw = {
        "idema": "3195",
        "fint": _iso_from_epoch(epoch),
        "ta": "21.2",
        "tamax": "24.0",
        "tamin": "12.5",
        "hr": "57",
        "pres": "948.4",
        "pres_nmar": "1012.6",
        "vv": "5.0",
        "vmax": "10.0",
        "dv": "225",
        "stdvv": "999",
        "prec": "0.8",
        "alt": "650",
        "lat": "41.7",
        "lon": "-2.5",
    }
    monkeypatch.setattr(aemet, "fetch_aemet_station_data", lambda station_id: raw)
    monkeypatch.setattr(aemet, "_aemet_data_age_minutes", lambda epoch: 0.0)

    data = aemet.get_aemet_data(_provider_state("AEMET", "3195"))

    assert data["Tc"] == 21.2
    assert data["RH"] == 57
    assert data["p_hpa"] == 1012.6
    assert data["p_station"] == 948.4
    assert data["wind"] == pytest.approx(18.0)
    assert data["gust"] == pytest.approx(36.0)
    assert data["wind_dir_deg"] == 225.0
    assert data["wind_dir_deg"] != 999.0
    assert data["precip_total"] == 0.8
    _assert_finite(data, "temp_max", "temp_min", "epoch", "lat", "lon", "elevation")


def test_meteocat_uses_interval_precipitation_and_full_series(monkeypatch):
    base = _now_epoch() - 600
    var_map = {
        meteocat.V_TEMP: [(base, 18.0), (base + 600, 22.0)],
        meteocat.V_TEMP_MAX_AIR: [(base, 18.5), (base + 600, 22.6)],
        meteocat.V_TEMP_MIN_AIR: [(base, 17.4), (base + 600, 21.7)],
        meteocat.V_RH: [(base, 70.0), (base + 600, 55.0)],
        meteocat.V_RH_MAX_DAY: [(base, 75.0), (base + 600, 77.0)],
        meteocat.V_RH_MIN_DAY: [(base, 68.0), (base + 600, 48.0)],
        meteocat.V_PRESSURE: [(base, 900.0), (base + 600, 901.0)],
        meteocat.V_WIND: [(base, 4.0), (base + 600, 5.0)],
        meteocat.V_GUST: [(base, 8.0), (base + 600, 10.0)],
        meteocat.V_WIND_DIR: [(base, 200.0), (base + 600, 210.0)],
        meteocat.V_SOLAR: [(base, 500.0), (base + 600, 720.0)],
        meteocat.V_UV: [(base, 4.0), (base + 600, 6.0)],
        meteocat.V_PRECIP: [(base, 0.2), (base + 600, 0.3)],
        meteocat.V_PRECIP_ACC: [(base, 19625.0), (base + 600, 19625.3)],
        meteocat.V_RAIN_1MIN_MAX: [(base, 0.1), (base + 600, 0.2)],
    }
    monkeypatch.setattr(
        meteocat,
        "fetch_meteocat_station_snapshot",
        lambda station_code, api_key=None: {
            "ok": True,
            "station_code": station_code,
            "latest_epoch": base + 600,
            "latest_iso": _iso_from_epoch(base + 600),
            "values": {
                "temp": 22.0,
                "rh": 55.0,
                "pressure_abs": 901.0,
                "wind": 5.0,
                "gust": 9.0,
                "wind_dir": 210.0,
                "gust_dir": 215.0,
                "solar": 720.0,
                "uv": 6.0,
            },
        },
    )
    monkeypatch.setattr(
        meteocat,
        "fetch_meteocat_local_day_window",
        lambda *args, **kwargs: {
            "has_data": True,
            "variables": var_map,
            "series": meteocat.extract_meteocat_daily_timeseries(var_map),
        },
    )
    monkeypatch.setattr(
        meteocat,
        "_find_station",
        lambda station_code: {
            "altitud": 2228,
            "coordenades": {"latitud": 42.7, "longitud": 0.7},
        },
    )

    data = meteocat.get_meteocat_data(state=_provider_state("METEOCAT", "Z6"))

    assert data["Tc"] == 22.0
    assert data["wind"] == pytest.approx(18.0)
    assert data["gust"] == pytest.approx(32.4)
    assert data["wind_dir_deg"] == 210.0
    assert data["precip_total"] == pytest.approx(0.5)
    assert data["precip_total"] != pytest.approx(19625.3)
    assert data["rain_1min_mm"] == 0.2
    assert data["solar_radiation"] == 720.0
    assert data["uv"] == 6.0
    assert data["temp_max"] == 22.6
    assert data["temp_min"] == 17.4
    assert data["rh_max"] == 77.0
    assert data["rh_min"] == 48.0
    assert data["gust_max"] == pytest.approx(36.0)
    _assert_series_has(
        data,
        "temps",
        "humidities",
        "pressures_abs",
        "winds",
        "gusts",
        "wind_dirs",
        "precips",
        "solar_radiations",
    )


def test_meteohub_contract_from_synthetic_observations(monkeypatch):
    base = _now_epoch() - 900
    payload = {
        "ok": True,
        "data": [
            {
                "stat": {
                    "lat": 42.4575,
                    "lon": 12.99972,
                    "net": "dpcn-lazio",
                    "details": [
                        {"var": "B01019", "val": "Monte Terminillo"},
                        {"var": "B05001", "val": 42.4575},
                        {"var": "B06001", "val": 12.99972},
                        {"var": "B07030", "val": 1875.0},
                    ],
                },
                "prod": [
                    {
                        "var": meteohub.P_TEMP,
                        "lev": "103,2000,0,0",
                        "trange": "254,0,0",
                        "val": [
                            {"ref": _iso_from_epoch(base), "val": 288.15},
                            {"ref": _iso_from_epoch(base + 900), "val": 289.15},
                        ],
                    },
                    {
                        "var": meteohub.P_RH,
                        "lev": "103,2000,0,0",
                        "trange": "254,0,0",
                        "val": [
                            {"ref": _iso_from_epoch(base), "val": 70.0},
                            {"ref": _iso_from_epoch(base + 900), "val": 65.0},
                        ],
                    },
                    {
                        "var": meteohub.P_PRESSURE,
                        "lev": "1,0,0,0",
                        "trange": "254,0,0",
                        "val": [
                            {"ref": _iso_from_epoch(base), "val": 81000.0},
                            {"ref": _iso_from_epoch(base + 900), "val": 81100.0},
                        ],
                    },
                    {
                        "var": meteohub.P_WIND_SPEED,
                        "lev": "103,10000,0,0",
                        "trange": "254,0,0",
                        "val": [
                            {"ref": _iso_from_epoch(base), "val": 2.0},
                            {"ref": _iso_from_epoch(base + 900), "val": 3.0},
                        ],
                    },
                    {
                        "var": meteohub.P_WIND_DIR,
                        "lev": "103,10000,0,0",
                        "trange": "254,0,0",
                        "val": [
                            {"ref": _iso_from_epoch(base), "val": 260.0},
                            {"ref": _iso_from_epoch(base + 900), "val": 270.0},
                        ],
                    },
                    {
                        "var": meteohub.P_PRECIP,
                        "lev": "1,0,0,0",
                        "trange": "1,0,3600",
                        "val": [
                            {"ref": _iso_from_epoch(base), "val": 0.2},
                            {"ref": _iso_from_epoch(base + 900), "val": 0.3},
                        ],
                    },
                ],
            }
        ],
    }
    monkeypatch.setattr(
        meteohub,
        "_load_stations",
        lambda: [
            {
                "id": "dpcn-lazio|42.45750|12.99972|monte-terminillo",
                "network": "dpcn-lazio",
                "lat": 42.4575,
                "lon": 12.99972,
                "name": "Monte Terminillo",
            }
        ],
    )
    monkeypatch.setattr(
        meteohub,
        "fetch_meteohub_observations",
        lambda network, lat, lon, days_back=0: payload,
    )

    data = meteohub.get_meteohub_data(
        _provider_state("METEOHUB_IT", "dpcn-lazio|42.45750|12.99972|monte-terminillo")
    )

    assert data["station_name"] == "Monte Terminillo"
    assert data["elevation"] == 1875.0
    assert data["Tc"] == pytest.approx(16.0)
    assert data["RH"] == 65.0
    assert data["p_abs_hpa"] == 811.0
    assert data["wind"] == pytest.approx(10.8)
    assert data["wind_dir_deg"] == 270.0
    assert data["precip_total"] == pytest.approx(0.5)
    _assert_series_has(data, "temps", "humidities", "pressures_abs", "winds", "wind_dirs", "precips")


def test_meteohub_observations_query_uses_explicit_utc_times():
    query = meteohub._build_observations_query(
        datetime(2026, 5, 27, 22, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 28, 20, 15, tzinfo=timezone.utc),
    )

    assert "reftime: >=2026-05-27 22:00,<=2026-05-28 20:15;" in query


def test_euskalmet_contract_from_synthetic_complete_series(monkeypatch):
    base = _now_epoch() - 1200
    series = {
        "ok": True,
        "epochs": [base, base + 600, base + 1200],
        "temps": [18.0, 20.0, 21.0],
        "humidities": [70.0, 62.0, 58.0],
        "pressures_abs": [940.0, 941.0, 942.0],
        "pressures_msl": [1010.0, 1011.0, 1012.0],
        "winds": [10.8, 14.4, 18.0],
        "gusts": [18.0, 28.8, 32.4],
        "wind_dirs": [190.0, 205.0, 220.0],
        "precips": [0.1, 0.2, 0.3],
        "solar_radiations": [400.0, 650.0, 780.0],
        "has_data": True,
    }
    monkeypatch.setattr(euskalmet, "_resolve_jwt", lambda jwt=None: "jwt")
    monkeypatch.setattr(euskalmet, "_resolve_api_key", lambda api_key=None: "api")
    monkeypatch.setattr(euskalmet, "fetch_euskalmet_day_series", lambda *args, **kwargs: series)
    monkeypatch.setattr(
        euskalmet,
        "_find_station",
        lambda station_id: {"lat": 43.2, "lon": -2.9, "altitude_m": 120},
    )

    data = euskalmet.get_euskalmet_data(state=_provider_state("EUSKALMET", "C009"))

    assert data["Tc"] == 21.0
    assert data["RH"] == 58.0
    assert data["p_abs_hpa"] == 942.0
    assert data["p_hpa"] == 1012.0
    assert data["wind"] == 18.0
    assert data["gust"] == 32.4
    assert data["wind_dir_deg"] == 220.0
    assert data["precip_total"] == pytest.approx(0.6)
    assert data["solar_radiation"] == 780.0
    assert data["temp_max"] == 21.0
    assert data["rh_min"] == 58.0
    _assert_series_has(
        data,
        "temps",
        "humidities",
        "pressures_abs",
        "winds",
        "gusts",
        "wind_dirs",
        "precips",
        "solar_radiations",
    )


def test_frost_contract_from_synthetic_complete_series(monkeypatch):
    base = _now_epoch() - 1200
    selected = {
        "temp_c": {"epoch": base + 1200, "obs": {"value": 21.0}},
        "rh": {"epoch": base + 1200, "obs": {"value": 58.0}},
        "p_abs_hpa": {"epoch": base + 1200, "obs": {"value": 942.0}},
        "wind_ms": {"epoch": base + 1200, "obs": {"value": 5.0}},
        "gust_ms": {"epoch": base + 1200, "obs": {"value": 9.0}},
        "wind_dir_deg": {"epoch": base + 1200, "obs": {"value": 220.0}},
    }
    today_series = {
        "epochs": [base, base + 600, base + 1200],
        "temps": [18.0, 20.0, 21.0],
        "humidities": [70.0, 62.0, 58.0],
        "pressures_abs": [940.0, 941.0, 942.0],
        "winds": [10.8, 14.4, 18.0],
        "gusts": [18.0, 28.8, 32.4],
        "wind_dirs": [190.0, 205.0, 220.0],
        "precip_accum_mm": [0.0, 0.2, 0.5],
        "precip_step_mm": [0.1, 0.2, 0.3],
        "has_data": True,
    }
    monkeypatch.setattr(
        frost,
        "_available_elements",
        lambda *args, **kwargs: tuple(kwargs.get("requested_elements") or ()),
    )
    monkeypatch.setattr(frost, "fetch_frost_latest", lambda *args, **kwargs: {"data": []})
    monkeypatch.setattr(frost, "fetch_frost_today_series", lambda *args, **kwargs: {"today": True})
    monkeypatch.setattr(frost, "fetch_frost_recent_series", lambda *args, **kwargs: {"recent": True})
    monkeypatch.setattr(frost, "_latest_selected_rows", lambda payload: selected)
    monkeypatch.setattr(
        frost,
        "_bin_series",
        lambda payload, *, bin_seconds: today_series if bin_seconds == 600 else today_series,
    )
    monkeypatch.setattr(
        frost,
        "_find_station",
        lambda station_id: {"name": "Oslo", "lat": 59.9, "lon": 10.7, "elev": 100},
    )

    data = frost.get_frost_data(_provider_state("FROST", "SN18700"))

    assert data["Tc"] == 21.0
    assert data["RH"] == 58.0
    assert data["p_abs_hpa"] == 942.0
    assert data["wind"] == pytest.approx(18.0)
    assert data["gust"] == pytest.approx(32.4)
    assert data["wind_dir_deg"] == 220.0
    assert data["precip_total"] == pytest.approx(0.5)
    assert data["temp_max"] == 21.0
    assert data["rh_min"] == 58.0
    _assert_series_has(
        data,
        "temps",
        "humidities",
        "pressures_abs",
        "winds",
        "gusts",
        "wind_dirs",
        "precip_accum_mm",
        "precip_step_mm",
    )


def test_meteofrance_contract_from_synthetic_raw_rows(monkeypatch):
    base = _now_epoch() - 1200

    def row(offset_s, temp_c, rh, wind_ms, gust_ms, wind_dir, precip):
        epoch = base + offset_s
        return {
            "validity_time": _iso_from_epoch(epoch),
            "lat": 48.88,
            "lon": 2.35,
            "t": temp_c + 273.15,
            "td": temp_c - 4.0 + 273.15,
            "tx": temp_c + 1.0 + 273.15,
            "tn": temp_c - 1.0 + 273.15,
            "u": rh,
            "ux": rh + 5,
            "un": rh - 5,
            "pres": 94200.0,
            "pmer": 101200.0,
            "ff": wind_ms,
            "fxi10": gust_ms,
            "dd": wind_dir,
            "rr_per": precip,
        }

    hourly = [
        row(0, 18.0, 70.0, 3.0, 6.0, 190.0, 0.1),
        row(600, 20.0, 62.0, 4.0, 8.0, 205.0, 0.2),
        row(1200, 21.0, 58.0, 5.0, 9.0, 220.0, 0.3),
    ]
    monkeypatch.setattr(meteofrance, "METEOFRANCE_API_KEY", "api")
    monkeypatch.setattr(meteofrance, "fetch_meteofrance_latest_6m", lambda *args, **kwargs: [hourly[-1]])
    monkeypatch.setattr(meteofrance, "fetch_meteofrance_hourly_series_today", lambda *args, **kwargs: hourly)
    monkeypatch.setattr(
        meteofrance,
        "_find_station",
        lambda station_id: {"name": "Paris", "lat": 48.88, "lon": 2.35, "elev": 90, "pack": "test"},
    )

    data = meteofrance.get_meteofrance_data(_provider_state("METEOFRANCE", "75110001"))

    assert data["Tc"] == pytest.approx(21.0)
    assert data["RH"] == 58.0
    assert data["p_abs_hpa"] == 942.0
    assert data["p_hpa"] == 1012.0
    assert data["wind"] == pytest.approx(18.0)
    assert data["gust"] == pytest.approx(32.4)
    assert data["wind_dir_deg"] == 220.0
    assert data["precip_total"] == pytest.approx(0.6)
    assert data["temp_max"] == pytest.approx(22.0)
    assert data["rh_min"] == 53.0
    _assert_series_has(
        data,
        "temps",
        "humidities",
        "pressures_abs",
        "winds",
        "gusts",
        "wind_dirs",
        "precips",
        "solar_radiations",
    )


def test_meteogalicia_contract_from_synthetic_payloads(monkeypatch):
    base = _now_epoch() - 1200
    hourly_series = {
        "epochs": [base, base + 600, base + 1200],
        "temps": [18.0, 20.0, 21.0],
        "humidities": [70.0, 62.0, 58.0],
        "pressures": [940.0, 941.0, 942.0],
        "winds": [10.8, 14.4, 18.0],
        "gusts": [18.0, 28.8, 32.4],
        "wind_dirs": [190.0, 205.0, 220.0],
        "precips": [0.1, 0.2, 0.3],
        "solar_radiations": [400.0, 650.0, 780.0],
        "has_data": True,
    }
    current_item = {
        "estacion": "Coruña",
        "_epoch": base + 1200,
        "_measures": {
            "temp": (21.0, "current"),
            "rh": (58.0, "current"),
            "pressure": (942.0, "current"),
            "wind": (18.0, "current"),
            "gust": (32.4, "current"),
            "wind_dir": (220.0, "current"),
            "precip": (0.3, "current"),
            "solar": (780.0, "current"),
        },
    }
    monkeypatch.setattr(
        meteogalicia,
        "fetch_meteogalicia_hourly",
        lambda station_id, num_hours=24: {"ok": True, "series": hourly_series},
    )
    monkeypatch.setattr(
        meteogalicia,
        "fetch_meteogalicia_current",
        lambda station_id: {"ok": True, "item": current_item},
    )
    monkeypatch.setattr(
        meteogalicia,
        "_find_station",
        lambda station_id: {"estacion": "Coruña", "lat": 43.38, "lon": -8.4, "altitude": 80},
    )

    data = meteogalicia.get_meteogalicia_data(_provider_state("METEOGALICIA", "10157"))

    assert data["Tc"] == 21.0
    assert data["RH"] == 58.0
    assert data["p_abs_hpa"] == 942.0
    assert data["wind"] == 18.0
    assert data["gust"] == 32.4
    assert data["wind_dir_deg"] == 220.0
    assert data["precip_total"] == pytest.approx(0.6)
    assert data["solar_radiation"] == 780.0
    assert data["temp_max"] == 21.0
    assert data["rh_min"] == 58.0
    _assert_series_has(
        data,
        "temps",
        "humidities",
        "pressures_abs",
        "winds",
        "gusts",
        "wind_dirs",
        "precips",
        "solar_radiations",
    )


def test_nws_contract_from_synthetic_features(monkeypatch):
    base = _now_epoch() - 1200
    series = {
        "epochs": [base, base + 600, base + 1200],
        "temps": [18.0, 20.0, 21.0],
        "humidities": [70.0, 62.0, 58.0],
        "pressures_abs": [940.0, 941.0, 942.0],
        "pressures_msl": [1010.0, 1011.0, 1012.0],
        "winds": [10.8, 14.4, 18.0],
        "gusts": [18.0, 28.8, 32.4],
        "wind_dirs": [190.0, 205.0, 220.0],
        "precips": [0.1, 0.2, 0.3],
        "lats": [47.4, 47.4, 47.4],
        "lons": [-122.3, -122.3, -122.3],
        "has_data": True,
    }
    parsed_rows = [
        {
            "epoch": base,
            "lat": 47.4,
            "lon": -122.3,
            "temp_c": 18.0,
            "rh": 70.0,
            "dewpoint_c": 12.0,
            "p_abs_hpa": 940.0,
            "p_msl_hpa": 1010.0,
            "wind_kmh": 10.8,
            "gust_kmh": 18.0,
            "wind_dir_deg": 190.0,
            "precip_last_mm": 0.1,
            "heat_index_c": 18.0,
            "wind_chill_c": 18.0,
        },
        {
            "epoch": base + 1200,
            "lat": 47.4,
            "lon": -122.3,
            "temp_c": 21.0,
            "rh": 58.0,
            "dewpoint_c": 13.0,
            "p_abs_hpa": 942.0,
            "p_msl_hpa": 1012.0,
            "wind_kmh": 18.0,
            "gust_kmh": 32.4,
            "wind_dir_deg": 220.0,
            "precip_last_mm": 0.3,
            "heat_index_c": 21.0,
            "wind_chill_c": 21.0,
        },
    ]
    monkeypatch.setattr(nws, "fetch_nws_observations", lambda *args, **kwargs: {"features": [{}, {}]})
    monkeypatch.setattr(nws, "_series_from_features", lambda *args, **kwargs: series)
    monkeypatch.setattr(nws, "_parse_observation_feature", lambda feature, elevation_m=0.0: parsed_rows.pop(0))
    monkeypatch.setattr(
        nws,
        "_find_station",
        lambda station_id: {"name": "Seattle", "lat": 47.4, "lon": -122.3, "elev": 130, "tz": "America/Los_Angeles"},
    )

    data = nws.get_nws_data(_provider_state("NWS", "KSEA"))

    assert data["Tc"] == 21.0
    assert data["RH"] == 58.0
    assert data["p_abs_hpa"] == 942.0
    assert data["p_hpa"] == 1012.0
    assert data["wind"] == 18.0
    assert data["gust"] == 32.4
    assert data["wind_dir_deg"] == 220.0
    assert data["precip_total"] == pytest.approx(0.6)
    assert data["temp_max"] == 21.0
    assert data["rh_min"] == 58.0
    _assert_series_has(
        data,
        "temps",
        "humidities",
        "pressures_abs",
        "winds",
        "gusts",
        "wind_dirs",
        "solar_radiations",
    )


def test_metoffice_contract_from_synthetic_land_observations(monkeypatch):
    base = _now_epoch() - 2400

    def obs(offset_s, temp_c, rh, mslp, wind_ms, gust_ms, wind_dir):
        epoch = base + offset_s
        return {
            "datetime": _iso_from_epoch(epoch),
            "temperature": temp_c,
            "humidity": rh,
            "mslp": mslp,
            "wind_speed": wind_ms,
            "wind_gust": gust_ms,
            "wind_direction": wind_dir,
            "visibility": 18000,
            "weather_code": 1,
            "pressure_tendency": "F",
        }

    rows = [
        obs(0, 18.0, 70.0, 1010.0, 3.0, 5.0, "S"),
        obs(1200, 20.0, 62.0, 1011.0, 4.0, 8.0, "SW"),
        obs(2400, 21.0, 58.0, 1012.0, 5.0, 9.0, "W"),
    ]
    monkeypatch.setattr(metoffice, "METOFFICE_API_KEY", "api")
    monkeypatch.setattr(
        metoffice,
        "fetch_metoffice_observations",
        lambda station_id, api_key="": {"ok": True, "observations": rows},
    )
    monkeypatch.setattr(
        metoffice,
        "_find_station",
        lambda station_id: {
            "geohash": station_id,
            "display_name": "Bealach Na Ba No 2",
            "name": "Devon",
            "lat": 50.7376,
            "lon": -3.4002,
            "elev": 100,
            "tz": "Europe/London",
        },
    )

    data = metoffice.get_metoffice_data(_provider_state("METOFFICE", "gcj8ds"))

    assert data["Tc"] == 21.0
    assert data["RH"] == 58.0
    assert data["p_hpa"] == 1012.0
    assert data["p_abs_hpa"] == pytest.approx(1012.0 / math.exp(100 / 8000.0))
    assert data["wind"] == pytest.approx(18.0)
    assert data["gust"] == pytest.approx(32.4)
    assert data["wind_dir_deg"] == 270.0
    assert data["station_name"] == "Bealach Na Ba No 2"
    assert math.isnan(float(data["precip_total"]))
    assert data["temp_max"] == 21.0
    assert data["rh_min"] == 58.0
    _assert_series_has(data, "temps", "humidities", "pressures_abs", "winds", "gusts", "wind_dirs")


def test_poem_contract_from_synthetic_endpoint_series(monkeypatch, patch_streamlit):
    patch_streamlit(poem)
    base = _now_epoch() - 1200
    series = {
        "epochs": [base, base + 600, base + 1200],
        "temps": [18.0, 20.0, 21.0],
        "humidities": [70.0, 62.0, 58.0],
        "pressures_abs": [940.0, 941.0, 942.0],
        "pressures_msl": [1010.0, 1011.0, 1012.0],
        "winds": [10.8, 14.4, 18.0],
        "gusts": [18.0, 28.8, 32.4],
        "wind_dirs": [190.0, 205.0, 220.0],
        "precips": [0.1, 0.2, 0.3],
        "lats": [43.3, 43.3, 43.3],
        "lons": [-3.1, -3.1, -3.1],
        "has_data": True,
    }
    monkeypatch.setattr(poem, "_poem_auth_config", lambda: ({}, {}, None, True, "test"))
    monkeypatch.setattr(
        poem,
        "_station_meta",
        lambda station_id: {"codigo": station_id, "nombre": "Boya Bilbao", "lat": 43.3, "lon": -3.1, "tipo": "Boya"},
    )
    monkeypatch.setattr(poem, "_resolve_station_endpoints", lambda station_meta: (["/tr"], ["/hourly"], "test"))
    monkeypatch.setattr(
        poem,
        "fetch_poem_endpoint_series",
        lambda endpoint, station_code, auth_cache_key="": {"ok": True, "series": dict(series)},
    )

    data = poem.get_poem_data(_provider_state("POEM", "1103"))

    assert data["Tc"] == 21.0
    assert data["RH"] == 58.0
    assert data["p_abs_hpa"] == 942.0
    assert data["p_hpa"] == 1012.0
    assert data["wind"] == 18.0
    assert data["gust"] == 32.4
    assert data["wind_dir_deg"] == 220.0
    assert data["precip_total"] == pytest.approx(0.6)
    assert data["temp_max"] == 21.0
    assert data["rh_min"] == 58.0
    _assert_series_has(data, "temps", "humidities", "pressures_abs", "winds", "gusts", "wind_dirs")


def test_poem_redext_scales_declared_meteo_columns_only():
    series = poem._rows_to_series(
        [
            {
                "fecha": _now_epoch() - 300,
                "codigo": 2798,
                "hr": 9340,
                "ps": 10244,
                "ta": 1949,
                "vv_md": 117,
                "vv_mx": 141,
                "dv_md": 160,
                "hm0": 9999,
                "tp": 8888,
            }
        ],
        "2798",
        allowed_metric_keys={"hr", "ps", "ta", "vv_md", "vv_mx", "dv_md"},
    )

    assert series["has_data"] is True
    assert series["humidities"][-1] == pytest.approx(93.4)
    assert series["pressures_abs"][-1] == pytest.approx(1024.4)
    assert series["temps"][-1] == pytest.approx(19.49)
    assert series["winds"][-1] == pytest.approx(4.212)
    assert series["gusts"][-1] == pytest.approx(5.076)
    assert series["wind_dirs"][-1] == 160.0


def test_poem_redmar_mareograph_scales_wind_tenths_and_prefers_mean_direction():
    series = poem._rows_to_series(
        [
            {
                "fecha": _now_epoch() - 300,
                "codigo": 3221,
                "vv_md": 9,
                "vv_mx": 25,
                "dv_md": 207,
                "dv_mx": 259,
                "ps": 10212,
                "nivel": 3060,
            }
        ],
        "3221",
        allowed_metric_keys={"dv_md", "dv_mx", "ps", "vv_md", "vv_mx"},
        wind_scale=poem._poem_wind_scale("/doris/mareas/redmar_mir_tr"),
    )

    assert series["has_data"] is True
    assert series["winds"][-1] == pytest.approx(3.24)
    assert series["gusts"][-1] == pytest.approx(9.0)
    assert series["wind_dirs"][-1] == 207.0
    assert series["pressures_abs"][-1] == pytest.approx(1021.2)


def test_weatherlink_contract_from_synthetic_current(monkeypatch, patch_streamlit):
    patch_streamlit(weatherlink)
    epoch = _now_epoch() - 300
    payload = {
        "station_id": 374964,
        "generated_at": epoch + 10,
        "sensors": [
            {
                "sensor_type": 45,
                "data_structure_type": 10,
                "data": [
                    {
                        "ts": epoch,
                        "temp": 73.4,
                        "temp_hi": 77.0,
                        "temp_lo": 68.0,
                        "hum": 42.7,
                        "hum_hi": 55.0,
                        "hum_lo": 38.0,
                        "dew_point": 49.3,
                        "wind_speed_last": 4.0,
                        "wind_speed_hi_last_10_min": 6.0,
                        "wind_speed_hi": 8.0,
                        "wind_dir_last": 195,
                        "rainfall_daily_mm": 1.2,
                        "rain_rate_last_mm": 0.4,
                        "solar_rad": 598,
                        "uv_index": 2.3,
                        "heat_index": 73.5,
                        "wind_chill": 73.3,
                    }
                ],
            },
            {
                "sensor_type": 242,
                "data_structure_type": 12,
                "data": [
                    {
                        "ts": epoch,
                        "bar_absolute": 29.515,
                        "bar_sea_level": 29.61,
                    }
                ],
            },
        ],
    }
    historic_payload = {
        "station_id": 374964,
        "generated_at": epoch,
        "sensors": [
            {
                "sensor_type": 45,
                "data_structure_type": 11,
                "data": [
                    {
                        "ts": epoch - 600,
                        "temp_last": 70.0,
                        "temp_hi": 71.5,
                        "temp_lo": 69.0,
                        "hum_last": 48.0,
                        "hum_hi": 51.0,
                        "hum_lo": 45.0,
                        "dew_point_last": 49.0,
                        "wind_speed_avg": 3.0,
                        "wind_speed_hi": 7.0,
                        "wind_dir_of_prevail": 190,
                        "rainfall_mm": 0.0,
                        "bar": 29.60,
                        "bar_absolute": 29.50,
                        "solar_rad_avg": 550,
                        "uv_index_avg": 2.0,
                    },
                    {
                        "ts": epoch - 300,
                        "temp_last": 72.0,
                        "temp_hi": 78.8,
                        "temp_lo": 70.0,
                        "hum_last": 44.0,
                        "hum_hi": 52.0,
                        "hum_lo": 37.0,
                        "dew_point_last": 48.5,
                        "wind_speed_avg": 4.0,
                        "wind_speed_hi": 9.0,
                        "wind_dir_of_prevail": 195,
                        "rainfall_mm": 0.2,
                        "bar": 29.61,
                        "bar_absolute": 29.515,
                        "solar_rad_avg": 598,
                        "uv_index_avg": 2.3,
                    },
                ],
            }
        ],
    }
    state = _provider_state("WEATHERLINK", "374964")
    state.update(
        {
            "weatherlink_api_key": "key",
            "weatherlink_api_secret": "secret",
            "weatherlink_station_alt": "120",
            "weatherlink_stations": [
                {
                    "station_id": 374964,
                    "station_name": "Davis Test",
                    "latitude": 41.4,
                    "longitude": 2.1,
                    "elevation": 100,
                }
            ],
        }
    )
    monkeypatch.setattr(
        weatherlink,
        "fetch_weatherlink_current",
        lambda station_id, api_key, api_secret, credential_hash="": {"ok": True, "payload": payload},
    )
    monkeypatch.setattr(
        weatherlink,
        "fetch_weatherlink_historic",
        lambda station_id, api_key, api_secret, start_ts, end_ts, credential_hash="": {"ok": True, "payload": historic_payload},
    )

    data = weatherlink.get_weatherlink_data(state)

    assert data["station_code"] == "374964"
    assert data["station_name"] == "Davis Test"
    assert data["Tc"] == pytest.approx(23.0, abs=0.05)
    assert data["RH"] == 42.7
    assert data["Td"] == pytest.approx(9.6, abs=0.05)
    assert data["p_hpa"] == pytest.approx(1002.713, abs=0.01)
    assert data["p_abs_hpa"] == pytest.approx(999.496, abs=0.01)
    assert data["wind"] == pytest.approx(6.437, abs=0.001)
    assert data["gust"] == pytest.approx(9.656, abs=0.001)
    assert data["wind_dir_deg"] == 195
    assert data["precip_total"] == 1.2
    assert data["solar_radiation"] == 598
    assert data["uv"] == 2.3
    assert data["temp_max"] == pytest.approx(26.0, abs=0.05)
    assert data["temp_min"] == pytest.approx(20.0, abs=0.05)
    assert data["rh_max"] == 55.0
    assert data["rh_min"] == 37.0
    assert data["gust_max"] == pytest.approx(14.484, abs=0.001)
    assert data["_series"]["has_data"] is True
    assert len(data["_series"]["epochs"]) >= 2
    for key in ("temps", "humidities", "pressures_abs", "winds", "gusts", "wind_dirs"):
        assert len(data["_series"][key]) == len(data["_series"]["epochs"])


def test_weatherlink_mock_payload_matches_service_contract():
    from scripts.mock_weatherlink_server import build_current_payload, build_historic_payload, build_station

    station = build_station("374964", "Davis Mock")
    epoch = _now_epoch() - 120
    payload = build_current_payload("374964", epoch=epoch)

    data = weatherlink.normalize_weatherlink_current(payload, station=station, altitude_m=39)
    historic = weatherlink.normalize_weatherlink_historic_series(
        build_historic_payload("374964", start_ts=epoch - 3600, end_ts=epoch),
        altitude_m=39,
    )

    assert data["station_code"] == "374964"
    assert data["station_name"] == "Davis Mock"
    assert data["_series"]["has_data"] is True
    assert historic["has_data"] is True
    assert len(historic["epochs"]) >= 2
    assert not math.isnan(float(data["Tc"]))
    assert not math.isnan(float(data["RH"]))
    assert not math.isnan(float(data["p_hpa"]))
    assert not math.isnan(float(data["temp_max"]))
    assert not math.isnan(float(data["temp_min"]))
    assert not math.isnan(float(data["rh_max"]))
    assert not math.isnan(float(data["rh_min"]))
    assert not math.isnan(float(data["gust_max"]))


def test_weatherlink_historic_series_fetches_daily_chunks(monkeypatch):
    from scripts.mock_weatherlink_server import build_historic_payload

    start = _now_epoch() - (2 * 24 * 3600) - 3600
    end = start + (2 * 24 * 3600) + 3600
    calls = []

    def fake_historic(station_id, api_key, api_secret, *, start_ts, end_ts, credential_hash=""):
        calls.append((start_ts, end_ts))
        return {
            "ok": True,
            "payload": build_historic_payload(station_id, start_ts=start_ts, end_ts=end_ts),
        }

    monkeypatch.setattr(weatherlink, "fetch_weatherlink_historic", fake_historic)

    series = weatherlink.fetch_weatherlink_historic_series(
        "374964",
        "key",
        "secret",
        start_ts=start,
        end_ts=end,
        altitude_m=39,
    )

    assert series["has_data"] is True
    assert len(calls) == 3
    assert all((chunk_end - chunk_start) <= 24 * 3600 for chunk_start, chunk_end in calls)
    assert len(series["epochs"]) == len(set(series["epochs"]))
    assert series["epochs"] == sorted(series["epochs"])
    assert not math.isnan(float(series["_extremes"]["temp_max"]))


def test_weatherlink_today_series_with_lookback_starts_three_hours_before_day(monkeypatch, patch_streamlit):
    patch_streamlit(weatherlink)
    state = _provider_state("WEATHERLINK", "374964")
    state.update(
        {
            "weatherlink_api_key": "key",
            "weatherlink_api_secret": "secret",
            "weatherlink_station_alt": "39",
            "weatherlink_stations": [{"station_id": 374964, "time_zone": "Europe/Madrid"}],
        }
    )
    calls = []

    monkeypatch.setattr(weatherlink, "_today_window", lambda station, now_epoch=None: (100_000, 120_000))

    def fake_series(station_id, api_key, api_secret, *, start_ts, end_ts, altitude_m=None, credential_hash="", max_chunk_seconds=24 * 3600):
        calls.append((station_id, start_ts, end_ts, altitude_m))
        return {"has_data": True, "epochs": [start_ts, end_ts], "temps": [20.0, 21.0], "humidities": [60.0, 61.0], "pressures_abs": [1000.0, 1001.0]}

    monkeypatch.setattr(weatherlink, "fetch_weatherlink_historic_series", fake_series)

    series = weatherlink.fetch_weatherlink_today_series_with_lookback("374964", hours_before_start=3, state=state)

    assert series["has_data"] is True
    assert calls == [("374964", 100_000 - (3 * 3600), 120_000, "39")]
