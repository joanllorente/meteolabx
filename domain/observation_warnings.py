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
