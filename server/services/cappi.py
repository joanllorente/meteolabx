"""CAPPI: reflectividad simulada a altitud constante sobre el nivel del mar.

AROME publica la reflectividad en niveles de presión, que suben y bajan con
la situación: 850 hPa puede estar a 1.300 m en una borrasca y a 1.550 m en un
anticiclón. Un CAPPI, como el de un radar, corta la columna a una altitud fija,
así que en cada celda se busca el par de niveles que la encierran —su altura
sale del geopotencial— y se interpola entre ellos.

La interpolación va en reflectividad lineal Z (mm⁶/m³), no en dBZ: la escala
logarítmica exagera los ecos débiles al mezclarlos con uno fuerte. Donde el
terreno supera la altitud pedida el corte queda bajo tierra y no hay valor,
igual que en el CAPPI de un radar.

Todo va nivel a nivel y sin retener más que el anterior: con la rejilla entera
de AROME son unos 6 MB por campo, y así el cálculo nunca pasa de una decena.
El coste está en descodificar los niveles, no aquí: el recorrido son un
puñado de operaciones vectoriales por nivel, milisegundos.
"""

from __future__ import annotations

import numpy as np


def reflectivity_from_rain_rate(rain_rate_mm_h: np.ndarray) -> np.ndarray:
    """Z lineal a partir de la lluvia equivalente de Marshall-Palmer (IP4).

    Z = 200·R^1,6. Lo que no llueve —cero, o los negativos minúsculos del
    empaquetado— es Z = 0, sin eco.
    """
    rate = np.asarray(rain_rate_mm_h, dtype=float)
    positive = np.where(rate > 0.0, rate, 0.0)
    return np.where(np.isfinite(rate), 200.0 * np.power(positive, 1.6), np.nan)


def reflectivity_from_dbz(dbz: np.ndarray) -> np.ndarray:
    """Z lineal a partir de dBZ, que es como la publica el WCS."""
    values = np.asarray(dbz, dtype=float)
    return np.where(np.isfinite(values), np.power(10.0, values / 10.0), np.nan)


class CappiAccumulator:
    """Recoge los niveles de abajo arriba y corta la columna a una altitud.

    ``add`` recibe cada nivel en orden de presión decreciente: su presión en
    hPa, la altura de la superficie isobárica sobre el nivel del mar en metros
    y la reflectividad lineal Z.
    """

    def __init__(self, altitude_m: float, surface_pressure_hpa: np.ndarray):
        self.altitude = float(altitude_m)
        self.surface = np.asarray(surface_pressure_hpa, dtype=float)
        self.result = np.full(self.surface.shape, np.nan)
        self.pending = np.ones(self.surface.shape, dtype=bool)
        self._previous: tuple[float, np.ndarray, np.ndarray] | None = None

    def add(self, pressure_hpa: float, height_m: np.ndarray, reflectivity_z: np.ndarray) -> None:
        pressure = float(pressure_hpa)
        height = np.asarray(height_m, dtype=float)
        # Un nivel bajo tierra lleva un valor extrapolado que no es de ningún
        # aire: su altura sí sirve para encerrar la altitud —el geopotencial
        # extrapolado sigue siendo monótono—, su reflectividad no.
        above_ground = pressure <= self.surface
        z = np.where(above_ground, np.asarray(reflectivity_z, dtype=float), np.nan)
        if self._previous is not None:
            self._bracket(*self._previous, pressure, height, z)
        self._previous = (pressure, height, z)

    def _bracket(self, lower_p, lower_h, lower_z, upper_p, upper_h, upper_z) -> None:
        with np.errstate(invalid="ignore", divide="ignore"):
            inside = (
                self.pending
                & (lower_h <= self.altitude)
                & (self.altitude < upper_h)
            )
            if not inside.any():
                return
            fraction = (self.altitude - lower_h) / (upper_h - lower_h)
            interpolated = lower_z + fraction * (upper_z - lower_z)
            # Si el nivel de abajo está bajo tierra pero la altitud no, el
            # único aire que queda por debajo es el de la capa superficial: se
            # toma el nivel de arriba, que es lo más cercano que hay.
            value = np.where(np.isfinite(lower_z), interpolated, upper_z)
            # La presión en la altitud pedida, en log-p como el geopotencial:
            # si queda por encima de la de superficie, el corte está bajo el
            # terreno.
            pressure = np.exp(
                np.log(lower_p) + fraction * (np.log(upper_p) - np.log(lower_p))
            )
            value = np.where(pressure <= self.surface, value, np.nan)
        self.result[inside] = value[inside]
        self.pending &= ~inside

    def dbz(self) -> np.ndarray:
        """El corte en dBZ. Por debajo de 0 dBZ —Z < 1— no hay eco: se da 0."""
        z = self.result
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(np.isfinite(z), 10.0 * np.log10(np.maximum(z, 1.0)), np.nan)
