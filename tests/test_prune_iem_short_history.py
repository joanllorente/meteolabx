from datetime import date

from scripts.prune_iem_short_history import (
    classify_climate_lifecycle,
    classify_cocorahs_as_historical,
    classify_historical_capability,
    classify_manual_metadata,
    declared_days,
    prune,
)


def test_declared_days_is_inclusive():
    assert declared_days({"archive_begin": "2024-01-01", "archive_end": "2024-12-30"}, date(2026, 1, 1)) == 365


def test_prune_only_removes_inactive_short_series():
    payload = {"stations": [
        {"network": "X", "id": "short", "online": False, "archive_begin": "2025-01-01", "archive_end": "2025-01-10"},
        {"network": "X", "id": "long", "online": False, "archive_begin": "2024-01-01", "archive_end": "2024-12-30"},
        {"network": "X", "id": "active", "online": True, "archive_begin": "2026-01-01", "archive_end": "2026-01-01"},
    ]}
    removed = prune(payload, minimum_days=365, today=date(2026, 1, 1))
    assert [row["id"] for row in removed] == ["short"]
    assert {row["id"] for row in payload["stations"]} == {"long", "active"}


def test_climate_lifecycle_uses_confirmed_daily_data():
    payload = {"stations": [
        {"network": "TXCLIMATE", "id": "live", "online": False},
        {"network": "TXCLIMATE", "id": "old", "online": True},
    ]}
    assert classify_climate_lifecycle(payload, {"TXCLIMATE|live"}) == 2
    assert payload["stations"][0]["online"] is True
    assert payload["stations"][0]["status"] == "online_manual"
    assert payload["stations"][1]["online"] is False
    assert payload["stations"][1]["status"] == "historical"


def test_all_manual_iem_network_families_get_explicit_metadata():
    payload = {"stations": [
        {"network": "TXCLIMATE"}, {"network": "IA_COOP"},
        {"network": "TX_COCORAHS"}, {"network": "IA_ASOS"},
    ]}
    assert classify_manual_metadata(payload) == 3
    assert [bool(row.get("manual")) for row in payload["stations"]] == [True, True, True, False]


def test_short_online_coop_is_removed_but_long_coop_is_kept():
    payload = {"stations": [
        {"network": "IA_COOP", "id": "short", "online": True, "archive_begin": "2025-01-01", "archive_end": "2025-01-10"},
        {"network": "IA_COOP", "id": "long", "online": True, "archive_begin": "2024-01-01", "archive_end": "2024-12-30"},
    ]}
    removed = prune(payload, minimum_days=365, today=date(2026, 1, 1))
    assert [row["id"] for row in removed] == ["short"]
    assert [row["id"] for row in payload["stations"]] == ["long"]


def test_long_inactive_cocorahs_gets_explicit_historical_status():
    payload = {"stations": [{"network": "IA_COCORAHS", "id": "X", "online": False,
                              "archive_begin": "2020-01-01", "archive_end": "2022-01-01"}]}
    assert classify_cocorahs_as_historical(payload, minimum_days=365, today=date(2026, 1, 1)) == 1
    assert payload["stations"][0]["status"] == "historical"


def test_historical_capability_is_independent_from_online_state():
    payload = {"stations": [
        {"id": "active-long", "online": True, "archive_begin": "2020-01-01"},
        {"id": "offline-long", "online": False, "archive_begin": "2020-01-01", "archive_end": "2022-01-01"},
        {"id": "active-short", "online": True, "archive_begin": "2025-12-01"},
    ]}
    classify_historical_capability(payload, minimum_days=365, today=date(2026, 1, 1))
    assert [row["has_historical"] for row in payload["stations"]] == [True, True, False]
