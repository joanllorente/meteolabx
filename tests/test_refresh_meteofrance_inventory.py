import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "refresh_meteofrance_station_inventory.py"
SPEC = importlib.util.spec_from_file_location("refresh_meteofrance", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


def raw(station_id: str):
    return {
        "Id_station": station_id, "Id_omm": "", "Nom_usuel": f"S {station_id}",
        "Latitude": "45.1", "Longitude": "2.2", "Altitude": "123",
        "Date_ouverture": "2000-01-01", "Pack": "RADOME",
    }


def test_merge_preserves_sensors_and_flags_history():
    old = [{"id": "1", "id_station": "1", "sensors": {"thermometer": True}}]
    rows, report = module.merge_catalog([raw("1"), raw("2")], old, set())
    by_id = {row["id"]: row for row in rows}
    assert report["added"] == ["2"]
    assert by_id["1"]["sensors"]["thermometer"] is True
    assert by_id["2"]["has_historical"] is True
    assert by_id["2"]["status"] == "active"
    assert by_id["2"]["sensor_probe_pending"] is True


def test_only_confirmed_dpclim_absence_is_removed():
    old = [{"id": "1", "id_station": "1"}, {"id": "2", "id_station": "2"}]
    rows, report = module.merge_catalog([], old, {"1"})
    assert [row["id"] for row in rows] == ["2"]
    assert rows[0]["status"] == "unconfirmed_missing"
    assert report["removed_without_dpclim"] == ["1"]
