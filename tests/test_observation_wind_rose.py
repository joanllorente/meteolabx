import math
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from tabs import observation


def test_observation_wind_rose_treats_under_two_kmh_as_calm():
    stats = observation._wind_rose_stats_cached(
        (1.5, 2.1),
        (float("nan"), float("nan")),
        (0.0, 90.0),
    )

    assert stats["calm"] == 1
    assert stats["dir_total"] == 1
    assert stats["counts"]["N"] == 0
    assert stats["counts"]["E"] == 1


def test_observation_precip_frame_hidden_when_today_total_is_zero():
    day_start = datetime(2026, 6, 1)
    epochs = [int((day_start + timedelta(hours=1)).timestamp())]

    frame = observation._prepare_observation_precip_frame(
        epochs,
        [1.2],
        day_start=day_start,
        day_end=day_start + timedelta(days=1),
        step_minutes=5,
        convert_precip=lambda value, _unit: value,
        precip_unit_pref="mm",
        precip_total_today=0.0,
    )

    assert frame is None


def test_observation_precip_frame_uses_positive_today_records_only():
    day_start = datetime(2026, 6, 1)
    epochs = [
        int((day_start - timedelta(minutes=5)).timestamp()),
        int((day_start + timedelta(hours=1)).timestamp()),
        int((day_start + timedelta(hours=2)).timestamp()),
    ]

    frame = observation._prepare_observation_precip_frame(
        epochs,
        [5.0, 0.0, 1.4],
        day_start=day_start,
        day_end=day_start + timedelta(days=1),
        step_minutes=5,
        convert_precip=lambda value, _unit: value,
        precip_unit_pref="mm",
        precip_total_today=1.4,
    )

    assert frame is not None
    assert frame["precip_mm"].tolist() == [1.4]


def test_observation_precip_frame_falls_back_to_today_total_without_series():
    day_start = datetime(2026, 6, 1)
    fallback_epoch = int((day_start + timedelta(hours=12)).timestamp())

    frame = observation._prepare_observation_precip_frame(
        [],
        [],
        day_start=day_start,
        day_end=day_start + timedelta(days=1),
        step_minutes=5,
        convert_precip=lambda value, _unit: value,
        precip_unit_pref="mm",
        precip_total_today=10.0,
        fallback_epoch=fallback_epoch,
    )

    assert frame is not None
    assert frame["precip_mm"].tolist() == [10.0]
    assert frame["precip_display"].tolist() == [10.0]


def test_observation_precip_frame_accumulates_interval_records():
    day_start = datetime(2026, 6, 1)
    epochs = [
        int((day_start + timedelta(hours=1)).timestamp()),
        int((day_start + timedelta(hours=2)).timestamp()),
        int((day_start + timedelta(hours=3)).timestamp()),
    ]

    frame = observation._prepare_observation_precip_frame(
        epochs,
        [0.2, 0.3, 0.0],
        day_start=day_start,
        day_end=day_start + timedelta(days=1),
        step_minutes=5,
        convert_precip=lambda value, _unit: value,
        precip_unit_pref="mm",
        precip_total_today=0.5,
    )

    assert frame is not None
    assert frame["precip_display"].tolist() == [0.2, 0.5, 0.5]


def test_observation_precip_frame_is_monotonic_daily_accumulation():
    """El gráfico debe mostrar el acumulado del día siempre creciente, aunque la
    serie de origen suba y baje (intervalos) o tenga reseteos espurios."""
    day_start = datetime(2026, 6, 1)
    raw = [2.5, 0.5, 0.8, 2.3, 7.4]
    epochs = [
        int((day_start + timedelta(hours=h)).timestamp())
        for h in range(1, len(raw) + 1)
    ]

    frame = observation._prepare_observation_precip_frame(
        epochs,
        raw,
        day_start=day_start,
        day_end=day_start + timedelta(days=1),
        step_minutes=5,
        convert_precip=lambda value, _unit: value,
        precip_unit_pref="mm",
        precip_total_today=sum(raw),
    )

    assert frame is not None
    display = frame["precip_display"].tolist()
    assert display == sorted(display), "el acumulado del día no es monótono"


def test_running_daily_precip_handles_spurious_reset():
    # Serie acumulada con un bajón espurio en medio: debe quedar monótona.
    out = observation._running_daily_precip_mm([2.5, 2.5, 0.5, 2.6, 2.6, 7.4])
    assert out == sorted(out)
    assert out[-1] == max(out)


def test_wet_bulb_risk_thresholds():
    assert observation._wet_bulb_risk(27.9) == observation.WetBulbRisk()
    assert observation._wet_bulb_risk(float("nan")) == observation.WetBulbRisk()

    potential = observation._wet_bulb_risk(28.0)
    assert potential.label_key == observation.WET_BULB_POTENTIAL_KEY
    assert potential.alert_key == ""

    critical = observation._wet_bulb_risk(30.5)
    assert critical.label_key == observation.WET_BULB_CRITICAL_KEY
    assert critical.alert_level == "warning"
    assert critical.alert_key == observation.WET_BULB_WARNING_KEY

    extreme = observation._wet_bulb_risk(34.0)
    assert extreme.label_key == observation.WET_BULB_EXTREME_KEY
    assert extreme.alert_level == "danger"
    assert extreme.alert_key == observation.WET_BULB_EXTREME_ALERT_KEY


def test_wet_bulb_risk_render_uses_translator():
    translations = {
        observation.WET_BULB_CRITICAL_KEY: "Translated critical",
        observation.WET_BULB_WARNING_KEY: "Translated warning",
    }
    translate = lambda key, **kwargs: translations[key]
    risk = observation._wet_bulb_risk(30.5)

    assert "Translated critical" in observation._wet_bulb_risk_subtitle_html(risk, translate)
    assert "Translated warning" in observation._wet_bulb_alert_html(risk, translate, dark=False)


