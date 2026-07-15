from scripts.analyze_netatmo_windy_duplicates import _median_resolution, preferred_provider


def test_median_resolution_uses_actual_timestamp_differences() -> None:
    assert _median_resolution([1000, 1300, 1605, 1905]) == 300


def test_preference_follows_resolution_rule() -> None:
    assert preferred_provider(3600, 300)[0] == "WINDY"
    assert preferred_provider(300, 300)[0] == "NETATMO"
    assert preferred_provider(300, 600)[0] == "NETATMO"
    assert preferred_provider(None, 300)[0] == "WINDY"
