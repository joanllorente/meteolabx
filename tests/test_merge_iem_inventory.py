from scripts.merge_iem_inventory import (
    apply_cocorahs_validation,
    apply_liveness_overrides,
    apply_saved_validations,
    merge_inventory,
)
from scripts.validate_iem_unresolved_stations import apply_results


def inv(*rows):
    return {"stations": list(rows), "networks": []}


def test_filters_new_hydrology_dcp_but_keeps_weather_dcp():
    raw = inv(
        {"network": "XX_DCP", "id": "river", "online": True},
        {"network": "XX_DCP", "id": "weather", "online": True},
    )
    sensors = {("XX_DCP", "weather"): {"thermometer": True}}
    merged, stats = merge_inventory(inv(), raw, dcp_currents=sensors,
        sensor_currents={}, asos_currents={}, asos_networks=set(), killed=set())
    assert [row["id"] for row in merged["stations"]] == ["weather"]
    assert merged["stations"][0]["sensors"]["thermometer"] is True
    assert stats["raw_dcp_excluded"] == 1


def test_preserves_previously_valid_missing_station_as_offline():
    old = inv({"network": "XX_DCP", "id": "old", "online": True,
               "archive_begin": "2020-01-01", "sensors": {"thermometer": True}})
    merged, stats = merge_inventory(old, inv(), dcp_currents={}, sensor_currents={},
                                    asos_currents={}, asos_networks=set(), killed=set())
    row = merged["stations"][0]
    assert row["online"] is False
    assert row["status"] == "unconfirmed_missing"
    assert row["archive_begin"] == "2020-01-01"
    assert stats["old_missing_preserved"] == 1


def test_kill_list_wins_over_fresh_catalogue():
    raw = inv({"network": "WMO", "id": "duplicate", "online": True})
    merged, stats = merge_inventory(inv(), raw, dcp_currents={}, sensor_currents={},
        asos_currents={}, asos_networks=set(), killed={"WMO|duplicate"})
    assert merged["stations"] == []
    assert stats["duplicates_excluded"] == 1


def test_iem_validation_removes_rows_without_accessible_weather_data():
    payload = inv(
        {"network": "AUTO", "id": "ok", "online": True},
        {"network": "AUTO", "id": "quiet", "online": True},
        {"network": "RAOB", "id": "upper", "online": True},
    )
    results = {
        "AUTO|ok": {"result": "weather_data", "sensors": {"thermometer": True}},
        "AUTO|quiet": {"result": "no_data_sample"},
        "RAOB|upper": {"result": "unsupported_product"},
    }
    stats = apply_results(payload, results)
    rows = {row["id"]: row for row in payload["stations"]}
    assert set(rows) == {"ok"}
    assert rows["ok"]["sensors"]["thermometer"] is True
    assert stats["unsupported_removed"] == 1
    assert stats["no_data_removed"] == 1


def test_saved_validation_exclusions_survive_future_inventory_merge(tmp_path):
    payload = inv(
        {"network": "IA_COOP", "id": "ok", "online": True},
        {"network": "IA_COOP", "id": "empty", "online": True},
    )
    report = tmp_path / "validation.json"
    report.write_text('{"stations":{"IA_COOP|empty":{"result":"no_data_sample"}}}')
    assert apply_saved_validations(payload, (report,)) == 1
    assert [row["id"] for row in payload["stations"]] == ["ok"]


def test_cocorahs_bulk_exclusions_survive_future_merge(tmp_path):
    payload = inv({"network": "IA_COCORAHS", "id": "empty", "online": True})
    report = tmp_path / "coco.json"
    report.write_text('{"stations":[{"network":"IA_COCORAHS","id":"empty"}]}')
    assert apply_cocorahs_validation(payload, report) == 1
    assert payload["stations"] == []


def test_confirmed_liveness_override_reactivates_station(tmp_path):
    payload = inv({"network": "CA_DCP", "id": "X", "online": False,
                   "has_historical": True, "status": "historical"})
    report = tmp_path / "live.json"
    report.write_text('{"stations":["CA_DCP|X"]}')
    assert apply_liveness_overrides(payload, report) == 1
    assert payload["stations"][0]["online"] is True
    assert payload["stations"][0]["has_historical"] is True
    assert "status" not in payload["stations"][0]
