"""Diagnósticos convectivos vectorizados sobre perfiles isobáricos AROME.

Las rutinas usan teoría de parcela pseudoadiabática sin arrastre. El objetivo es
mantener MUCAPE, MULI, la capa efectiva y SHIP bajo una misma definición física,
sin confundirlos con los campos ECAPE nativos del modelo.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np

try:  # Dependencia oficial fijada en requirements.txt para producción.
    from sharppy.sharptab import params as sharppy_params
    from sharppy.sharptab import thermo as sharppy_thermo
    from sharppy.sharptab import utils as sharppy_utils
except ImportError:  # Permite ejecutar tests mínimos sin el extra meteorológico.
    sharppy_params = None
    sharppy_thermo = None
    sharppy_utils = None


RD = 287.05
RV = 461.5
CP_D = 1004.0
EPSILON = RD / RV
# Desviación de Bunkers respecto al viento medio de la capa 0-6 km.
DEVIATION_MS = 7.5
GRAVITY = 9.80665
KAPPA = RD / CP_D


@dataclass(frozen=True)
class ParcelDiagnostics:
    cape: np.ndarray
    cin: np.ndarray
    li500: np.ndarray
    equilibrium_height_m: np.ndarray
    equilibrium_pressure_hpa: np.ndarray
    # Nivel de convección libre: el primero, subiendo, en el que la parcela
    # gana flotabilidad. Es hasta donde tiene que llegar el ascenso forzado
    # para que la convección se dispare sola.
    lfc_height_m: np.ndarray
    lfc_pressure_hpa: np.ndarray


@dataclass(frozen=True)
class ConvectiveDiagnostics:
    mucape: np.ndarray
    muli: np.ndarray
    mlcape: np.ndarray
    mlli: np.ndarray
    sbcape: np.ndarray
    sbli: np.ndarray
    dcape: np.ndarray
    mu_lcl_pressure_hpa: np.ndarray
    mu_equilibrium_pressure_hpa: np.ndarray
    ml_lcl_pressure_hpa: np.ndarray
    ml_equilibrium_pressure_hpa: np.ndarray
    mu_mixing_ratio_gkg: np.ndarray
    mu_equilibrium_height_m: np.ndarray
    effective_base_height_m: np.ndarray
    # Altura AGL del nivel de convección libre de la parcela de capa mezclada:
    # hasta dónde tiene que llegar el ascenso forzado para que se dispare.
    ml_lfc_height_m: np.ndarray


def saturation_vapor_pressure_hpa(temperature_k: np.ndarray) -> np.ndarray:
    temperature_c = np.asarray(temperature_k, dtype=float) - 273.15
    return 6.112 * np.exp(17.67 * temperature_c / (temperature_c + 243.5))


def mixing_ratio_kgkg(pressure_hpa: np.ndarray, dewpoint_k: np.ndarray) -> np.ndarray:
    pressure = np.asarray(pressure_hpa, dtype=float)
    vapor_pressure = saturation_vapor_pressure_hpa(dewpoint_k)
    return np.divide(
        EPSILON * vapor_pressure,
        pressure - vapor_pressure,
        out=np.full(np.broadcast_shapes(pressure.shape, vapor_pressure.shape), np.nan),
        where=np.isfinite(pressure) & np.isfinite(vapor_pressure) & (pressure > vapor_pressure),
    )


def dewpoint_from_mixing_ratio_k(
    pressure_hpa: np.ndarray,
    mixing_ratio: np.ndarray,
) -> np.ndarray:
    """Invierte la razón de mezcla mediante la misma fórmula de Bolton."""
    pressure = np.asarray(pressure_hpa, dtype=float)
    ratio = np.maximum(np.asarray(mixing_ratio, dtype=float), 0.0)
    vapor_pressure = np.divide(
        ratio * pressure,
        EPSILON + ratio,
        out=np.full(np.broadcast_shapes(pressure.shape, ratio.shape), np.nan),
        where=np.isfinite(pressure) & np.isfinite(ratio),
    )
    logarithm = np.log(np.maximum(vapor_pressure, 1e-6) / 6.112)
    dewpoint_c = 243.5 * logarithm / (17.67 - logarithm)
    return dewpoint_c + 273.15


def virtual_temperature_k(temperature_k: np.ndarray, mixing_ratio: np.ndarray) -> np.ndarray:
    return np.asarray(temperature_k, dtype=float) * (
        1.0 + np.asarray(mixing_ratio, dtype=float) / EPSILON
    ) / (1.0 + np.asarray(mixing_ratio, dtype=float))


def lcl_temperature_k(temperature_k: np.ndarray, dewpoint_k: np.ndarray) -> np.ndarray:
    temperature = np.asarray(temperature_k, dtype=float)
    dewpoint = np.minimum(np.asarray(dewpoint_k, dtype=float), temperature)
    return 1.0 / (
        1.0 / (dewpoint - 56.0) + np.log(temperature / dewpoint) / 800.0
    ) + 56.0


def equivalent_potential_temperature_k(
    pressure_hpa: np.ndarray,
    temperature_k: np.ndarray,
    dewpoint_k: np.ndarray,
) -> np.ndarray:
    """Aproximación de Bolton (1980) para seleccionar y elevar parcelas."""
    pressure = np.asarray(pressure_hpa, dtype=float)
    temperature = np.asarray(temperature_k, dtype=float)
    dewpoint = np.minimum(np.asarray(dewpoint_k, dtype=float), temperature)
    vapor_pressure = saturation_vapor_pressure_hpa(dewpoint)
    ratio = mixing_ratio_kgkg(pressure, dewpoint)
    tlcl = lcl_temperature_k(temperature, dewpoint)
    dry_theta = temperature * np.power(
        1000.0 / np.maximum(pressure - vapor_pressure, 1.0),
        0.2854 * (1.0 - 0.28 * ratio),
    )
    return dry_theta * np.exp(
        (3036.0 / tlcl - 1.78) * ratio * (1.0 + 0.448 * ratio)
    )


def equivalent_potential_temperature_metpy_k(
    pressure_hpa: np.ndarray,
    temperature_k: np.ndarray,
    dewpoint_k: np.ndarray,
) -> np.ndarray:
    """Theta-e de Bolton (1980) delegada en MetPy, en kelvin.

    Existe aparte de :func:`equivalent_potential_temperature_k` a propósito.
    Aquella mezcla dos variantes de Bolton —toma el exponencial de una y la
    theta seca de otra— y se desvía de MetPy entre 0,02 y 0,19 K en casos
    corrientes. La diferencia es pequeña, pero es una función compartida por
    los diagnósticos convectivos: cambiarla movería MUCAPE, MULI, la capa
    efectiva y SHIP a la vez, así que se revisa por separado y con calma. Un
    mapa nuevo no tiene por qué heredar esa deuda.

    El rocío se recorta a la temperatura: el modelo publica de vez en cuando
    sobresaturaciones numéricas de centésimas y MetPy las propaga.
    """
    import metpy.calc as mpcalc
    from metpy.units import units

    temperature = np.asarray(temperature_k, dtype=float)
    dewpoint = np.minimum(np.asarray(dewpoint_k, dtype=float), temperature)
    pressure = np.asarray(pressure_hpa, dtype=float)
    theta_e = mpcalc.equivalent_potential_temperature(
        pressure * units.hPa,
        temperature * units.kelvin,
        dewpoint * units.kelvin,
    )
    return np.asarray(theta_e.to("K").magnitude, dtype=float)


def _saturated_theta_e_k(pressure_hpa: np.ndarray, temperature_k: np.ndarray) -> np.ndarray:
    return equivalent_potential_temperature_k(pressure_hpa, temperature_k, temperature_k)


_THETA_E_TOLERANCE = 1e-7


def _saturated_temperature_from_theta_e(
    pressure_hpa: np.ndarray,
    theta_e_k: np.ndarray,
    initial_k: np.ndarray,
) -> np.ndarray:
    """Invierte theta-e saturada mediante Newton vectorizado.

    Solo se sigue iterando sobre los puntos que aún no han convergido. Newton
    es cuadrático y sobre una rejilla AROME el 99,8 % converge en cuatro
    pasadas, así que iterar la malla entera nueve veces gastaba la mayor parte
    del tiempo en recalcular exponenciales de puntos ya resueltos.
    """
    pressure = np.asarray(pressure_hpa, dtype=float)
    target_log = np.log(np.maximum(np.asarray(theta_e_k, dtype=float), 1.0))
    temperature = np.clip(np.asarray(initial_k, dtype=float), 170.0, 380.0)

    shape = temperature.shape
    values = temperature.ravel().copy()
    flat_pressure = np.broadcast_to(pressure, shape).ravel()
    flat_target = np.broadcast_to(target_log, shape).ravel()
    pending = np.arange(values.size)

    for _ in range(9):
        point_pressure = flat_pressure[pending]
        point_temperature = values[pending]
        residual = (
            np.log(_saturated_theta_e_k(point_pressure, point_temperature))
            - flat_target[pending]
        )
        # Un residuo no finito no se corrige iterando: se deja como está.
        improvable = np.isfinite(residual) & (np.abs(residual) >= _THETA_E_TOLERANCE)
        if not improvable.any():
            break
        pending = pending[improvable]
        point_pressure = point_pressure[improvable]
        point_temperature = point_temperature[improvable]
        residual = residual[improvable]

        plus = _saturated_theta_e_k(point_pressure, point_temperature + 0.08)
        minus = _saturated_theta_e_k(point_pressure, point_temperature - 0.08)
        derivative = (np.log(plus) - np.log(minus)) / 0.16
        correction = np.divide(
            residual,
            derivative,
            out=np.zeros_like(point_temperature),
            where=np.isfinite(derivative) & (np.abs(derivative) > 1e-8),
        )
        values[pending] = np.clip(point_temperature - correction, 170.0, 380.0)

    return values.reshape(shape)


def parcel_temperature_profile_k(
    pressure_hpa: np.ndarray,
    parcel_pressure_hpa: np.ndarray,
    parcel_temperature_k: np.ndarray,
    parcel_dewpoint_k: np.ndarray,
) -> np.ndarray:
    pressure = np.asarray(pressure_hpa, dtype=float)
    p0 = np.asarray(parcel_pressure_hpa, dtype=float)
    t0 = np.asarray(parcel_temperature_k, dtype=float)
    td0 = np.minimum(np.asarray(parcel_dewpoint_k, dtype=float), t0)
    tlcl = lcl_temperature_k(t0, td0)
    plcl = p0 * np.power(tlcl / t0, 1.0 / KAPPA)
    theta_e = equivalent_potential_temperature_k(p0, t0, td0)

    dry = t0[None, ...] * np.power(
        pressure / p0[None, ...], KAPPA
    )
    moist_guess = tlcl[None, ...] * np.power(
        pressure / plcl[None, ...], 0.16
    )
    moist = _saturated_temperature_from_theta_e(
        pressure,
        theta_e[None, ...],
        moist_guess,
    )
    result = np.where(pressure >= plcl[None, ...], dry, moist)
    valid = (
        np.isfinite(pressure)
        & np.isfinite(t0)[None, ...]
        & np.isfinite(td0)[None, ...]
        & (pressure <= p0[None, ...] + 0.5)
    )
    return np.where(valid, result, np.nan)


def _level_of_free_convection(
    pressure_hpa: np.ndarray,
    height_m: np.ndarray,
    buoyancy_ms2: np.ndarray,
    lcl_pressure_hpa: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Interpola el primer cruce de flotabilidad positiva por encima del LCL.

    El recorrido nivel a nivel evita crear varias matrices 3-D auxiliares, que
    penalizarían mucho el pico de memoria sobre la rejilla AROME completa.
    """
    pressure = np.asarray(pressure_hpa, dtype=float)
    height = np.asarray(height_m, dtype=float)
    buoyancy = np.asarray(buoyancy_ms2, dtype=float)
    lcl_pressure = np.asarray(lcl_pressure_hpa, dtype=float)
    shape = pressure.shape[1:]

    lcl_height = np.full(shape, np.nan)
    for index in range(pressure.shape[0] - 1):
        p0 = pressure[index]
        p1 = pressure[index + 1]
        z0 = height[index]
        z1 = height[index + 1]
        valid = (
            ~np.isfinite(lcl_height)
            & np.isfinite(p0)
            & np.isfinite(p1)
            & np.isfinite(z0)
            & np.isfinite(z1)
            & np.isfinite(lcl_pressure)
            & (p0 > p1)
            & (z1 > z0)
            & (p0 >= lcl_pressure)
            & (p1 <= lcl_pressure)
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            fraction = np.divide(
                np.log(p0 / lcl_pressure),
                np.log(p0 / p1),
                out=np.zeros(shape),
                where=valid,
            )
        candidate = z0 + np.clip(fraction, 0.0, 1.0) * (z1 - z0)
        lcl_height = np.where(valid, candidate, lcl_height)

    lfc_height = np.full(shape, np.nan)
    lfc_pressure = np.full(shape, np.nan)
    for index in range(pressure.shape[0] - 1):
        p0 = pressure[index]
        p1 = pressure[index + 1]
        z0 = height[index]
        z1 = height[index + 1]
        b0 = buoyancy[index]
        b1 = buoyancy[index + 1]
        valid = (
            ~np.isfinite(lfc_height)
            & np.isfinite(lcl_height)
            & np.isfinite(p0)
            & np.isfinite(p1)
            & np.isfinite(z0)
            & np.isfinite(z1)
            & np.isfinite(b0)
            & np.isfinite(b1)
            & (p0 > p1)
            & (z1 > z0)
            & (z1 >= lcl_height)
        )
        start_height = np.maximum(z0, lcl_height)
        start_fraction = np.divide(
            start_height - z0,
            z1 - z0,
            out=np.zeros(shape),
            where=valid,
        )
        start_buoyancy = b0 + start_fraction * (b1 - b0)
        already_positive = valid & (start_buoyancy > 0.0)
        crosses_zero = valid & (start_buoyancy <= 0.0) & (b1 > 0.0)
        crossing_fraction = np.divide(
            -start_buoyancy,
            b1 - start_buoyancy,
            out=np.zeros(shape),
            where=crosses_zero,
        )
        candidate_height = np.where(
            already_positive,
            start_height,
            start_height + crossing_fraction * (z1 - start_height),
        )
        found = already_positive | crosses_zero
        height_fraction = np.divide(
            candidate_height - z0,
            z1 - z0,
            out=np.zeros(shape),
            where=found,
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            candidate_pressure = p0 * np.power(p1 / p0, height_fraction)
        lfc_height = np.where(found, candidate_height, lfc_height)
        lfc_pressure = np.where(found, candidate_pressure, lfc_pressure)

    return lfc_height, lfc_pressure


def parcel_diagnostics(
    pressure_hpa: np.ndarray,
    environmental_temperature_k: np.ndarray,
    environmental_dewpoint_k: np.ndarray,
    height_m: np.ndarray,
    parcel_pressure_hpa: np.ndarray,
    parcel_temperature_k: np.ndarray,
    parcel_dewpoint_k: np.ndarray,
) -> ParcelDiagnostics:
    pressure = np.asarray(pressure_hpa, dtype=float)
    env_temperature = np.asarray(environmental_temperature_k, dtype=float)
    env_dewpoint = np.minimum(np.asarray(environmental_dewpoint_k, dtype=float), env_temperature)
    height = np.asarray(height_m, dtype=float)
    parcel_temperature = parcel_temperature_profile_k(
        pressure,
        parcel_pressure_hpa,
        parcel_temperature_k,
        parcel_dewpoint_k,
    )

    env_ratio = mixing_ratio_kgkg(pressure, env_dewpoint)
    parcel_ratio_initial = mixing_ratio_kgkg(parcel_pressure_hpa, parcel_dewpoint_k)
    tlcl = lcl_temperature_k(parcel_temperature_k, parcel_dewpoint_k)
    plcl = parcel_pressure_hpa * np.power(tlcl / parcel_temperature_k, 1.0 / KAPPA)
    saturated_ratio = mixing_ratio_kgkg(pressure, parcel_temperature)
    parcel_ratio = np.where(
        pressure >= plcl[None, ...],
        parcel_ratio_initial[None, ...],
        saturated_ratio,
    )
    env_virtual = virtual_temperature_k(env_temperature, env_ratio)
    parcel_virtual = virtual_temperature_k(parcel_temperature, parcel_ratio)
    buoyancy = GRAVITY * (parcel_virtual - env_virtual) / env_virtual

    delta_z = np.diff(height, axis=0)
    layer_energy = 0.5 * (buoyancy[:-1] + buoyancy[1:]) * delta_z
    valid_layer = (
        np.isfinite(layer_energy)
        & np.isfinite(delta_z)
        & (delta_z > 0)
    )
    positive = valid_layer & (layer_energy > 0)
    before_lfc = np.cumsum(positive, axis=0) == 0
    cape = np.sum(np.where(positive, layer_energy, 0.0), axis=0)
    cin = np.sum(
        np.where(valid_layer & before_lfc & (layer_energy < 0), layer_energy, 0.0),
        axis=0,
    )
    has_profile = np.any(valid_layer, axis=0)
    cape = np.where(has_profile, cape, np.nan)
    cin = np.where(has_profile, cin, np.nan)

    has_positive = np.any(positive, axis=0)
    positive_top = np.where(positive, height[1:], -np.inf)
    equilibrium_height = np.max(positive_top, axis=0)
    equilibrium_height = np.where(has_positive, equilibrium_height, np.nan)
    positive_top_pressure = np.where(positive, pressure[1:], np.inf)
    equilibrium_pressure = np.min(positive_top_pressure, axis=0)
    equilibrium_pressure = np.where(has_positive, equilibrium_pressure, np.nan)

    # El NCL/LFC no coincide normalmente con un nivel isobárico del modelo:
    # es el primer cruce B=0 por encima del LCL y se interpola dentro de la capa.
    lfc_height, lfc_pressure = _level_of_free_convection(
        pressure, height, buoyancy, plcl
    )

    pressure_1d = pressure[:, 0, 0]
    index_500 = int(np.nanargmin(np.abs(pressure_1d - 500.0)))
    li500 = env_temperature[index_500] - parcel_temperature[index_500]
    li500 = np.where(np.isfinite(parcel_temperature[index_500]), li500, np.nan)
    return ParcelDiagnostics(
        cape,
        cin,
        li500,
        equilibrium_height,
        equilibrium_pressure,
        lfc_height,
        lfc_pressure,
    )


def hypsometric_height_profile_m(
    pressure_hpa: np.ndarray,
    temperature_k: np.ndarray,
    dewpoint_k: np.ndarray,
    surface_height_m: np.ndarray,
) -> np.ndarray:
    """Integra alturas geopotenciales desde la superficie con la ecuación hipsométrica."""
    pressure = np.asarray(pressure_hpa, dtype=float)
    temperature = np.asarray(temperature_k, dtype=float)
    dewpoint = np.asarray(dewpoint_k, dtype=float)
    ratio = mixing_ratio_kgkg(pressure, dewpoint)
    virtual = virtual_temperature_k(temperature, ratio)
    result = np.full_like(temperature, np.nan)
    result[0] = surface_height_m
    for index in range(1, pressure.shape[0]):
        p_lower = pressure[index - 1]
        p_upper = pressure[index]
        mean_virtual = 0.5 * (virtual[index - 1] + virtual[index])
        delta = RD * mean_virtual / GRAVITY * np.log(p_lower / p_upper)
        duplicate_surface = (
            np.isfinite(result[index - 1])
            & np.isfinite(p_lower)
            & np.isfinite(p_upper)
            & (np.abs(p_lower - p_upper) <= 0.5)
        )
        valid = (
            np.isfinite(result[index - 1])
            & np.isfinite(delta)
            & (p_lower > p_upper)
        )
        result[index] = np.where(
            duplicate_surface,
            result[index - 1],
            np.where(valid, result[index - 1] + delta, np.nan),
        )
    return result


def diagnose_convection(
    pressure_hpa: np.ndarray,
    temperature_k: np.ndarray,
    dewpoint_k: np.ndarray,
    height_m: np.ndarray,
    include_dcape: bool = True,
) -> ConvectiveDiagnostics:
    """Calcula MU parcel y base efectiva para una matriz completa de perfiles."""
    pressure = np.asarray(pressure_hpa, dtype=float)
    temperature = np.asarray(temperature_k, dtype=float)
    dewpoint = np.asarray(dewpoint_k, dtype=float)
    height = np.asarray(height_m, dtype=float)
    surface_pressure = pressure[0]

    theta_e = equivalent_potential_temperature_k(pressure, temperature, dewpoint)
    candidate = (
        np.isfinite(theta_e)
        & (pressure <= surface_pressure[None, ...] + 0.5)
        & (pressure >= surface_pressure[None, ...] - 300.0)
    )
    score = np.where(candidate, theta_e, -np.inf)
    mu_index = np.argmax(score, axis=0)
    any_mu = np.any(candidate, axis=0)

    def selected(values: np.ndarray) -> np.ndarray:
        return np.take_along_axis(values, mu_index[None, ...], axis=0)[0]

    mu_pressure = np.where(any_mu, selected(pressure), np.nan)
    mu_temperature = np.where(any_mu, selected(temperature), np.nan)
    mu_dewpoint = np.where(any_mu, selected(dewpoint), np.nan)
    mu = parcel_diagnostics(
        pressure,
        temperature,
        dewpoint,
        height,
        mu_pressure,
        mu_temperature,
        mu_dewpoint,
    )
    mu_lcl_temperature = lcl_temperature_k(mu_temperature, mu_dewpoint)
    mu_lcl_pressure = mu_pressure * np.power(
        mu_lcl_temperature / mu_temperature,
        1.0 / KAPPA,
    )
    mu_ratio = mixing_ratio_kgkg(mu_pressure, mu_dewpoint) * 1000.0

    # Parcela superficial: las condiciones de 2 m se insertan como primer
    # nivel del perfil, por lo que SB comparte exactamente ese origen.
    surface = parcel_diagnostics(
        pressure,
        temperature,
        dewpoint,
        height,
        surface_pressure,
        temperature[0],
        dewpoint[0],
    )

    # Parcela de capa mezclada: promedio de theta y razón de mezcla en los
    # 100 hPa inferiores, reconstruido a la presión de superficie. Esta es la
    # definición convencional de ML y no debe confundirse con ML-ECAPE nativo.
    mixed_temperature, mixed_dewpoint = _mixed_layer_parcel_properties(
        pressure, temperature, dewpoint
    )
    mixed = parcel_diagnostics(
        pressure,
        temperature,
        dewpoint,
        height,
        surface_pressure,
        mixed_temperature,
        mixed_dewpoint,
    )
    mixed_lcl_temperature = lcl_temperature_k(mixed_temperature, mixed_dewpoint)
    mixed_lcl_pressure = surface_pressure * np.power(
        mixed_lcl_temperature / mixed_temperature,
        1.0 / KAPPA,
    )

    # DCAPE es el diagnóstico más caro y el único que exige el punto de rocío
    # exacto del modelo, así que se puede pedir aparte y no retrasar al resto.
    dcape = (
        downdraft_cape(pressure, temperature, dewpoint, height)
        if include_dcape
        else np.full(pressure.shape[1:], np.nan)
    )

    effective_base = np.full(surface_pressure.shape, np.nan)
    unresolved = np.isfinite(surface_pressure)
    for index in range(pressure.shape[0]):
        parcel_pressure = pressure[index]
        eligible_origin = (
            unresolved
            & np.isfinite(parcel_pressure)
            & (parcel_pressure <= surface_pressure + 0.5)
            & (parcel_pressure >= 500.0)
        )
        if not np.any(eligible_origin):
            continue
        parcel = parcel_diagnostics(
            pressure,
            temperature,
            dewpoint,
            height,
            np.where(eligible_origin, parcel_pressure, np.nan),
            np.where(eligible_origin, temperature[index], np.nan),
            np.where(eligible_origin, dewpoint[index], np.nan),
        )
        qualifies = eligible_origin & (parcel.cape >= 100.0) & (parcel.cin >= -250.0)
        effective_base = np.where(qualifies, height[index], effective_base)
        unresolved &= ~qualifies

    return ConvectiveDiagnostics(
        mucape=mu.cape,
        muli=mu.li500,
        mlcape=mixed.cape,
        mlli=mixed.li500,
        sbcape=surface.cape,
        sbli=surface.li500,
        dcape=dcape,
        mu_lcl_pressure_hpa=mu_lcl_pressure,
        mu_equilibrium_pressure_hpa=mu.equilibrium_pressure_hpa,
        ml_lcl_pressure_hpa=mixed_lcl_pressure,
        ml_equilibrium_pressure_hpa=mixed.equilibrium_pressure_hpa,
        mu_mixing_ratio_gkg=mu_ratio,
        mu_equilibrium_height_m=mu.equilibrium_height_m,
        effective_base_height_m=effective_base,
        ml_lfc_height_m=mixed.lfc_height_m - height[0],
    )


def pressure_weighted_layer_mean(
    pressure_hpa: np.ndarray,
    values: np.ndarray,
    bottom_pressure_hpa: np.ndarray,
    top_pressure_hpa: np.ndarray,
) -> np.ndarray:
    """Media de un campo siguiendo ``∫ value dp / (p_bottom-p_top)``."""
    pressure = np.asarray(pressure_hpa, dtype=float)
    data = np.asarray(values, dtype=float)
    bottom = np.asarray(bottom_pressure_hpa, dtype=float)
    top = np.asarray(top_pressure_hpa, dtype=float)
    integral = np.zeros(bottom.shape)
    for index in range(pressure.shape[0] - 1):
        layer_bottom = pressure[index]
        layer_top = pressure[index + 1]
        overlap_bottom = np.minimum(layer_bottom, bottom)
        overlap_top = np.maximum(layer_top, top)
        layer_depth = layer_bottom - layer_top
        valid = (
            np.isfinite(layer_bottom)
            & np.isfinite(layer_top)
            & np.isfinite(data[index])
            & np.isfinite(data[index + 1])
            & (layer_depth > 0.0)
            & (overlap_bottom > overlap_top)
        )
        bottom_fraction = np.divide(
            layer_bottom - overlap_bottom,
            layer_depth,
            out=np.zeros_like(bottom),
            where=valid,
        )
        top_fraction = np.divide(
            layer_bottom - overlap_top,
            layer_depth,
            out=np.zeros_like(bottom),
            where=valid,
        )
        value_bottom = data[index] + bottom_fraction * (
            data[index + 1] - data[index]
        )
        value_top = data[index] + top_fraction * (
            data[index + 1] - data[index]
        )
        contribution = 0.5 * (value_bottom + value_top) * (
            overlap_bottom - overlap_top
        )
        integral = np.where(valid, integral + contribution, integral)
    depth = bottom - top
    return np.divide(
        integral,
        depth,
        out=np.full(bottom.shape, np.nan),
        where=np.isfinite(depth) & (depth > 0.0),
    )


def _mixed_layer_parcel_properties(
    pressure_hpa: np.ndarray,
    temperature_k: np.ndarray,
    dewpoint_k: np.ndarray,
    depth_hpa: float = 100.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Reconstruye en superficie la parcela media de una capa en presión."""
    pressure = np.asarray(pressure_hpa, dtype=float)
    temperature = np.asarray(temperature_k, dtype=float)
    dewpoint = np.asarray(dewpoint_k, dtype=float)
    surface_pressure = pressure[0]
    layer_top = surface_pressure - depth_hpa
    potential_temperature = temperature * np.power(1000.0 / pressure, KAPPA)
    ratio = mixing_ratio_kgkg(pressure, dewpoint)
    mean_theta = pressure_weighted_layer_mean(
        pressure, potential_temperature, surface_pressure, layer_top
    )
    mean_ratio = pressure_weighted_layer_mean(
        pressure, ratio, surface_pressure, layer_top
    )
    mixed_temperature = mean_theta * np.power(surface_pressure / 1000.0, KAPPA)
    mixed_dewpoint = np.minimum(
        dewpoint_from_mixing_ratio_k(surface_pressure, mean_ratio),
        mixed_temperature,
    )
    return mixed_temperature, mixed_dewpoint


def downdraft_cape(
    pressure_hpa: np.ndarray,
    environmental_temperature_k: np.ndarray,
    environmental_dewpoint_k: np.ndarray,
    height_m: np.ndarray,
) -> np.ndarray:
    """DCAPE según el procedimiento SPC/SHARPpy.

    Busca, dentro de los 400 hPa inferiores, la capa móvil de 100 hPa con
    menor theta-e media. La parcela parte del centro de esa capa, se satura a
    temperatura de bulbo húmedo y desciende pseudoadiabáticamente. Para
    reproducir SHARPpy, la integral usa temperatura ordinaria, no virtual.
    """
    pressure = np.asarray(pressure_hpa, dtype=float)
    temperature = np.asarray(environmental_temperature_k, dtype=float)
    dewpoint = np.minimum(np.asarray(environmental_dewpoint_k, dtype=float), temperature)
    height = np.asarray(height_m, dtype=float)
    surface_pressure = pressure[0]

    # Los logaritmos de presión no dependen del campo interpolado ni del
    # objetivo, así que se calculan una sola vez: antes se evaluaban dos por
    # nivel y por llamada sobre la malla completa, y eran el grueso del coste.
    log_pressure = np.log(np.maximum(pressure, 1e-6))

    def interpolate_to_targets(values: np.ndarray, targets: np.ndarray) -> np.ndarray:
        squeeze = targets.ndim == surface_pressure.ndim
        target_values = targets[None, ...] if squeeze else targets
        result = np.full(target_values.shape, np.nan)
        log_targets = np.log(np.maximum(target_values, 1e-6))
        for index in range(pressure.shape[0] - 1):
            p_lower = pressure[index]
            p_upper = pressure[index + 1]
            valid_layer = (
                np.isfinite(p_lower)
                & np.isfinite(p_upper)
                & np.isfinite(values[index])
                & np.isfinite(values[index + 1])
                & (p_lower > p_upper + 0.1)
            )
            within = (
                ~np.isfinite(result)
                & valid_layer[None, ...]
                & (target_values <= p_lower[None, ...])
                & (target_values >= p_upper[None, ...])
            )
            if not within.any():
                continue
            log_lower = log_pressure[index][None, ...]
            log_fraction = np.divide(
                log_targets - log_lower,
                log_pressure[index + 1][None, ...] - log_lower,
                out=np.zeros_like(target_values),
                where=within,
            )
            interpolated = values[index][None, ...] + log_fraction * (
                values[index + 1] - values[index]
            )[None, ...]
            np.copyto(result, interpolated, where=within)
        surface_match = np.isclose(target_values, surface_pressure[None, ...], atol=0.5)
        np.copyto(result, np.broadcast_to(values[0][None, ...], result.shape), where=surface_match)
        return result[0] if squeeze else result

    # SHARPpy solo prueba capas cuya base coincide con un nivel del perfil.
    # Dentro de cada capa interpolamos cada 5 hPa para conservar rendimiento
    # vectorial; sus funciones termo oficiales calculan theta-e y Tw.
    offsets = np.arange(0.0, 100.1, 5.0)[:, None, None]
    minimum_mean = np.full(surface_pressure.shape, np.inf)
    source_base_index = np.full(surface_pressure.shape, -1, dtype=np.int16)
    source_base_pressure = np.full(surface_pressure.shape, np.nan)
    for index in range(pressure.shape[0]):
        base_pressure = pressure[index]
        eligible = (
            np.isfinite(base_pressure)
            & (base_pressure <= surface_pressure + 0.5)
            & (base_pressure >= surface_pressure - 400.0)
        )
        targets = base_pressure[None, ...] - offsets
        sampled_temperature = interpolate_to_targets(temperature, targets)
        sampled_dewpoint = np.minimum(
            interpolate_to_targets(dewpoint, targets),
            sampled_temperature,
        )
        # params.mean_thetae es escalar y thermo.thetae mezcla una presión
        # final escalar con arrays, caso que SHARPpy 1.4 no vectoriza. Usamos
        # Bolton aquí y contrastamos el resultado con params.dcape por muestra.
        sampled_theta_e = equivalent_potential_temperature_k(
            targets,
            sampled_temperature,
            sampled_dewpoint,
        )
        valid = eligible & np.all(np.isfinite(sampled_theta_e), axis=0)
        layer_mean = (
            0.5 * sampled_theta_e[0]
            + np.sum(sampled_theta_e[1:-1], axis=0)
            + 0.5 * sampled_theta_e[-1]
        ) / (sampled_theta_e.shape[0] - 1)
        better = valid & (layer_mean < minimum_mean)
        minimum_mean = np.where(better, layer_mean, minimum_mean)
        source_base_index = np.where(better, index, source_base_index)
        source_base_pressure = np.where(better, base_pressure, source_base_pressure)

    any_layer = source_base_index >= 0
    source_pressure = source_base_pressure - 50.0
    source_temperature = interpolate_to_targets(temperature, source_pressure)
    source_dewpoint = np.minimum(
        interpolate_to_targets(dewpoint, source_pressure),
        source_temperature,
    )
    source_height = interpolate_to_targets(height, source_pressure)
    if sharppy_thermo is not None:
        sharp_valid = (
            any_layer
            & np.isfinite(source_pressure)
            & np.isfinite(source_temperature)
            & np.isfinite(source_dewpoint)
        )
        sharp_wetbulb = np.asarray(
            sharppy_thermo.wetbulb(
                np.where(sharp_valid, source_pressure, 1000.0),
                np.where(sharp_valid, source_temperature - 273.15, 0.0),
                np.where(sharp_valid, source_dewpoint - 273.15, 0.0),
            ),
            dtype=float,
        )
        source_wetbulb_c = np.where(sharp_valid, sharp_wetbulb, np.nan)
    else:
        source_theta_e = equivalent_potential_temperature_k(
            source_pressure,
            source_temperature,
            source_dewpoint,
        )
        source_wetbulb_c = _saturated_temperature_from_theta_e(
            source_pressure,
            source_theta_e,
            source_temperature,
        ) - 273.15

    parcel_pressure = source_pressure.copy()
    parcel_temperature_c = source_wetbulb_c.copy()
    environment_temperature_c = source_temperature - 273.15
    parcel_height = source_height.copy()
    energy = np.zeros(surface_pressure.shape)

    # Igual que params.dcape: desde p0 recorre los niveles nativos hacia la
    # superficie, wetlift por capa e integra sin corrección virtual.
    for index in range(pressure.shape[0] - 1, -1, -1):
        active = (
            any_layer
            & (index <= source_base_index)
            & np.isfinite(pressure[index])
            & np.isfinite(temperature[index])
            & np.isfinite(height[index])
        )
        target_pressure = np.where(active, pressure[index], parcel_pressure)
        if sharppy_thermo is not None:
            sharp_valid = (
                active
                & np.isfinite(parcel_pressure)
                & np.isfinite(parcel_temperature_c)
                & np.isfinite(target_pressure)
            )
            sharp_lifted = np.asarray(
                sharppy_thermo.wetlift(
                    np.where(sharp_valid, parcel_pressure, 1000.0),
                    np.where(sharp_valid, parcel_temperature_c, 0.0),
                    np.where(sharp_valid, target_pressure, 1000.0),
                ),
                dtype=float,
            )
            target_parcel_c = np.where(
                sharp_valid,
                sharp_lifted,
                parcel_temperature_c,
            )
        else:
            target_parcel_c = _saturated_temperature_from_theta_e(
                target_pressure,
                equivalent_potential_temperature_k(
                    parcel_pressure,
                    parcel_temperature_c + 273.15,
                    parcel_temperature_c + 273.15,
                ),
                parcel_temperature_c + 273.15,
            ) - 273.15
        target_environment_c = temperature[index] - 273.15
        target_height = height[index]
        deficit_start = (parcel_temperature_c - environment_temperature_c) / (
            environment_temperature_c + 273.15
        )
        deficit_end = (target_parcel_c - target_environment_c) / (
            target_environment_c + 273.15
        )
        layer_energy = GRAVITY * 0.5 * (deficit_start + deficit_end) * (
            target_height - parcel_height
        )
        energy = np.where(active, energy + layer_energy, energy)
        parcel_pressure = np.where(active, target_pressure, parcel_pressure)
        parcel_temperature_c = np.where(active, target_parcel_c, parcel_temperature_c)
        environment_temperature_c = np.where(
            active,
            target_environment_c,
            environment_temperature_c,
        )
        parcel_height = np.where(active, target_height, parcel_height)

    return np.where(any_layer, np.maximum(energy, 0.0), np.nan)


def interpolate_profile_at_height(
    heights_m: np.ndarray,
    values: np.ndarray,
    target_height_m: np.ndarray,
) -> np.ndarray:
    heights = np.asarray(heights_m, dtype=float)
    data = np.asarray(values, dtype=float)
    target = np.asarray(target_height_m, dtype=float)
    result = np.full(target.shape, np.nan)
    for index in range(heights.shape[0] - 1):
        lower_height = heights[index]
        upper_height = heights[index + 1]
        within = (
            ~np.isfinite(result)
            & np.isfinite(lower_height)
            & np.isfinite(upper_height)
            & np.isfinite(data[index])
            & np.isfinite(data[index + 1])
            & (target >= lower_height)
            & (target <= upper_height)
            & (upper_height > lower_height)
        )
        fraction = np.divide(
            target - lower_height,
            upper_height - lower_height,
            out=np.zeros_like(target),
            where=upper_height > lower_height,
        )
        result = np.where(within, data[index] + fraction * (data[index + 1] - data[index]), result)
    return result


EARTH_RADIUS_M = 6_371_000.0


def vertical_vorticity(
    u: np.ndarray,
    v: np.ndarray,
    latitudes_deg: np.ndarray,
    lon_step_deg: float,
    lat_step_deg: float,
) -> np.ndarray:
    """Vorticidad vertical de cada nivel, en s⁻¹.

        ζ = ∂v/∂x − ∂u/∂y

    Las distancias van en metros, no en grados: un grado de longitud mide
    111 km en el ecuador y 64 en el norte del dominio, así que sin corregir por
    el coseno de la latitud la vorticidad saldría inflada arriba del mapa.

    Los dos pasos van con signo, el que lleva la rejilla al avanzar de índice:
    en AROME las filas bajan de norte a sur, así que lat_step_deg es negativo y
    es ese signo el que convierte ∂u/∂fila en ∂u/∂y. Pasarlo en valor absoluto
    invierte el término y en un vórtice ideal las dos contribuciones se anulan
    en lugar de sumarse.

    Necesita las celdas vecinas, de modo que hay que calcularla sobre la
    rejilla entera: por bandas, cada borde saldría mal.
    """
    paso_lat_m = np.radians(lat_step_deg) * EARTH_RADIUS_M
    paso_lon_m = (
        np.radians(lon_step_deg)
        * EARTH_RADIUS_M
        * np.cos(np.radians(latitudes_deg))
    )
    # np.gradient da diferencias centradas dentro y de un lado en los bordes.
    dv_dx = np.gradient(v, axis=-1) / paso_lon_m[None, :, None]
    du_dy = np.gradient(u, axis=-2) / paso_lat_m
    return dv_dx - du_dy


def updraft_helicity(
    height_agl_m: np.ndarray,
    vertical_velocity_ms: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    latitudes_deg: np.ndarray,
    lon_step_deg: float,
    lat_step_deg: float,
    base_m: float = 2_000.0,
    top_m: float = 5_000.0,
) -> np.ndarray:
    """Helicidad de la corriente ascendente entre dos alturas, en m²/s².

        UH = ∫ w·ζ dz

    Integra por trapecios sobre todos los niveles de la capa, con los extremos
    interpolados para que sea exactamente la pedida. Mide cuánta rotación
    acompaña al ascenso: es el rastro que deja una supercélula en un modelo que
    resuelve la convección, y separa una tormenta rotatoria de una que sólo
    sube fuerte.

    Devuelve NaN donde la columna no cubre la capa entera, no le quedan
    niveles válidos dentro o le falta alguno de los de en medio: un valor
    parcial se leería como rotación débil cuando en realidad es falta de datos.
    """
    integrando = vertical_velocity_ms * vertical_vorticity(
        u, v, latitudes_deg, lon_step_deg, lat_step_deg
    )
    z = height_agl_m
    z0, z1 = z[:-1], z[1:]
    h0, h1 = integrando[:-1], integrando[1:]
    espesor = z1 - z0
    utilizable = np.isfinite(espesor) & (espesor > 0)

    # Recorte de cada tramo a la capa, interpolando el integrando en los cortes.
    desde = np.clip(z0, base_m, top_m)
    hasta = np.clip(z1, base_m, top_m)
    solapa = utilizable & (hasta > desde)
    dentro = solapa & np.isfinite(h0) & np.isfinite(h1)
    # Un tramo que cae en la capa pero se queda sin integrando —falta un nivel
    # de IP3, por ejemplo— no se puede saltar: la integral saldría corta y ese
    # hueco no se distingue de una columna que gira poco.
    incompleta = np.any(solapa & ~dentro, axis=0)

    def en(altura: np.ndarray) -> np.ndarray:
        fraccion = np.divide(
            altura - z0, espesor, out=np.zeros_like(espesor), where=utilizable
        )
        return h0 + np.clip(fraccion, 0.0, 1.0) * (h1 - h0)

    trapecio = 0.5 * (en(desde) + en(hasta)) * (hasta - desde)
    total = np.sum(np.where(dentro, trapecio, 0.0), axis=0)

    # La columna tiene que llegar de verdad a los dos extremos de la capa.
    cima = np.nanmax(np.where(np.isfinite(z), z, -np.inf), axis=0)
    suelo = np.nanmin(np.where(np.isfinite(z), z, np.inf), axis=0)
    cubre = (
        (cima >= top_m)
        & (suelo <= base_m)
        & np.any(dentro, axis=0)
        & ~incompleta
    )
    return np.where(cubre, total, np.nan)


def _layer_mean_wind(
    height_agl_m: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    base_m: float,
    top_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Viento medio de una capa, pesado por su espesor.

    Pesar por espesor y no por niveles evita que la rejilla isobárica, que es
    mucho más densa abajo que arriba, incline la media hacia los niveles
    bajos. Los bordes se interpolan para que la capa sea exactamente la pedida.
    """
    z0, z1 = height_agl_m[:-1], height_agl_m[1:]
    espesor = z1 - z0
    valido = np.isfinite(espesor) & (espesor > 0)
    # Recorte de cada capa al tramo pedido.
    desde = np.clip(np.maximum(z0, base_m), base_m, top_m)
    hasta = np.clip(np.minimum(z1, top_m), base_m, top_m)
    dentro = valido & (hasta > desde)
    peso = np.where(dentro, hasta - desde, 0.0)

    def medio(valores: np.ndarray) -> np.ndarray:
        # Valor en el punto medio del tramo recortado, interpolado.
        centro = 0.5 * (desde + hasta)
        fraccion = np.where(dentro, (centro - z0) / np.where(valido, espesor, 1.0), 0.0)
        en_centro = valores[:-1] + fraccion * (valores[1:] - valores[:-1])
        total = np.sum(peso, axis=0)
        return np.divide(
            np.sum(np.where(dentro, en_centro * peso, 0.0), axis=0),
            total,
            out=np.full(total.shape, np.nan),
            where=total > 0,
        )

    return medio(u), medio(v)


def bunkers_right_motion(
    height_agl_m: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Movimiento de la supercélula derecha, Bunkers 2000.

    Es el viento medio de los seis primeros kilómetros desviado 7,5 m/s
    perpendicularmente a la cizalladura de la capa, hacia la derecha. Se usa
    éste y no el movimiento de célula ordinaria del panel porque los umbrales
    de SRH al uso —150, 300 m²/s²— están definidos sobre Bunkers, y con otro
    movimiento dejarían de significar lo mismo.

    Las medias van pesadas por espesor, no por presión, como especifica el
    artículo original: allí se comprueba que ponderar por presión no reduce el
    error. MetPy integra en presión, de modo que su movimiento sale algo
    distinto —en un hodógrafo de prueba, 1,1 m/s por componente— sin que
    ninguno de los dos esté mal.

    Devuelve NaN donde el perfil no llega a 6 km: sin esa capa la desviación
    no está definida y un valor aproximado engañaría más que ayudar.
    """
    medio_u, medio_v = _layer_mean_wind(height_agl_m, u, v, 0.0, 6_000.0)
    bajo_u, bajo_v = _layer_mean_wind(height_agl_m, u, v, 0.0, 500.0)
    alto_u, alto_v = _layer_mean_wind(height_agl_m, u, v, 5_500.0, 6_000.0)
    corte_u, corte_v = alto_u - bajo_u, alto_v - bajo_v
    modulo = np.hypot(corte_u, corte_v)
    # Perpendicular a la cizalladura, hacia la derecha del vector.
    desviacion_u = np.divide(
        DEVIATION_MS * corte_v, modulo, out=np.zeros_like(modulo), where=modulo > 0
    )
    desviacion_v = np.divide(
        -DEVIATION_MS * corte_u, modulo, out=np.zeros_like(modulo), where=modulo > 0
    )
    alcanza = np.nanmax(height_agl_m, axis=0) >= 6_000.0
    fuera = ~alcanza | (modulo <= 0)
    return (
        np.where(fuera, np.nan, medio_u + desviacion_u),
        np.where(fuera, np.nan, medio_v + desviacion_v),
    )


def storm_relative_helicity(
    height_agl_m: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    storm_u: np.ndarray,
    storm_v: np.ndarray,
    depth_m: float,
) -> np.ndarray:
    """Helicidad relativa a la tormenta entre el suelo y `depth_m`, en m²/s².

    Suma el área que el hodógrafo barre alrededor del vector de movimiento,
    tramo a tramo:

        SRH = Σ (u_{i+1}-C_u)(v_i-C_v) - (u_i-C_u)(v_{i+1}-C_v)

    Se recorren todos los niveles intermedios, no sólo los extremos, y el
    tramo que cruza el techo se interpola para que la capa sea exactamente la
    pedida. Las capas de espesor nulo —niveles isobáricos que quedan bajo
    tierra— no aportan nada.
    """
    z0, z1 = height_agl_m[:-1], height_agl_m[1:]
    espesor = z1 - z0
    valido = np.isfinite(espesor) & (espesor > 0) & (z0 < depth_m)
    # Qué parte del tramo entra: 1 si cabe entero, menos si cruza el techo.
    fraccion = np.clip(
        np.divide(
            depth_m - z0, espesor, out=np.zeros_like(espesor), where=espesor > 0
        ),
        0.0,
        1.0,
    )
    u_techo = u[:-1] + fraccion * (u[1:] - u[:-1])
    v_techo = v[:-1] + fraccion * (v[1:] - v[:-1])
    ur0, vr0 = u[:-1] - storm_u[None, ...], v[:-1] - storm_v[None, ...]
    ur1, vr1 = u_techo - storm_u[None, ...], v_techo - storm_v[None, ...]
    aporte = ur1 * vr0 - ur0 * vr1
    hay = np.any(valido & np.isfinite(aporte), axis=0)
    total = np.sum(np.where(valido & np.isfinite(aporte), aporte, 0.0), axis=0)
    return np.where(hay, total, np.nan)


def effective_bulk_wind_difference(
    heights_m: np.ndarray,
    u_ms: np.ndarray,
    v_ms: np.ndarray,
    effective_base_height_m: np.ndarray,
    equilibrium_height_m: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    base = np.asarray(effective_base_height_m, dtype=float)
    top = base + 0.5 * (np.asarray(equilibrium_height_m, dtype=float) - base)
    u_base = interpolate_profile_at_height(heights_m, u_ms, base)
    v_base = interpolate_profile_at_height(heights_m, v_ms, base)
    u_top = interpolate_profile_at_height(heights_m, u_ms, top)
    v_top = interpolate_profile_at_height(heights_m, v_ms, top)
    delta_u = u_top - u_base
    delta_v = v_top - v_base
    magnitude = np.hypot(delta_u, delta_v)
    invalid = ~np.isfinite(base) | ~np.isfinite(top) | (top <= base)
    return (
        np.where(invalid, np.nan, magnitude),
        np.where(invalid, np.nan, delta_u),
        np.where(invalid, np.nan, delta_v),
    )


def freezing_level_m(temperature_k: np.ndarray, height_m: np.ndarray) -> np.ndarray:
    temperature_c = np.asarray(temperature_k, dtype=float) - 273.15
    height = np.asarray(height_m, dtype=float)
    result = np.full(temperature_c.shape[1:], np.nan)
    for index in range(temperature_c.shape[0] - 1):
        t0, t1 = temperature_c[index], temperature_c[index + 1]
        crosses = (
            ~np.isfinite(result)
            & np.isfinite(t0)
            & np.isfinite(t1)
            & (t0 >= 0.0)
            & (t1 < 0.0)
        )
        fraction = np.divide(t0, t0 - t1, out=np.zeros_like(t0), where=np.abs(t0 - t1) > 1e-6)
        crossing = height[index] + fraction * (height[index + 1] - height[index])
        result = np.where(crosses, crossing, result)
    return result - height[0]


def significant_hail_parameter(
    mucape: np.ndarray,
    mu_mixing_ratio_gkg: np.ndarray,
    lapse_rate_700_500_ckm: np.ndarray,
    temperature_500_c: np.ndarray,
    shear_surface_6km_ms: np.ndarray,
    freezing_level_agl_m: np.ndarray,
) -> np.ndarray:
    """SHIP operativo reproducido de SHARPpy/SPC (normalización 42e6)."""
    cape = np.maximum(np.asarray(mucape, dtype=float), 0.0)
    mixing_ratio = np.clip(np.asarray(mu_mixing_ratio_gkg, dtype=float), 11.0, 13.6)
    lapse_rate = np.maximum(np.asarray(lapse_rate_700_500_ckm, dtype=float), 0.0)
    temperature_500 = np.minimum(np.asarray(temperature_500_c, dtype=float), -5.5)
    shear = np.clip(np.asarray(shear_surface_6km_ms, dtype=float), 7.0, 27.0)
    freezing = np.maximum(np.asarray(freezing_level_agl_m, dtype=float), 0.0)
    ship = -(cape * mixing_ratio * lapse_rate * temperature_500 * shear) / 42_000_000.0
    ship *= np.where(cape < 1300.0, cape / 1300.0, 1.0)
    ship *= np.where(lapse_rate < 5.8, lapse_rate / 5.8, 1.0)
    ship *= np.where(freezing < 2400.0, freezing / 2400.0, 1.0)
    valid = (
        np.isfinite(cape)
        & np.isfinite(mu_mixing_ratio_gkg)
        & np.isfinite(lapse_rate)
        & np.isfinite(temperature_500_c)
        & np.isfinite(shear_surface_6km_ms)
        & np.isfinite(freezing_level_agl_m)
    )
    return np.where(valid, np.maximum(ship, 0.0), np.nan)


def significant_hail_parameter_sharppy(
    mucape: np.ndarray,
    mu_mixing_ratio_gkg: np.ndarray,
    lapse_rate_700_500_ckm: np.ndarray,
    temperature_500_c: np.ndarray,
    shear_surface_6km_ms: np.ndarray,
    freezing_level_agl_m: np.ndarray,
) -> np.ndarray:
    """Ejecuta ``sharppy.sharptab.params.ship`` celda a celda.

    SHARPpy no vectoriza esta función, pero su coste (~1–2 s para toda la
    rejilla) es asumible durante el precálculo de cada hora. Si la dependencia
    opcional no está disponible, conserva la formulación vectorizada idéntica.
    """
    arrays = np.broadcast_arrays(
        np.asarray(mucape, dtype=float),
        np.asarray(mu_mixing_ratio_gkg, dtype=float),
        np.asarray(lapse_rate_700_500_ckm, dtype=float),
        np.asarray(temperature_500_c, dtype=float),
        np.asarray(shear_surface_6km_ms, dtype=float),
        np.asarray(freezing_level_agl_m, dtype=float),
    )
    if sharppy_params is None or sharppy_utils is None:
        return significant_hail_parameter(*arrays)

    result = np.full(arrays[0].shape, np.nan)
    valid = np.logical_and.reduce([np.isfinite(values) for values in arrays])
    flat_result = result.ravel()
    flattened = [values.ravel() for values in arrays]
    for flat_index in np.flatnonzero(valid.ravel()):
        cape, ratio, lapse_rate, temperature_500, shear, freezing = (
            float(values[flat_index]) for values in flattened
        )
        parcel_dewpoint_c = float(
            sharppy_thermo.temp_at_mixrat(max(ratio, 0.0), 1000.0)
            if sharppy_thermo is not None
            else dewpoint_from_mixing_ratio_k(
                np.asarray(1000.0),
                np.asarray(max(ratio, 0.0) / 1000.0),
            ) - 273.15
        )
        parcel = SimpleNamespace(
            bplus=max(cape, 0.0),
            pres=1000.0,
            dwpc=parcel_dewpoint_c,
        )
        profile = SimpleNamespace(
            sfc_6km_shear=(sharppy_utils.MS2KTS(max(shear, 0.0)), 0.0),
        )
        value = sharppy_params.ship(
            profile,
            mupcl=parcel,
            frz_lvl=max(freezing, 1e-9),
            h5_temp=temperature_500 if temperature_500 != 0.0 else 1e-9,
            lr75=max(lapse_rate, 1e-9),
        )
        flat_result[flat_index] = max(float(value), 0.0)
    return result
