from datetime import datetime, timezone

from models.radiation import sunrise_sunset_label


def test_sunrise_sunset_label_uses_explicit_timezone():
    epoch = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc).timestamp()

    madrid_label = sunrise_sunset_label(41.387, 2.17, epoch, tz_name="Europe/Madrid")
    utc_label = sunrise_sunset_label(41.387, 2.17, epoch, tz_name="UTC")

    assert "Ocaso 21:" in madrid_label
    assert "Ocaso 19:" in utc_label
