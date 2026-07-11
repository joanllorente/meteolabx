"""
Metadatos y capacidades comunes de proveedores.
"""

from __future__ import annotations

from typing import Any


PROVIDER_FEATURES: dict[str, dict[str, Any]] = {
    "WU": {
        "historical_supported": True,
        "historical_missing_key": "historical.errors.missing_wu_credentials",
        "requires_api_key": True,
        "api_key_source": "wu",
        "today_trends_source_key": "trends.sources.local_today",
        "synoptic_source_key": "trends.sources.wu_synoptic",
    },
    "AEMET": {
        "historical_supported": True,
        "historical_min_year": 1950,
        "historical_lookback_years": None,
        "historical_missing_key": "historical.errors.missing_aemet_station",
        "requires_api_key": False,
        "api_key_source": "aemet",
        "today_trends_source_key": "trends.sources.aemet_today",
        "synoptic_source_key": "trends.sources.aemet_synoptic",
        "synoptic_unavailable_warning_key": "trends.warnings.aemet_weekly_unavailable",
        "synoptic_unavailable_caption_key": "trends.warnings.aemet_weekly_caption",
        "series_start_provider_label": "AEMET",
        "series_start_source": "aemet",
    },
    "METEOCAT": {
        "historical_supported": True,
        "historical_missing_key": "historical.errors.missing_meteocat_station",
        "requires_api_key": False,
        "today_trends_source_key": "trends.sources.meteocat_today",
        "synoptic_source_key": "trends.sources.meteocat_synoptic",
        "series_start_provider_label": "Meteocat",
        "series_start_source": "meteocat",
    },
    "FROST": {
        "historical_supported": True,
        "historical_missing_key": "historical.errors.missing_frost_station",
        "requires_api_key": False,
    },
    "METEOFRANCE": {
        "historical_supported": True,
        "historical_missing_key": "historical.errors.missing_meteofrance_station",
        "requires_api_key": False,
        "api_key_source": "meteofrance",
        "synoptic_source_key": "trends.sources.meteofrance_synoptic",
        "series_start_provider_label": "Meteo-France",
        "series_start_source": "meteofrance",
    },
    "METEOGALICIA": {
        "historical_supported": True,
        "historical_missing_key": "historical.errors.missing_meteogalicia_station",
        "requires_api_key": False,
        "synoptic_source_key": "trends.sources.meteogalicia_synoptic",
        "synoptic_coverage_note_key": "trends.notes.meteogalicia_max_coverage",
    },
    "EUSKALMET": {
        "historical_supported": False,
        "historical_note_key": "historical.notes.euskalmet_unavailable",
        "synoptic_source_key": "trends.sources.euskalmet_synoptic",
        "synoptic_unavailable_note_key": "trends.notes.provider_insufficient_data",
    },
    "NWS": {
        "historical_supported": False,
        "historical_note_key": "historical.notes.nws_unavailable",
    },
    "POEM": {
        "historical_supported": False,
        "synoptic_source_key": "trends.sources.poem_synoptic",
        "synoptic_coverage_note_key": "trends.notes.synoptic_insufficient_coverage",
    },
    "METOFFICE": {
        "historical_supported": False,
        "today_trends_source_key": "trends.sources.metoffice_today",
        "synoptic_source_key": "trends.sources.metoffice_synoptic",
        "synoptic_coverage_note_key": "trends.notes.synoptic_insufficient_coverage",
    },
    "METEOHUB_IT": {
        "historical_supported": False,
        "today_trends_source_key": "trends.sources.meteohub_today",
        "synoptic_source_key": "trends.sources.meteohub_synoptic",
        "synoptic_coverage_note_key": "trends.notes.synoptic_insufficient_coverage",
    },
    "IEM": {
        "historical_supported": True,
        "historical_missing_key": "historical.errors.missing_iem_station",
        "requires_api_key": False,
        "series_start_provider_label": "IEM",
        "series_start_source": "iem",
        "today_trends_source_key": "trends.sources.iem_today",
        "synoptic_source_key": "trends.sources.iem_synoptic",
        "synoptic_coverage_note_key": "trends.notes.synoptic_insufficient_coverage",
    },
    "WEATHERLINK": {
        "historical_supported": True,
        "historical_missing_key": "historical.errors.missing_weatherlink_credentials",
        "requires_api_key": True,
        "requires_api_secret": True,
        "api_key_source": "weatherlink",
        "today_trends_source_key": "trends.sources.weatherlink_today",
        "synoptic_source_key": "trends.sources.weatherlink_synoptic",
        "synoptic_coverage_note_key": "trends.notes.synoptic_insufficient_coverage",
    },
}


SUPPORTED_HISTORICAL_PROVIDERS = tuple(
    provider_id for provider_id, config in PROVIDER_FEATURES.items() if config.get("historical_supported")
)


def get_provider_feature(provider_id: str) -> dict[str, Any]:
    return PROVIDER_FEATURES.get(str(provider_id or "").strip().upper(), {})
