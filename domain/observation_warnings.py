"""
Códigos de warning estables del pipeline de observación.

El backend emite los avisos como ``{"code": <str>, "params": {...}}`` en
lugar de texto libre. El frontend los traduce vía i18n (claves
``warnings.<code>`` en ``locales/*.json``) usando ``params`` como
argumentos de ``str.format``.

Centralizar el código + params aquí evita el "stringly-typed" en los
call-sites y desacopla el cálculo (backend) de la presentación (idioma,
emoji, redacción), que vive solo en el frontend.

IMPORTANTE: estos strings son contrato. No cambiarlos sin actualizar
``locales/*.json`` y los consumidores del frontend.
"""

from __future__ import annotations

from typing import Any, Dict

# Datos de la estación demasiado antiguos (> max_data_age_minutes).
DATA_AGE = "data_age"
# Sin altitud de usuario ni del proveedor: presión absoluta y
# termodinámica calculadas con z=0.
MISSING_ELEVATION = "missing_elevation"
# Windy devuelve suficientes puntos, pero una o varias variables quedan
# exactamente congeladas durante horas. No son extremos diarios fiables.
FLATLINED_SERIES = "flatlined_series"
# El pluviómetro ha registrado un salto de intensidad implausible: la variable
# queda en cuarentena ese día (fuera del ranking) y la estación avisa de que
# sus datos podrían no ser correctos.
SUSPECT_PRECIPITATION = "suspect_precipitation"
# El pluviómetro acumula lluvia que ningún parte del día corrobora: en un METAR
# son instrumentos distintos del mismo aparato, así que el silencio del
# discriminador de precipitación acusa al pluviómetro.
UNREPORTED_PRECIPITATION = "unreported_precipitation"


def data_age(provider: str, minutes: float) -> Dict[str, Any]:
    """Warning estructurado de datos antiguos."""
    return {
        "code": DATA_AGE,
        "params": {"provider": str(provider), "minutes": round(float(minutes))},
    }


def missing_elevation() -> Dict[str, Any]:
    """Warning estructurado de altitud ausente."""
    return {"code": MISSING_ELEVATION, "params": {}}


def flatlined_series() -> Dict[str, Any]:
    """Warning de serie upstream congelada durante varias horas."""
    return {"code": FLATLINED_SERIES, "params": {}}


def suspect_precipitation(amount_mm: float, minutes: float) -> Dict[str, Any]:
    """Warning de pluviómetro con una intensidad implausible."""
    return {
        "code": SUSPECT_PRECIPITATION,
        "params": {
            "amount": round(float(amount_mm), 1),
            "minutes": round(float(minutes)),
        },
    }


def unreported_precipitation(amount_mm: float, reports: int) -> Dict[str, Any]:
    """Warning de pluviómetro que acumula lluvia que nadie más vio."""
    return {
        "code": UNREPORTED_PRECIPITATION,
        "params": {"amount": round(float(amount_mm), 1), "reports": int(reports)},
    }
