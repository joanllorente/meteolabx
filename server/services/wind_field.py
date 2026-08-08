"""Configuración visual del campo mundial de velocidad del viento."""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple

import numpy as np


FIELD_ALGORITHM_VERSION = 2
COLOR_SCALE_VERSION = 1

# Velocidad media (km/h) → RGB. El azul representa calma; la transición hasta
# violeta reserva contraste suficiente para temporales fuertes sin saturar el
# mapa con las velocidades habituales de 5-30 km/h.
COLOR_STOPS: Sequence[Tuple[float, Tuple[int, int, int]]] = (
    (0.0, (83, 167, 231)),
    (5.0, (67, 196, 207)),
    (10.0, (72, 201, 146)),
    (20.0, (185, 218, 83)),
    (30.0, (247, 207, 63)),
    (40.0, (247, 151, 50)),
    (60.0, (226, 73, 50)),
    (80.0, (178, 42, 91)),
    (110.0, (103, 35, 125)),
    (150.0, (54, 24, 91)),
)

# Bandas de 2 km/h: suavizan el ruido entre estaciones sin borrar gradientes.
BAND_SIZE_KMH = 2.0


def interpolate_wind_grid(
    points: Iterable[Tuple[float, float, float]],
) -> tuple[np.ndarray, np.ndarray]:
    """Mantiene el perfil espacial original del viento.

    La temperatura usa desde la versión 6 un alcance mayor y conserva parte
    de los extremos aislados. Esos parámetros no son apropiados para el viento,
    que es más local y racheado, por lo que se fijan aquí explícitamente.
    """
    from server.services.temperature_field import interpolate_grid

    return interpolate_grid(
        points,
        medium_influence_cells=36,
        medium_sigma_cells=10.0,
        local_influence_cells=10,
        local_sigma_cells=1.5,
        local_single_station_share=0.0,
        regional_mean_shift_limit=0.0,
        density_adaptive=False,
    )
