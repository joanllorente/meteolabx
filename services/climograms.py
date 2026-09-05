"""Puente hacia ``domain.climograms``.

El cálculo de climogramas se mudó a ``domain/`` cuando el backend empezó a
servir la pestaña de Histórico: ``server/`` y ``domain/`` no pueden importar
el paquete ``services`` —es el lado Streamlit, con su propio acceso HTTP a
proveedores— y así lo comprueba
``tests/backend/test_no_frontend_provider_http.py``.

El módulo es cálculo puro (pandas + i18n), así que su sitio natural es el
dominio. Este puente mantiene vivo el import de ``meteolabx.py``, que lo carga
por nombre, sin tocar la app actual.
"""

from __future__ import annotations

from domain.climograms import *  # noqa: F401,F403
from domain.climograms import (  # noqa: F401
    ClimogramPeriod,
    build_chart_table,
    build_extremes_table,
    build_general_metrics_table,
    build_period_specs,
    build_units_table,
    resolve_chart_granularity,
)
