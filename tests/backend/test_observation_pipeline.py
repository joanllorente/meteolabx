"""
Tests del pipeline puro ``domain.observation_pipeline``.

Estos tests no levantan Streamlit ni FastAPI; el pipeline es puro y
testeable como cualquier función. Fijamos el comportamiento que el
adapter de ``meteolabx.process_standard_provider`` depende y que el
backend podrá explotar más adelante.
"""

from __future__ import annotations

import math
import time as _time
from pathlib import Path

import pytest

from domain.observation_pipeline import (
    ELEVATION_SOURCE,
    LAST_UPDATE_TIME,
    PROVIDER_STATION_ALT,
    PROVIDER_STATION_ID,
    ProcessingContext,
    ProcessingResult,
    STATION_ELEVATION,
    STATION_LAT,
    normalize_chart_series,
    process_observation,
    rain_intensity_label,
)


# =====================================================================
# Pureza: nada de streamlit, nada de fastapi
# =====================================================================

def test_pipeline_module_does_not_import_streamlit_or_fastapi() -> None:
    """
    Garantía estática: el módulo no debe arrastrar streamlit ni fastapi
    por importación. Chequeo textual para no depender de qué pasó antes
    en ``sys.modules``.
    """
    source = Path("domain/observation_pipeline.py").read_text(encoding="utf-8")
    assert "import streamlit" not in source
    assert "from streamlit" not in source
    assert "import fastapi" not in source
    assert "from fastapi" not in source


def test_normalize_chart_series_accepts_only_canonical_precips() -> None:
    normalized = normalize_chart_series(
        {
            "epochs": [1, 2],
            "precips": [0.0, 0.4],
            "precip_accum_mm": [0.0, 0.0],
            "precip_step_mm": [0.0, 0.4],
            "has_data": True,
        }
    )

    assert normalized["precips"] == [0.0, 0.4]


def test_process_observation_fills_missing_wind_from_series() -> None:
    series = {
        "epochs": [1, 2, 3],
        "winds": [float("nan"), 9.0, 11.0],
        "gusts": [15.0, float("nan"), 21.0],
        "wind_dirs": [float("nan"), 240.0, 260.0],
        "has_data": True,
    }
    base = _fresh_base(
        wind=float("nan"),
        gust=float("nan"),
        wind_dir_deg=float("nan"),
    )

    process_observation(base, ProcessingContext(provider_name="METEOCAT", series_override=series))

    assert base["wind"] == pytest.approx(11.0)
    assert base["gust"] == pytest.approx(21.0)
    assert base["wind_dir_deg"] == pytest.approx(260.0)


# =====================================================================
# rain_intensity_label
# =====================================================================

@pytest.mark.parametrize(
    "rate,expected",
    [
        (0.0, "Sin precipitación"),
        (-1.0, "Sin precipitación"),
        (0.1, "Traza de precipitación"),
        (0.6, "Lluvia muy débil"),
        (2.0, "Lluvia débil"),
        (5.0, "Lluvia ligera"),
        (12.0, "Lluvia moderada"),
        (30.0, "Lluvia fuerte"),
        (50.0, "Lluvia muy fuerte"),
        (200.0, "Lluvia torrencial"),
    ],
)
def test_rain_intensity_label(rate: float, expected: str) -> None:
    assert rain_intensity_label(rate) == expected


def test_rain_intensity_label_nan_returns_sin_precipitacion() -> None:
    assert rain_intensity_label(float("nan")) == "Sin precipitación"


# =====================================================================
# Fixtures
# =====================================================================

def _fresh_base(**overrides) -> dict:
    """
    Observación canónica realista con epoch reciente para evitar el
    warning de datos antiguos en los tests que no lo prueban.
    """
    base: dict = {
        "Tc": 22.0,
        "RH": 65.0,
        "p_hpa": 1013.0,
        "p_abs_hpa": 1011.5,
        "Td": 14.0,
        "wind": 10.0,
        "gust": 18.0,
        "wind_dir_deg": 180,
        "epoch": int(_time.time()),
        "lat": 41.4,
        "lon": 2.2,
        "elevation": 12.0,
        "solar_radiation": 800.0,
        "uv": 6.0,
        "precip_total": 0.4,
    }
    base.update(overrides)
    return base


