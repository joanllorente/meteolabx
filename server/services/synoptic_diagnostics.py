"""Diagnósticos sinópticos en una rejilla regular lon/lat, norte arriba.

Derivadas esféricas en SI; Q se define sin dividir por estabilidad estática
(convención QVEC). El filtro de Q actúa sobre T y Z antes de derivar.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import binary_dilation, correlate1d, gaussian_filter, gaussian_filter1d

EARTH_RADIUS = 6_371_000.0
GRAVITY = 9.80665
OMEGA = 7.292115e-5
RD = 287.05
# Unas 6 celdas de 0,25° en latitud, ~165 km. Q usa derivadas segundas de Z
# y su divergencia, terceras: con 2 celdas el mapa era casi todo ruido de
# rejilla, y a partir de 6 queda la escala sinóptica, que es la que describe.
Q_SIGMA = 6.0
# Tope del filtro en longitud, en celdas: hacia el polo σ/cos φ se dispara.
Q_SIGMA_X_MAX = 24.0
# Celdas que se descartan alrededor de lo que queda bajo tierra: el filtro y
# las derivadas arrastran allí el borde del hueco.
Q_TERRAIN_MARGIN = 4


def coordinates(shape, bounds):
    height, width = shape
    west, south, east, north = bounds
    lon = np.deg2rad(west + (np.arange(width) + .5) * (east - west) / width)
    lat = np.deg2rad(north - (np.arange(height) + .5) * (north - south) / height)
    return lon, lat


def gradient(field, bounds):
    lon, lat = coordinates(field.shape, bounds)
    dx = np.gradient(field, lon[1] - lon[0], axis=1, edge_order=2) / (EARTH_RADIUS * np.cos(lat[:, None]))
    dy = np.gradient(field, lat[1] - lat[0], axis=0, edge_order=2) / EARTH_RADIUS
    return dx, dy


def relative_vorticity(u, v, bounds):
    """Componente radial del rotacional, s⁻¹ (sin Coriolis)."""
    _, lat = coordinates(u.shape, bounds)
    vx, _ = gradient(v, bounds)
    _, uy = gradient(u, bounds)
    return vx - uy + u * np.tan(lat[:, None]) / EARTH_RADIUS


def divergence(u, v, bounds):
    _, lat = coordinates(u.shape, bounds)
    ux, _ = gradient(u, bounds)
    _, vy = gradient(v, bounds)
    return ux + vy - v * np.tan(lat[:, None]) / EARTH_RADIUS


def _normalized(field, blur):
    """Convolución normalizada: los huecos no cuentan como ceros."""
    valid = np.isfinite(field)
    weights = blur(valid.astype(float))
    total = blur(np.where(valid, field, 0.0))
    result = np.divide(total, weights, out=np.full_like(total, np.nan), where=weights > 1e-6)
    return np.where(valid, result, np.nan)


def smooth(field, sigma, bounds=None):
    """Gaussiano de σ celdas en latitud.

    Con `bounds`, el filtro es de igual anchura en kilómetros: en longitud se
    ensancha a σ/cos φ, porque a 70° N una celda de longitud mide un tercio
    que una de latitud y un filtro en celdas apenas suavizaba en ese sentido.
    """
    if not sigma:
        return np.array(field, dtype=float)
    if bounds is None:
        return _normalized(field, lambda a: gaussian_filter(a, sigma, mode='nearest'))
    _, lat = coordinates(field.shape, bounds)
    sigma_x = np.minimum(sigma / np.maximum(np.cos(lat), 1e-3), Q_SIGMA_X_MAX)

    # Todas las filas con el mismo radio de núcleo. gaussian_filter1d lo
    # redondea a un entero que crece con σ, y en las filas donde saltaba una
    # celda el suavizado cambiaba de golpe: la tercera derivada de Q lo
    # convertía en franjas horizontales. Con radio fijo, σ varía de fila en
    # fila sin escalones.
    radius = int(np.ceil(4.0 * float(sigma_x.max())))
    offsets = np.arange(-radius, radius + 1, dtype=float)
    kernels = np.exp(-0.5 * (offsets[None, :] / sigma_x[:, None]) ** 2)
    kernels /= kernels.sum(axis=1, keepdims=True)

    def blur(array):
        array = gaussian_filter1d(array, sigma, axis=0, mode='nearest', truncate=4.0)
        return np.stack([
            correlate1d(row, kernel, mode='nearest')
            for row, kernel in zip(array, kernels)
        ])
    return _normalized(field, blur)


# Por debajo de 20° el viento geostrófico (g/f·∇Z) deja de parecerse al real y
# amplifica el ruido de Z por 1/f: a 20° ya vale el doble que a 45°. Mejor una
# franja gris que vectores sin sentido.
Q_MIN_LATITUDE = 20.0


def q_vectors(temperature, height, bounds, pressure_pa=70000.0, sigma=Q_SIGMA):
    """Q geostrófico (m² kg⁻¹ s⁻¹) y div Q (m kg⁻¹ s⁻¹).

    Incluye términos métricos esféricos en el tensor de deformación y en
    la divergencia. Se excluye |lat| < Q_MIN_LATITUDE por la aproximación geostrófica.
    """
    hueco = ~(np.isfinite(temperature) & np.isfinite(height))
    temperature = smooth(temperature, sigma, bounds)
    height = smooth(height, sigma, bounds)
    _, lat = coordinates(height.shape, bounds)
    f = 2 * OMEGA * np.sin(lat[:, None])
    f = np.where(np.abs(lat[:, None]) >= np.deg2rad(Q_MIN_LATITUDE), f, np.nan)
    zx, zy = gradient(height, bounds)
    ug, vg = -GRAVITY * zy / f, GRAVITY * zx / f
    tx, ty = gradient(temperature, bounds)
    ux, uy = gradient(ug, bounds)
    vx, vy = gradient(vg, bounds)
    metric = np.tan(lat[:, None]) / EARTH_RADIUS
    qx = -RD / pressure_pa * ((ux - vg * metric) * tx + (vx + ug * metric) * ty)
    qy = -RD / pressure_pa * (uy * tx + vy * ty)
    div = divergence(qx, qy, bounds)
    if sigma and hueco.any():
        borde = binary_dilation(hueco, iterations=Q_TERRAIN_MARGIN)
        qx, qy, div = (np.where(borde, np.nan, a) for a in (qx, qy, div))
    return qx, qy, div


# Frontogénesis: filtro más estrecho que el de Q. Los frentes miden decenas de
# kilómetros de ancho, y a 165 km se borraban. Con ~40 km quedan las bandas
# frontales finas, a cambio de algo de moteado junto al relieve.
FRONTOGENESIS_SIGMA = 1.5
# Sin franja alrededor del relieve: con derivadas primeras el borde del hueco
# no deja artefactos visibles, y la franja ensanchaba cada sierra 100 km.
FRONTOGENESIS_TERRAIN_MARGIN = 0


def frontogenesis(theta, u, v, bounds, sigma=FRONTOGENESIS_SIGMA):
    """Frontogénesis cinemática de Petterssen en el plano, en K m⁻¹ s⁻¹.

    F = −(1/|∇θ|)·[θx(∂u/∂x θx + ∂v/∂x θy) + θy(∂u/∂y θx + ∂v/∂y θy)]

    Con el viento real, no el geostrófico: la convergencia es la mitad de lo
    que hace un frente. Positiva donde el flujo aprieta las isentrópicas.
    Las derivadas del viento llevan los términos métricos de la esfera, como Q.
    """
    hueco = ~(np.isfinite(theta) & np.isfinite(u) & np.isfinite(v))
    theta = smooth(theta, sigma, bounds)
    u = smooth(u, sigma, bounds)
    v = smooth(v, sigma, bounds)
    _, lat = coordinates(theta.shape, bounds)
    metric = np.tan(lat[:, None]) / EARTH_RADIUS
    tx, ty = gradient(theta, bounds)
    ux, uy = gradient(u, bounds)
    vx, vy = gradient(v, bounds)
    ux = ux - v * metric
    vx = vx + u * metric
    modulo = np.hypot(tx, ty)
    numerador = tx * (ux * tx + vx * ty) + ty * (uy * tx + vy * ty)
    result = np.divide(-numerador, modulo, out=np.zeros_like(modulo), where=modulo > 1e-12)
    result = np.where(np.isfinite(modulo), result, np.nan)
    if sigma and hueco.any():
        # iterations=0 en binary_dilation significa «hasta que no cambie», que
        # taparía el mapa entero: sin margen, solo el hueco.
        margen = FRONTOGENESIS_TERRAIN_MARGIN
        borde = binary_dilation(hueco, iterations=margen) if margen > 0 else hueco
        result = np.where(borde, np.nan, result)
    return result


# Eady: por debajo de esta N² la capa es casi neutra y el índice, que divide
# por N, se dispara sin que haya más baroclinicidad.
EADY_MIN_N2 = 2e-5
# Latitud mínima: con f → 0 el índice deja de tener sentido.
EADY_MIN_LATITUDE = 15.0


def eady_growth_rate(u_low, v_low, u_high, v_high, z_low, z_high, t_low, t_high,
                     bounds, p_low=850.0, p_high=500.0, sigma=0.0):
    """Tasa de crecimiento de Eady de la capa, en s⁻¹.

    σ = 0,31 · |f| · |ΔV/Δz| / N, con la cizalladura como diferencia vectorial
    del viento entre los dos niveles dividida por su separación en altura, y
    N² = (g/θ̄)·Δθ/Δz. Donde N² no llega a `EADY_MIN_N2`, o cerca del ecuador,
    queda sin valor.
    """
    campos = [u_low, v_low, u_high, v_high, z_low, z_high, t_low, t_high]
    if sigma:
        campos = [smooth(campo, sigma, bounds) for campo in campos]
    u_low, v_low, u_high, v_high, z_low, z_high, t_low, t_high = campos
    _, lat = coordinates(np.shape(z_low), bounds)
    f = 2 * OMEGA * np.abs(np.sin(lat[:, None]))
    espesor = z_high - z_low
    cizalladura = np.hypot(u_high - u_low, v_high - v_low) / espesor
    theta_low = t_low * (1000.0 / p_low) ** 0.2857
    theta_high = t_high * (1000.0 / p_high) ** 0.2857
    n2 = GRAVITY / (0.5 * (theta_low + theta_high)) * (theta_high - theta_low) / espesor
    valido = (np.isfinite(n2) & (n2 >= EADY_MIN_N2) & (espesor > 0)
              & (np.abs(lat[:, None]) >= np.deg2rad(EADY_MIN_LATITUDE)))
    raiz = np.sqrt(np.where(valido, n2, np.nan))
    return np.where(valido, 0.31 * f * cizalladura / raiz, np.nan)
