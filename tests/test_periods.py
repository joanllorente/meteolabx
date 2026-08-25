from datetime import date

from domain.parsing.periods import merge_date_periods


def test_merge_date_periods_unifies_overlaps_and_adjacent_ranges() -> None:
    assert merge_date_periods(
        [
            (date(2026, 3, 1), date(2026, 3, 31)),
            (date(2026, 1, 31), date(2026, 2, 28)),
            (date(2026, 1, 1), date(2026, 1, 31)),
            (date(2026, 5, 1), date(2026, 5, 31)),
        ]
    ) == [
        (date(2026, 1, 1), date(2026, 3, 31)),
        (date(2026, 5, 1), date(2026, 5, 31)),
    ]


def test_merge_date_periods_normalizes_reversed_ranges() -> None:
    assert merge_date_periods(
        [(date(2026, 4, 30), date(2026, 4, 1))]
    ) == [(date(2026, 4, 1), date(2026, 4, 30))]
