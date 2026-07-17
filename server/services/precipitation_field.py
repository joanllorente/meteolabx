"""Configuracion del campo de precipitacion acumulada en las ultimas 24 h."""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple

import numpy as np

from server.services.temperature_field import (
    CELL_DEG,
    FIELD_BBOX,
    _add_kernel,
    _aggregate_station_points,
    _gaussian_kernel,
    _grid_shape,
    _land_mask,
)


FIELD_ALGORITHM_VERSION = 3
COLOR_SCALE_VERSION = 1

# La lluvia es mucho mas local que la temperatura. El campo de fondo une
# observaciones cercanas, pero cada pluviómetro conserva una zona compacta en
# la que domina su lectura real. Esto evita diluir un aguacero de 38 mm entre
# muchas estaciones secas y, a la vez, hace que su influencia caiga deprisa.
BACKGROUND_INFLUENCE_CELLS = 14
BACKGROUND_KERNEL_SIGMA = 4.5
LOCAL_INFLUENCE_CELLS = 8
LOCAL_KERNEL_SIGMA = 2.5
MIN_BACKGROUND_WEIGHT = 0.003

# mm/24 h -> RGB. Los primeros tramos separan llovizna, lluvia debil y lluvia
# moderada; los violetas quedan reservados para acumulados intensos/extremos.
COLOR_STOPS: Sequence[Tuple[float, Tuple[int, int, int]]] = (
    (0.0, (224, 238, 247)),
    (0.2, (183, 224, 240)),
    (1.0, (116, 200, 225)),
    (2.0, (62, 169, 214)),
    (5.0, (43, 126, 191)),
    (10.0, (42, 91, 166)),
    (20.0, (81, 65, 155)),
    (50.0, (129, 48, 144)),
    (100.0, (180, 36, 109)),
    (200.0, (111, 16, 73)),
)

BAND_SIZE_MM = 0.2


def _add_nearest_station_kernel(
    strength: np.ndarray,
    nearest_amount: np.ndarray,
    kernel: np.ndarray,
    *,
    row: int,
    col: int,
    amount: float,
    radius_cells: int,
) -> None:
    """Guarda en cada celda la lectura de la estación local más próxima."""
    rows, cols = strength.shape
    size = 2 * radius_cells + 1
    row_0 = max(0, row - radius_cells)
    row_1 = min(rows, row + radius_cells + 1)
    col_0 = max(0, col - radius_cells)
    col_1 = min(cols, col + radius_cells + 1)
    window = kernel[
        row_0 - (row - radius_cells): size - ((row + radius_cells + 1) - row_1),
        col_0 - (col - radius_cells): size - ((col + radius_cells + 1) - col_1),
    ]
    strength_window = strength[row_0:row_1, col_0:col_1]
    nearest_window = nearest_amount[row_0:row_1, col_0:col_1]
    update = window > strength_window
    np.copyto(strength_window, window, where=update)
    np.copyto(nearest_window, np.float32(amount), where=update)


def interpolate_precipitation_grid(
    points: Iterable[Tuple[float, float, float]],
) -> tuple[np.ndarray, np.ndarray]:
    """Interpola lluvia con transición compacta y máximos locales fieles.

    El fondo se calcula sobre ``sqrt(mm)`` para que un extremo no pinte una
    región entera. Encima se mezcla la lectura de la estación más próxima con
    un peso muy local: vale 1 exactamente sobre el pluviómetro y cae casi a
    cero en unos 80 km. Las estaciones secas usan la misma regla, por lo que
    también frenan manchas húmedas cercanas.
    """
    source_points = [
        (float(lat), float(lon), max(0.0, float(amount)))
        for lat, lon, amount in points
        if np.isfinite(lat) and np.isfinite(lon) and np.isfinite(amount)
    ]
    stations = _aggregate_station_points(
        source_points,
        cell_deg=CELL_DEG,
        block_size_cells=1,
    )
    rows, cols = _grid_shape(CELL_DEG)
    background_weight = np.zeros((rows, cols), dtype=np.float32)
    background_value = np.zeros((rows, cols), dtype=np.float32)
    local_strength = np.zeros((rows, cols), dtype=np.float32)
    nearest_amount = np.zeros((rows, cols), dtype=np.float32)

    background_kernel = _gaussian_kernel(
        BACKGROUND_INFLUENCE_CELLS,
        BACKGROUND_KERNEL_SIGMA,
    )
    local_kernel = _gaussian_kernel(
        LOCAL_INFLUENCE_CELLS,
        LOCAL_KERNEL_SIGMA,
    )
    for row, col, station_amount in stations:
        _add_kernel(
            background_weight,
            background_value,
            background_kernel,
            row=row,
            col=col,
            value=float(np.sqrt(station_amount)),
            radius_cells=BACKGROUND_INFLUENCE_CELLS,
        )
        _add_nearest_station_kernel(
            local_strength,
            nearest_amount,
            local_kernel,
            row=row,
            col=col,
            amount=station_amount,
            radius_cells=LOCAL_INFLUENCE_CELLS,
        )

    background_sqrt = np.divide(
        background_value,
        np.maximum(background_weight, np.float32(1e-9)),
    )
    background_amount = np.square(
        np.maximum(background_sqrt, 0.0),
        dtype=np.float32,
    )
    amount = (
        local_strength * nearest_amount
        + (1.0 - local_strength) * background_amount
    ).astype(np.float32, copy=False)

    support = background_weight >= np.float32(MIN_BACKGROUND_WEIGHT)
    # Suelo seco transparente: evita cubrir el mapa con una pelicula azul
    # palida. La opacidad sube de forma continua entre 0,05 y 1 mm.
    rain_alpha = np.clip((amount - 0.05) / 0.95, 0.0, 1.0).astype(
        np.float32, copy=False,
    )
    mask = np.where(support, _land_mask(CELL_DEG), 0.0).astype(
        np.float32, copy=False,
    )
    return amount, mask * rain_alpha
