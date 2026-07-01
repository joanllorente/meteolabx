"""
Traductores de etiquetas dinámicas (string ES del cálculo → clave i18n).

Las cards dinámicas (presión, lluvia, claridad, balance, orto/ocaso) reciben
una etiqueta en español desde el pipeline; aquí la mapeamos a su clave de
``locales`` y la traducimos vía ``t``. Extraídos de ``meteolabx.py`` (Fase E):
puros, solo dependen de ``utils.i18n.t``.
"""

from __future__ import annotations

from utils.i18n import t


def translate_pressure_trend_label(label: str) -> str:
    mapping = {
        "Estable": "stable",
        "Subiendo rápido": "rising_fast",
        "Subiendo": "rising",
        "Bajando rápido": "falling_fast",
        "Bajando": "falling",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.pressure.{key}") if key else str(label or "—")


def translate_rain_intensity_label(label: str) -> str:
    mapping = {
        "Sin precipitación": "no_precip",
        "Traza de precipitación": "trace",
        "Lluvia muy débil": "very_light",
        "Lluvia débil": "light",
        "Lluvia ligera": "light_moderate",
        "Lluvia moderada": "moderate",
        "Lluvia fuerte": "heavy",
        "Lluvia muy fuerte": "very_heavy",
        "Lluvia torrencial": "torrential",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.rain.{key}") if key else str(label or "—")


def translate_clarity_label(label: str) -> str:
    mapping = {
        "Despejado": "clear",
        "Poco nuboso": "mostly_clear",
        "Parcialmente nuboso": "partly_cloudy",
        "Nuboso": "cloudy",
        "Muy nuboso": "very_cloudy",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.clarity.{key}") if key else str(label or "—")


def translate_balance_label(label: str) -> str:
    mapping = {
        "Superávit": "surplus",
        "Positivo": "positive",
        "Equilibrio": "balance",
        "Déficit": "deficit",
    }
    key = mapping.get(str(label or "").strip())
    return t(f"observation.cards.dynamic.balance.{key}") if key else str(label or "—")


def translate_sunrise_sunset_label(label: str) -> str:
    text = str(label or "").strip()
    if not text or "·" not in text:
        return text
    left, right = [part.strip() for part in text.split("·", 1)]
    sunrise = left.replace("Orto", "").replace("Sunrise", "").strip()
    sunset = right.replace("Ocaso", "").replace("Sunset", "").strip()
    if not sunrise and not sunset:
        return text
    return t("observation.cards.radiation.sky_clarity.sunrise_sunset", sunrise=sunrise, sunset=sunset)
