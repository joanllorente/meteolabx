from datetime import datetime

from components.internal_stats import _datetime_from_epoch


def test_stats_epoch_is_a_sortable_datetime() -> None:
    august_1 = _datetime_from_epoch(1785582000)
    august_4 = _datetime_from_epoch(1785841200)

    assert isinstance(august_1, datetime)
    assert isinstance(august_4, datetime)
    assert sorted([august_1, august_4], reverse=True) == [august_4, august_1]


def test_stats_empty_epoch_stays_empty() -> None:
    assert _datetime_from_epoch(0) is None