# =====================================================================
# Session updates
# =====================================================================

def test_session_updates_include_lat_lon_elevation_and_provider_metadata() -> None:
    base = _fresh_base()
    ctx = ProcessingContext(
        provider_name="AEMET",
        elevation_fallback=0.0,
        owner_station_id="0076",
        station_name="Barcelona",
        station_tz="Europe/Madrid",
    )

    result = process_observation(base, ctx)

    su = result.session_updates
    assert su[LAST_UPDATE_TIME] > 0
    assert su[STATION_LAT] == 41.4
    assert su[STATION_ELEVATION] == 12.0
    assert su[ELEVATION_SOURCE] == "AEMET"
    assert su[PROVIDER_STATION_ALT] == 12.0
    assert su[PROVIDER_STATION_ID] == "0076"
    # Prefijo por proveedor también presente.
    assert su["aemet_station_id"] == "0076"


def test_session_updates_skip_nan_and_none_fields() -> None:
    """No queremos persistir NaN/None en session_state."""
    base = _fresh_base(lat=float("nan"), lon=None)
    ctx = ProcessingContext(provider_name="AEMET")

    su = process_observation(base, ctx).session_updates

    # lat NaN: STATION_LAT se setea con NaN (compat con legacy) pero
    # PROVIDER_STATION_LAT (que es el "limpio") NO debe estar.
    assert "provider_station_lat" not in su
    assert "provider_station_lon" not in su
    # station_name vacío → no se setea
    assert "provider_station_name" not in su


def test_elevation_fallback_used_when_base_missing() -> None:
    base = _fresh_base()
    base.pop("elevation")
    ctx = ProcessingContext(provider_name="X", elevation_fallback=345.0)

    result = process_observation(base, ctx)

    assert result.derivatives["z"] == 345.0
    assert result.session_updates[STATION_ELEVATION] == 345.0


# =====================================================================
# Warnings (datos antiguos)
# =====================================================================

def test_old_data_produces_warning() -> None:
    base = _fresh_base(epoch=int(_time.time()) - 3600 * 24)  # 24 h atrás
    ctx = ProcessingContext(provider_name="AEMET", max_data_age_minutes=60.0)

    result = process_observation(base, ctx)

    assert len(result.warnings) == 1
    assert result.warnings[0]["code"] == "data_age"
    assert result.warnings[0]["params"]["provider"] == "AEMET"


def test_fresh_data_emits_no_warning() -> None:
    base = _fresh_base()
    ctx = ProcessingContext(provider_name="AEMET", max_data_age_minutes=60.0)

    assert process_observation(base, ctx).warnings == []


def test_pressure_display_uses_wu_decimals_for_wu_provider() -> None:
    base = _fresh_base(p_abs_hpa=1010.7, p_hpa=1015.4)
    # provider_for_pressure="WU" → 0 decimales
    ctx = ProcessingContext(provider_name="WU", provider_for_pressure="WU")
    result = process_observation(base, ctx)
    assert result.derivatives["p_abs_disp"] == "1011"
    assert result.derivatives["p_msl_disp"] == "1015"


def test_pressure_display_uses_one_decimal_for_non_wu() -> None:
    base = _fresh_base(p_abs_hpa=1010.7, p_hpa=1015.4)
    ctx = ProcessingContext(provider_name="AEMET", provider_for_pressure="AEMET")
    result = process_observation(base, ctx)
    assert result.derivatives["p_abs_disp"] == "1010.7"
    assert result.derivatives["p_msl_disp"] == "1015.4"


# =====================================================================
# Pressure trend 3h
# =====================================================================

