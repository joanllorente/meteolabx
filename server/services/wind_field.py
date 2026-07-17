"""Configuración visual del campo mundial de velocidad del viento."""

from __future__ import annotations

from typing import Sequence, Tuple


FIELD_ALGORITHM_VERSION = 1
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
