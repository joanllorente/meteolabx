"""
Módulo de modelos y cálculos
"""
from .thermodynamics import (
    e_s, vapor_pressure, dewpoint_from_vapor_pressure,
    mixing_ratio, specific_humidity, absolute_humidity,
    potential_temperature, virtual_temperature, equivalent_temperature, equivalent_potential_temperature,
    wet_bulb_celsius, wet_bulb_celsius_stull,
    msl_to_absolute, absolute_to_msl, air_density, lcl_height,
    # Aliases para compatibilidad
    theta_celsius, Tv_celsius, Te_celsius
)

from .radiation import (
    penman_monteith_et0, sky_clarity_label,
    uv_index_label, water_balance, water_balance_label
)

from .trends import (
    saturation_pressure, vapor_pressure as vapor_pressure_trends,
    specific_humidity as specific_humidity_trends,
    potential_temperature as potential_temperature_trends,
    equivalent_potential_temperature as equivalent_potential_temperature_trends,
    calculate_trend
)

__all__ = [
    # Thermodynamics
    'e_s', 'vapor_pressure', 'dewpoint_from_vapor_pressure',
    'mixing_ratio', 'specific_humidity', 'absolute_humidity',
    'potential_temperature', 'virtual_temperature', 'equivalent_temperature', 'equivalent_potential_temperature',
    'wet_bulb_celsius', 'wet_bulb_celsius_stull',
    'msl_to_absolute', 'absolute_to_msl', 'air_density', 'lcl_height',
    'theta_celsius', 'Tv_celsius', 'Te_celsius',  # Aliases
    # Radiation
    'penman_monteith_et0', 'sky_clarity_label',
    'uv_index_label', 'water_balance', 'water_balance_label',
    # Trends
    'saturation_pressure', 'calculate_trend'
]