def test_pressure_trend_from_base_pressure_3h_ago_when_no_series() -> None:
    """Sin serie de chart, la tendencia se calcula desde base["pressure_3h_ago"]."""
    base = _fresh_base(
        p_hpa=1015.0,
        pressure_3h_ago=1010.0,
        epoch=1000000,
        epoch_3h_ago=1000000 - 3 * 3600,
    )
    ctx = ProcessingContext(provider_name="X")
    result = process_observation(base, ctx)

    assert result.derivatives["dp3"] == pytest.approx(5.0)
    assert result.derivatives["p_arrow"] != "•"  # no es "indefinido"


def test_pressure_trend_marks_stable_when_dp3_small() -> None:
    base = _fresh_base(
        p_hpa=1015.0,
        pressure_3h_ago=1014.5,
        epoch=1000000,
        epoch_3h_ago=1000000 - 3 * 3600,
    )
    ctx = ProcessingContext(provider_name="X")
    result = process_observation(base, ctx)
    assert result.derivatives["p_label"] == "Estable"
    assert result.derivatives["p_arrow"] == "→"


def test_pressure_trend_tolerates_nan_epoch_3h_ago() -> None:
    base = _fresh_base(
        p_hpa=1015.0,
        pressure_3h_ago=1014.5,
        epoch=1000000,
        epoch_3h_ago=float("nan"),
    )
    ctx = ProcessingContext(provider_name="WEATHERLINK")

    result = process_observation(base, ctx)

    assert math.isnan(result.derivatives["dp3"])
    assert result.derivatives["p_arrow"] == "•"


# =====================================================================
# Thermodynamics
# =====================================================================

def test_thermodynamics_computed_when_temp_and_humidity_present() -> None:
    base = _fresh_base(Tc=22.0, RH=65.0)
    ctx = ProcessingContext(provider_name="X")
    result = process_observation(base, ctx)

    # Punto de rocío para 22°C, 65% RH ≈ 14.9°C
    assert result.derivatives["Td_calc"] == pytest.approx(15.0, abs=0.5)
    assert result.derivatives["e_sat"] > 0
    assert result.derivatives["e"] > 0
    # base mutado con Td calculado
    assert base["Td"] == pytest.approx(result.derivatives["Td_calc"])


def test_thermodynamics_all_nan_when_temp_missing() -> None:
    base = _fresh_base(Tc=float("nan"))
    ctx = ProcessingContext(provider_name="X")
    result = process_observation(base, ctx)
    assert math.isnan(result.derivatives["Td_calc"])
    assert math.isnan(result.derivatives["e_sat"])


def test_base_gets_feels_like_and_heat_index_calculated() -> None:
    """Estos dos siempre los calculamos nosotros, nunca confiamos en el API."""
    base = _fresh_base(Tc=28.0, RH=80.0, wind=10.0)
    ctx = ProcessingContext(provider_name="X")
    process_observation(base, ctx)
    assert "feels_like" in base
    assert "heat_index" in base
    assert isinstance(base["feels_like"], float)


# =====================================================================
# Radiación / claridad
# =====================================================================

def test_has_radiation_true_when_solar_or_uv_present() -> None:
    base = _fresh_base(solar_radiation=500.0, uv=float("nan"))
    result = process_observation(base, ProcessingContext(provider_name="X"))
    assert result.derivatives["has_radiation"] is True


def test_has_radiation_false_when_both_missing() -> None:
    base = _fresh_base(solar_radiation=float("nan"), uv=float("nan"))
    result = process_observation(base, ProcessingContext(provider_name="X"))
    assert result.derivatives["has_radiation"] is False
    assert math.isnan(result.derivatives["clarity"])


# =====================================================================
# Series del chart
# =====================================================================

def test_chart_series_uses_explicit_canonical_series() -> None:
    base = _fresh_base()
    series = {
        "epochs": [100, 200, 300],
        "temps": [20.0, 21.0, 22.0],
        "has_data": True,
    }
    ctx = ProcessingContext(provider_name="X", series_override=series, owner_station_id="STATION_ABC")
    result = process_observation(base, ctx)

    assert result.derivatives["has_chart_data"] is True
    assert result.chart_series["epochs"] == [100, 200, 300]
    assert result.chart_series_owner == ("X", "STATION_ABC")


