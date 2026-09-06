import json

from providers.meteocat_provider import _has_open_status
from utils import station_metadata


def test_meteocat_lifecycle_dates_for_historical_station(tmp_path, monkeypatch):
    catalog = tmp_path / "meteocat.json"
    catalog.write_text(json.dumps([{
        "codi": "AN",
        "estats": [
            {"codi": 2, "dataInici": "1992-05-11T15:30Z", "dataFi": "2002-10-29T05:00Z"},
            {"codi": 1, "dataInici": "2002-10-29T05:00Z", "dataFi": None},
        ],
    }]), encoding="utf-8")
    monkeypatch.setattr(station_metadata, "METEOCAT_STATIONS_PATH", catalog)
    station_metadata._catalog.cache_clear()
    try:
        assert station_metadata.meteocat_series_start("AN") == "1992-05-11"
        assert station_metadata.meteocat_series_end("AN") == "2002-10-29"
    finally:
        station_metadata._catalog.cache_clear()


def test_meteocat_dismantled_open_status_is_not_online():
    assert not _has_open_status({
        "estats": [
            {"codi": 2, "dataFi": "2002-10-29T05:00Z"},
            {"codi": 1, "dataFi": None},
        ]
    })
    assert _has_open_status({"estats": [{"codi": 2, "dataFi": None}]})
