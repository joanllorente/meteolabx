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
from services import nws
from services import poem


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
        meteocat.V_RH: [(base, 70.0), (base + 600, 55.0)],
        meteocat.V_PRESSURE: [(base, 900.0), (base + 600, 901.0)],
        meteocat.V_WIND: [(base, 4.0), (base + 600, 5.0)],
        meteocat.V_GUST: [(base, 8.0), (base + 600, 9.0)],
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
    assert data["temp_max"] == 22.0
    assert data["temp_min"] == 18.0
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
    _assert_series_has(data, "temps", "humidities", "pressures_abs", "winds", "gusts", "wind_dirs")


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
