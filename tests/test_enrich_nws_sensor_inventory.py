from scripts.enrich_nws_sensor_inventory import (
    _apply_probe_result,
    _empty_sensors,
    _selected_stations,
)


def test_retry_errors_selects_only_failed_stations():
    stations = [
        {"id": "GOOD", "sensors": {}},
        {"id": "FAILED", "sensors": {}, "sensor_probe_error": "404 Client Error"},
        {"id": "NEW"},
    ]

    selected = _selected_stations(
        stations, resume=False, retry_errors=True, max_stations=0,
    )

    assert [station["id"] for station in selected] == ["FAILED"]


def test_probe_state_counts_only_consecutive_404_and_preserves_sensors():
    station = {"id": "TEST", "sensors": {"thermometer": True}}
    empty = _empty_sensors()

    _apply_probe_result(station, empty, "404 Client Error", now_iso="2026-09-06T18:00:00+00:00")
    assert station["consecutive_404"] == 1
    assert station["sensors"]["thermometer"] is True

    _apply_probe_result(station, empty, "404 Client Error", now_iso="2026-09-07T18:00:00+00:00")
    assert station["consecutive_404"] == 2

    _apply_probe_result(station, empty, "Read timed out", now_iso="2026-09-08T18:00:00+00:00")
    assert station["consecutive_404"] == 2
    assert station["last_error_kind"] == "timeout"

    detected = dict(empty, anemometer=True)
    _apply_probe_result(station, detected, "", now_iso="2026-09-09T18:00:00+00:00")
    assert station["consecutive_404"] == 0
    assert station["sensors"]["thermometer"] is True
    assert station["sensors"]["anemometer"] is True
    assert station["sensors"]["rain_gauge"] is False
    assert "sensor_probe_error" not in station