def test_wet_bulb_risk_locale_keys_exist_in_supported_languages():
    locales_dir = Path(__file__).resolve().parent.parent / "locales"
    required_keys = (
        ("wet_bulb_risk", "potential"),
        ("wet_bulb_risk", "critical"),
        ("wet_bulb_risk", "extreme"),
        ("wet_bulb_alert", "warning"),
        ("wet_bulb_alert", "extreme"),
    )

    for lang in ("es", "en", "fr"):
        payload = json.loads((locales_dir / f"{lang}.json").read_text(encoding="utf-8"))
        dew_point = payload["observation"]["cards"]["basic"]["dew_point"]
        for group, key in required_keys:
            assert dew_point[group][key].strip()


def test_wet_bulb_alert_renders_before_basic_cards():
    events = []

    class _St:
        session_state = {}
        query_params = {}

        def markdown(self, message, **kwargs):
            events.append(("markdown", message, kwargs))

        def info(self, message):
            events.append(("info", message))

        def warning(self, message):
            events.append(("warning", message))

    class _Logger:
        def info(self, message):
            pass

        def warning(self, message):
            pass

    def _is_nan(value):
        try:
            return math.isnan(float(value))
        except (TypeError, ValueError):
            return True

    def _fmt(value, decimals=1):
        return "—" if _is_nan(value) else f"{float(value):.{decimals}f}"

    def _card(title, value, unit="", icon_kind="", subtitle_html="", **kwargs):
        events.append(("card", title, subtitle_html))
        return f"{title}|{subtitle_html}"

    def _render_grid(cards, cols=3, extra_class=""):
        events.append(("grid", list(cards), cols, extra_class))

    def _section_title(text):
        events.append(("section", text))

    ctx = {
        "RD": 287.0,
        "Te": float("nan"),
        "Tv": float("nan"),
        "Tw": 30.5,
        "ensure_chart_data": None,
        "_fmt_precip_display": _fmt,
        "_fmt_pressure_display": _fmt,
        "_fmt_radiation_display": _fmt,
        "_fmt_radiation_energy_display": _fmt,
        "_fmt_temp_display": _fmt,
        "_fmt_wind_display": _fmt,
        "_get_aemet_service": lambda: SimpleNamespace(is_aemet_connection=lambda: False),
        "_infer_series_step_minutes": lambda _dt: 5,
        "_plotly_chart_stretch": lambda *args, **kwargs: None,
        "_translate_balance_label": lambda label: label,
        "_translate_clarity_label": lambda label: label,
        "_translate_pressure_trend_label": lambda label: label,
        "_translate_rain_intensity_label": lambda label: label,
        "_translate_sunrise_sunset_label": lambda label: label,
        "balance": float("nan"),
        "base": {
            "Tc": 32.0,
            "RH": 65.0,
            "Td": 24.0,
            "wind_dir_deg": float("nan"),
            "wind": 0.0,
            "gust": float("nan"),
            "precip_total": 0.0,
            "feels_like": 34.0,
            "heat_index": 35.0,
        },
        "card": _card,
        "clarity": float("nan"),
        "connected": True,
        "connection_type": "WU",
        "convert_precip": lambda value, _unit: value,
        "convert_pressure": lambda value, _unit: value,
        "convert_radiation": lambda value, _unit: value,
        "convert_temperature": lambda value, _unit: value,
        "convert_wind": lambda value, _unit: value,
        "dark": False,
        "dp3": float("nan"),
        "e": 30.0,
        "et0": float("nan"),
        "has_chart_data": False,
        "has_radiation": False,
        "html": SimpleNamespace(escape=lambda text: text),
        "inst_label": "test",
        "inst_mm_h": 0.0,
        "is_nan": _is_nan,
        "lcl": float("nan"),
        "logger": _Logger(),
        "p_abs": float("nan"),
        "p_arrow": "",
        "p_label": "stable",
        "p_msl": float("nan"),
        "precip_unit_pref": "mm",
        "precip_unit_txt": "mm",
        "pressure_unit_pref": "hPa",
        "pressure_unit_txt": "hPa",
        "q_gkg": float("nan"),
        "r5_mm_h": 0.0,
        "r10_mm_h": 0.0,
        "radiation_energy_unit_txt": "MJ/m²",
        "radiation_unit_pref": "W/m²",
        "radiation_unit_txt": "W/m²",
        "render_grid": _render_grid,
        "rho": float("nan"),
        "rho_v_gm3": float("nan"),
        "section_title": _section_title,
        "sky_clarity_label": lambda value: "—",
        "solar_rad": float("nan"),
        "st": _St(),
        "t": lambda key, **kwargs: {
            observation.WET_BULB_WARNING_KEY: "Translated warning",
            observation.WET_BULB_CRITICAL_KEY: "Translated critical",
        }.get(key, key),
        "temp_unit_pref": "C",
        "temp_unit_txt": "°C",
        "theme_mode": "light",
        "theta": float("nan"),
        "time": time,
        "uv": float("nan"),
        "water_balance_label": lambda value: "—",
        "wind_dir_text": lambda deg: "N",
        "wind_unit_pref": "km/h",
        "wind_unit_txt": "km/h",
        "z": float("nan"),
    }

    observation.render_observation_tab(ctx)

    alert_idx = next(
        i for i, event in enumerate(events)
        if event[0] == "markdown" and "Translated warning" in event[1]
    )
    grid_idx = next(i for i, event in enumerate(events) if event[0] == "grid")
    assert alert_idx < grid_idx
    assert any(
        event[0] == "card"
        and event[1] == "observation.cards.basic.dew_point.title"
        and "Translated critical" in event[2]
        for event in events
    )
