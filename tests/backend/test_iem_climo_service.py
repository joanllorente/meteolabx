from datetime import date

import pytest

from server.services import iem_climo
from utils.station_metadata import aemet_series_start, iem_series_start


class _Response:
    status_code = 200
    text = (
        "station,valid,tmpf,dwpf,relh,drct,sknt,gust,p01i,alti,mslp\n"
        "LTFG,2025-06-01 00:00,50,45,70,90,10,15,0.01,29.92,\n"
        "LTFG,2025-06-01 12:00,68,50,45,180,20,30,0.02,29.90,\n"
    )


class _Client:
    def __init__(self):
        self.calls = []

    async def get(self, url, *, params=None, headers=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "params": list(params or []),
                "headers": dict(headers or {}),
                "timeout": timeout,
            }
        )
        return _Response()


@pytest.mark.asyncio
async def test_iem_climo_fetches_asos_csv_and_aggregates_daily_rows():
    client = _Client()

    frame = await iem_climo.fetch_climo_daily_for_periods(
        client,
        "TR__ASOS|LTFG",
        [(date(2025, 6, 1), date(2025, 6, 1))],
        today_date=date(2025, 6, 2),
    )

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["date"].strftime("%Y-%m-%d") == "2025-06-01"
    assert row["temp_min"] == pytest.approx(10.0)
    assert row["temp_max"] == pytest.approx(20.0)
    assert row["temp_mean"] == pytest.approx(15.0)
    assert row["wind_mean"] == pytest.approx(27.78)
    assert row["gust_max"] == pytest.approx(55.56)
    assert row["precip_total"] == pytest.approx(0.762)

    params = client.calls[0]["params"]
    assert ("network", "TR__ASOS") in params
    assert ("station", "LTFG") in params
    assert ("data", "tmpf") in params
    assert ("data", "p01i") in params


def test_iem_series_start_uses_archive_begin_from_inventory():
    assert iem_series_start("TR__ASOS|LTFG") == "2004-05-09"


def test_aemet_series_start_uses_archive_begin_from_inventory():
    assert aemet_series_start("0076") == "1934-06-01"
    assert aemet_series_start("0016A") == "1950-01-01"
    assert aemet_series_start("9771C") == "1950-01-01"
    assert aemet_series_start("9981A") == "1920-01-01"
