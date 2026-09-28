"""Cónica conforme de Lambert (LCC) para los dominios regionales de ECMWF.

Los datos de ECMWF vienen en latitud y longitud, y pintarlos tal cual estira
el este-oeste por 1/cos φ: el triple a 70° N. El visor los reproyecta a una
LCC, que conserva las formas; aquí solo se define cada proyección y se calcula
qué recuadro de latitud y longitud hay que leer para rellenar su rectángulo
entero, sin esquinas vacías.

Mismas fórmulas que `prototype-svelte/src/lib/projection.js` (Snyder, 1987,
§15). En el hemisferio sur se trabaja con la imagen especular del norte.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


EARTH_RADIUS_KM = 6371.0
# Paso y bordes de celda de la rejilla de 0,25° de ECMWF: centros en múltiplos
# de 0,25° y bordes medio paso más allá.
GRID_STEP = 0.25
GRID_WEST_EDGE = -180.125
GRID_EAST_EDGE = 179.875


def lcc(lon0: float, lat0: float, lat1: float, lat2: float):
    """Directa e inversa de la LCC tangente o secante en `lat1` y `lat2`."""
    signo = 1.0 if lat1 + lat2 > 0 else -1.0
    f1, f2, f0 = (math.radians(signo * lat) for lat in (lat1, lat2, lat0))
    t = lambda phi: np.tan(np.pi / 4 + np.asarray(phi) / 2)  # noqa: E731
    if abs(f1 - f2) < 1e-9:
        n = math.sin(f1)
    else:
        n = math.log(math.cos(f1) / math.cos(f2)) / math.log(float(t(f2) / t(f1)))
    F = math.cos(f1) * float(t(f1)) ** n / n
    rho0 = EARTH_RADIUS_KM * F / float(t(f0)) ** n

    def forward(lon, lat):
        phi = np.radians(signo * np.asarray(lat, dtype=float))
        dlon = (np.asarray(lon, dtype=float) - lon0 + 180.0) % 360.0 - 180.0
        theta = n * np.radians(dlon)
        rho = EARTH_RADIUS_KM * F / t(phi) ** n
        return rho * np.sin(theta), signo * (rho0 - rho * np.cos(theta))

    def inverse(x, y):
        x = np.asarray(x, dtype=float)
        y = signo * np.asarray(y, dtype=float)
        rho = np.hypot(x, rho0 - y)
        theta = np.arctan2(x, rho0 - y)
        phi = 2 * np.arctan((EARTH_RADIUS_KM * F / rho) ** (1 / n)) - np.pi / 2
        lon = (lon0 + np.degrees(theta / n) + 180.0) % 360.0 - 180.0
        return lon, signo * np.degrees(phi)

    return forward, inverse


def rectangle(projection: dict[str, Any]) -> tuple[float, float, float, float]:
    """Rectángulo del dominio en km de la proyección: (xmin, ymin, xmax, ymax)."""
    forward, _ = lcc(projection["lon0"], projection["lat0"], projection["lat1"], projection["lat2"])
    x0, y0 = (float(v) for v in forward(projection["lon0"], projection["lat0"]))
    ancho, alto = projection["width_km"] / 2, projection["height_km"] / 2
    return x0 - ancho, y0 - alto, x0 + ancho, y0 + alto


def covering_bounds(projection: dict[str, Any]) -> tuple[float, float, float, float]:
    """Recuadro de latitud y longitud que cubre el rectángulo LCC entero.

    El borde del rectángulo se recorre en lat/lon: los paralelos son arcos, y
    sus extremos —las esquinas de arriba en el norte— llegan más al polo y más
    lejos en longitud que el centro. Se redondea hacia fuera a bordes de celda
    y se deja una celda de margen para la interpolación del visor.
    """
    _, inverse = lcc(projection["lon0"], projection["lat0"], projection["lat1"], projection["lat2"])
    xmin, ymin, xmax, ymax = rectangle(projection)
    muestras = np.linspace(0.0, 1.0, 401)
    xs = np.concatenate([xmin + (xmax - xmin) * muestras, np.full(401, xmax),
                         xmin + (xmax - xmin) * muestras, np.full(401, xmin)])
    ys = np.concatenate([np.full(401, ymin), ymin + (ymax - ymin) * muestras,
                         np.full(401, ymax), ymin + (ymax - ymin) * muestras])
    lon, lat = inverse(xs, ys)
    # Longitudes relativas al meridiano central, para no partir el recuadro
    # si el rectángulo pasara por la línea de cambio de fecha.
    relativa = (lon - projection["lon0"] + 180.0) % 360.0 - 180.0
    oeste = projection["lon0"] + float(relativa.min())
    este = projection["lon0"] + float(relativa.max())
    sur, norte = float(lat.min()), float(lat.max())

    def hacia_fuera(valor: float, abajo: bool) -> float:
        celdas = (valor + 0.125) / GRID_STEP
        celdas = math.floor(celdas) - 1 if abajo else math.ceil(celdas) + 1
        return celdas * GRID_STEP - 0.125

    return (
        hacia_fuera(oeste, True), max(-90.125, hacia_fuera(sur, True)),
        hacia_fuera(este, False), min(90.125, hacia_fuera(norte, False)),
    )
