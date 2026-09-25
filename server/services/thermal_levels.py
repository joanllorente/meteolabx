"""Altura de isotermas y cruces múltiples de perfiles AROME."""

from __future__ import annotations

import numpy as np


def wet_bulb_celsius(
    temperature_c: np.ndarray, dewpoint_c: np.ndarray, pressure_hpa: np.ndarray | float
) -> np.ndarray:
    """Ecuación psicrométrica resuelta en bloque, con presión local en hPa."""
    t = np.asarray(temperature_c, dtype=np.float64)
    td = np.minimum(np.asarray(dewpoint_c, dtype=np.float64), t)
    p = np.asarray(pressure_hpa, dtype=np.float64)
    gamma = 1004.0 * p / (0.622 * 2.5e6)
    actual = 6.112 * np.exp(17.67 * td / (td + 243.5))
    tw = (t + td) / 2.0
    for _ in range(6):
        saturation = 6.112 * np.exp(17.67 * tw / (tw + 243.5))
        derivative = saturation * 17.67 * 243.5 / (tw + 243.5) ** 2
        tw -= (saturation - gamma * (t - tw) - actual) / (derivative + gamma)
    return np.clip(tw, td, t)


class IsothermLevelAccumulator:
    """Recorre niveles de abajo arriba sin retener el perfil completo en RAM."""

    def __init__(
        self, surface_temperature_c: np.ndarray, surface_height_m: np.ndarray,
        threshold_c: float,
    ):
        self.threshold_c = threshold_c
        self.surface_temperature = np.asarray(surface_temperature_c, dtype=np.float64)
        self.surface_height = np.asarray(surface_height_m, dtype=np.float64)
        self.previous_temperature = self.surface_temperature
        self.previous_height = self.surface_height
        self.highest = np.full(self.previous_temperature.shape, np.nan, dtype=np.float32)
        self.crossings = np.zeros(self.previous_temperature.shape, dtype=np.uint8)
        at_threshold = np.isfinite(self.surface_temperature) & (self.surface_temperature == threshold_c)
        self.highest[at_threshold] = self.surface_height[at_threshold]
        self.crossings[at_threshold] = 1
        self.uncertain = np.zeros(self.previous_temperature.shape, dtype=bool)

    def add(self, temperature_c: np.ndarray, height_m: np.ndarray) -> None:
        current = np.asarray(temperature_c, dtype=np.float64)
        height = np.asarray(height_m, dtype=np.float64)
        lower = self.previous_temperature
        lower_height = self.previous_height
        valid = (
            np.isfinite(lower) & np.isfinite(current)
            & np.isfinite(lower_height) & np.isfinite(height)
            & (height > lower_height)
        )
        lower_delta = lower - self.threshold_c
        upper_delta = current - self.threshold_c
        crossing = valid & (
            ((lower_delta < 0) & (upper_delta >= 0))
            | ((lower_delta > 0) & (upper_delta <= 0))
        )
        fraction = np.zeros_like(current)
        np.divide(-lower_delta, upper_delta - lower_delta, out=fraction, where=crossing)
        candidate = lower_height + fraction * (height - lower_height)
        self.highest[crossing] = candidate[crossing]
        self.crossings[crossing] = np.minimum(self.crossings[crossing].astype(np.uint16) + 1, 255)
        # Un nivel ausente rompe la continuidad: no se inventa un cruce a
        # través del hueco. Los niveles bajo suelo tampoco reemplazan el ancla.
        above_ground = np.isfinite(height) & (height > lower_height)
        self.uncertain |= above_ground & (~np.isfinite(current) | ~np.isfinite(lower))
        self.previous_temperature = np.where(above_ground, current, lower)
        self.previous_height = np.where(above_ground, height, lower_height)

    def result(self, precipitation_mm: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        level = self.highest.copy()
        # Si la temperatura ya está bajo el umbral en superficie y no hay
        # cruce superior, la isoterma operativa alcanza el suelo del modelo.
        surface_frozen = np.isfinite(self.surface_temperature) & (self.crossings == 0)
        level = np.where(surface_frozen & (self.surface_temperature <= self.threshold_c),
                         self.surface_height, level)
        if precipitation_mm is not None:
            level = np.where(np.asarray(precipitation_mm) >= 0.05, level, np.nan)
        level = np.where(self.uncertain, np.nan, level).astype(np.float32)
        multiple = np.where(np.isfinite(level) & (self.crossings > 1), 1.0, np.nan).astype(np.float32)
        return level, multiple
