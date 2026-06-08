from datetime import date
from types import SimpleNamespace

from services import climograms


def test_wu_history_requests_are_clipped_to_current_day(monkeypatch):
    calls = []

    def fake_fetch_wu_history_daily(*, station_id, api_key, start_date, end_date):
        calls.append((station_id, api_key, start_date, end_date))
        return {"observations": []}

    monkeypatch.setattr(climograms, "fetch_wu_history_daily", fake_fetch_wu_history_daily)
    monkeypatch.setattr(climograms, "st", SimpleNamespace(session_state={}))

    frame = climograms.fetch_wu_daily_history_for_periods(
        station_id="TEST1",
        api_key="key",
        periods=[
            climograms.ClimogramPeriod(
                label="2026",
                start=date(2026, 1, 1),
                end=date(2026, 12, 31),
            )
        ],
        today_date=date(2026, 6, 6),
    )

    assert frame.empty
    assert calls
    assert calls[-1][2:] == ("20260605", "20260606")
    assert all(end_date <= "20260606" for *_prefix, end_date in calls)


def test_wu_history_skips_future_periods(monkeypatch):
    calls = []

    def fake_fetch_wu_history_daily(**kwargs):
        calls.append(kwargs)
        return {"observations": []}

    monkeypatch.setattr(climograms, "fetch_wu_history_daily", fake_fetch_wu_history_daily)
    monkeypatch.setattr(climograms, "st", SimpleNamespace(session_state={}))

    frame = climograms.fetch_wu_daily_history_for_periods(
        station_id="TEST1",
        api_key="key",
        periods=[
            climograms.ClimogramPeriod(
                label="2027",
                start=date(2027, 1, 1),
                end=date(2027, 12, 31),
            )
        ],
        today_date=date(2026, 6, 6),
    )

    assert frame.empty
    assert calls == []