def test_radiation_and_ui_derivatives_are_computed_in_pipeline() -> None:
    epoch = 1_718_016_600
    base = _fresh_base(epoch=epoch, Tc=36.0, RH=80.0, uv=4.0, solar_radiation=300.0)
    series = {
        "epochs": [epoch - 600, epoch],
        "temps": [35.0, 36.0],
        "humidities": [80.0, 80.0],
        "pressures_abs": [1010.0, 1010.0],
        "solar_radiations": [100.0, 300.0],
        "uv_indexes": [2.0, 4.0],
        "has_data": True,
    }
    result = process_observation(
        base,
        ProcessingContext(provider_name="X", series_override=series, sun_tz_name="UTC"),
    )
    derivatives = result.derivatives

    assert derivatives["sound_speed_ms"] > 340.0
    assert derivatives["wet_bulb_risk"] in {"potential", "critical", "extreme"}
    assert derivatives["solar_energy_today_wh_m2"] == pytest.approx(33.333333, rel=1e-5)
    assert derivatives["erythemal_irradiance_mw_m2"] == pytest.approx(100.0)
    assert derivatives["erythemal_dose_today_j_m2"] == pytest.approx(90.0)
    assert derivatives["erythemal_dose_today_sed"] == pytest.approx(0.9)


def test_chart_series_override_takes_precedence() -> None:
    base = _fresh_base()
    override = {"epochs": [99], "temps": [42.0], "has_data": True}
    ctx = ProcessingContext(provider_name="X", series_override=override,
                            owner_station_id="STATION_OWNER")
    result = process_observation(base, ctx)

    assert result.chart_series["epochs"] == [99]
    assert result.chart_series["temps"] == [42.0]


def test_chart_series_owner_none_when_no_owner_station_id() -> None:
    base = _fresh_base()
    ctx = ProcessingContext(
        provider_name="X",
        series_override={"epochs": [100], "temps": [20.0], "has_data": True},
        owner_station_id="",
    )
    result = process_observation(base, ctx)
    assert result.chart_series_owner is None


# =====================================================================
# Trend hourly
# =====================================================================

def test_trend_hourly_action_clear_when_no_series_7d_and_no_owner() -> None:
    base = _fresh_base()
    ctx = ProcessingContext(provider_name="X", series_7d=None, owner_station_id="")
    result = process_observation(base, ctx)
    assert result.trend_hourly_owner_action == "clear"


def test_trend_hourly_action_set_when_series_7d_has_data() -> None:
    base = _fresh_base()
    ctx = ProcessingContext(
        provider_name="X",
        series_7d={"epochs": [100], "temps": [20.0], "has_data": True},
        owner_station_id="STATION_HOURLY",
    )
    result = process_observation(base, ctx)
    assert result.trend_hourly_owner_action == "set"
    assert result.trend_hourly_owner == ("X", "STATION_HOURLY")


# =====================================================================
# Smoke test: shape completo
# =====================================================================

def test_pipeline_returns_complete_canonical_derivatives_block() -> None:
    expected_fields = {
        "z", "p_abs", "p_msl", "p_abs_disp", "p_msl_disp",
        "dp3", "rate_h", "p_label", "p_arrow",
        "inst_mm_h", "r5_mm_h", "r10_mm_h", "inst_label",
        "e_sat", "e", "Td_calc", "Tw", "q", "q_gkg",
        "theta", "Tv", "Te", "rho", "rho_v_gm3", "lcl",
        "sound_speed_ms", "wet_bulb_risk", "wet_bulb_alert_level",
        "solar_rad", "uv", "et0", "clarity", "balance",
        "solar_energy_today_wh_m2", "erythemal_irradiance_mw_m2",
        "erythemal_dose_today_j_m2", "erythemal_dose_today_sed",
        "has_radiation", "has_chart_data",
    }
    actual_fields = set(
        process_observation(_fresh_base(), ProcessingContext(provider_name="X")).derivatives
    )
    assert actual_fields == expected_fields
