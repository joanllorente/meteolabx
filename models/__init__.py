"""
Módulo de modelos y cálculos.

Este paquete expone una API plana por compatibilidad, pero carga cada
submódulo bajo demanda para no penalizar el arranque con imports pesados
como `models.trends` (que depende de pandas).
"""
from importlib import import_module


_NAME_TO_MODULE = {
    # Thermodynamics
    "e_s": "models.thermodynamics",
    "vapor_pressure": "models.thermodynamics",
    "dewpoint_from_vapor_pressure": "models.thermodynamics",
    "mixing_ratio": "models.thermodynamics",
    "specific_humidity": "models.thermodynamics",
    "absolute_humidity": "models.thermodynamics",
    "potential_temperature": "models.thermodynamics",
    "virtual_temperature": "models.thermodynamics",
    "equivalent_temperature": "models.thermodynamics",
    "equivalent_potential_temperature": "models.thermodynamics",
    "wet_bulb_celsius": "models.thermodynamics",
    "wet_bulb_celsius_stull": "models.thermodynamics",
    "wet_bulb_psychrometric": "models.thermodynamics",
    "msl_to_absolute": "models.thermodynamics",
    "absolute_to_msl": "models.thermodynamics",
    "air_density": "models.thermodynamics",
    "lcl_height": "models.thermodynamics",
    "apparent_temperature": "models.thermodynamics",
    "heat_index_rothfusz": "models.thermodynamics",
    "theta_celsius": "models.thermodynamics",
    "Tv_celsius": "models.thermodynamics",
    "Te_celsius": "models.thermodynamics",
    # Radiation
    "penman_monteith_et0": "models.radiation",
    "sky_clarity_label": "models.radiation",
    "uv_index_label": "models.radiation",
    "water_balance": "models.radiation",
    "water_balance_label": "models.radiation",
    # Trends
    "saturation_pressure": "models.trends",
    "vapor_pressure_trends": "models.trends",
    "specific_humidity_trends": "models.trends",
    "potential_temperature_trends": "models.trends",
    "equivalent_potential_temperature_trends": "models.trends",
    "calculate_trend": "models.trends",
}

__all__ = list(_NAME_TO_MODULE.keys())


def __getattr__(name: str):
    module_name = _NAME_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module 'models' has no attribute {name!r}")
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals().keys()) | set(__all__))
