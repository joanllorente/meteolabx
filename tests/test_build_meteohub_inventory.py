from scripts.build_meteohub_inventory import merge_existing_inventory


def _station(station_id, name, lat, *, active=True):
    return {
        "id": station_id, "name": name, "network": "test", "lat": lat,
        "lon": 12.0, "active_now": active, "observed_start": "2026-05-28",
    }


def test_merge_preserves_missing_and_replaces_renamed_location():
    existing = [
        _station("test|old", "Old name", 41.0),
        _station("test|missing", "Missing", 42.0),
    ]
    current = [
        _station("test|new", "New name", 41.0),
        _station("test|added", "Added", 43.0),
    ]

    merged, report = merge_existing_inventory(
        current, existing, checked_at="2026-09-07T00:00:00Z",
    )
    by_id = {row["id"]: row for row in merged}

    assert "test|old" not in by_id
    assert by_id["test|missing"]["status"] == "unconfirmed_missing"
    assert by_id["test|missing"]["missing_checks"] == 1
    assert by_id["test|added"]["active_now"] is True
    assert report["renamed_or_rekeyed"] == ["test|old"]
    assert report["unconfirmed_missing"] == ["test|missing"]
