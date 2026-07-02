from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from server.services.ranking import RankingStore, _daily_gust_max_from_series


def test_daily_gust_max_discards_isolated_temporal_spike():
    values = [86.0, 91.0, 84.0, 267.5, 88.0, 79.0, 73.0]

    assert _daily_gust_max_from_series(values) == pytest.approx(91.0)


def test_daily_gust_max_keeps_real_high_wind_cluster():
    values = [92.0, 118.0, 143.0, 168.0, 181.0, 174.0, 151.0]

    assert _daily_gust_max_from_series(values) == pytest.approx(181.0)


def test_accumulable_ranking_uses_temporally_filtered_gust_max():
    store = RankingStore()
    now = datetime(2026, 7, 2, 14, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    day = now.date().isoformat()
    gusts = [84.0, 88.0, 91.0, 267.5, 86.0, 79.0, 67.0]

    for hour, gust in enumerate(gusts):
        store.upsert_hourly(
            "AEMET",
            "1437P",
            day=day,
            hour_key=f"{day}T{hour:02d}",
            name="EVC_NOIA",
            locality="",
            lat=42.7208,
            lon=-8.9233,
            values={"tmax": 23.0, "tmin": 14.0, "gust": gust, "rain": 0.0},
        )

    records = store.reduce_accumulable_records("AEMET", now=now)

    assert len(records) == 1
    assert records[0].gust == pytest.approx(91.0)
