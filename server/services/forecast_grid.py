"""Formato binario de las rejillas que consume el visor.

Vivía dentro de `arome_forecast`, que era el único que escribía frames. Al
entrar un segundo modelo hacía falta elegir entre duplicar el empaquetado o
compartirlo, y duplicarlo significa que un cambio de formato arregle un modelo
y rompa el otro sin que nadie se entere hasta ver el mapa en blanco.

El cuerpo son códigos uint16 con los bytes altos y bajos en bloques separados,
precedidos de una cabecera JSON con la geometría y las escalas. El visor lo
deshace en `services/forecastApi.js`; cualquier cambio aquí obliga a subir
`FORECAST_DATA_REVISION` allí.
"""

from __future__ import annotations

import json
import math
import struct
from typing import Any

import numpy as np


GRID_FORMAT_VERSION = 3
QUANTIZATION_LEVELS = 4096
MAX_QUANTIZATION_CODE = 65534


def quantization_step(span: float) -> float:
    """Mayor paso 1/2/5·10^k que divide el rango del producto en ≥4096 niveles.

    Al reducir el número de códigos distintos el plano de bytes altos queda casi
    constante, y ahí está la ganancia frente a Float32, cuya mantisa es
    prácticamente ruido incompresible. Medido sobre un frame real de viento:
    un tercio del tamaño, con el valor del tooltip intacto en el 97,6 % de las
    celdas y un error máximo de 0,007 m/s.
    """
    if not np.isfinite(span) or span <= 0:
        return 1.0
    target = span / QUANTIZATION_LEVELS
    base = 10.0 ** math.floor(math.log10(target))
    for factor in (5.0, 2.0, 1.0):
        if factor * base <= target:
            return factor * base
    return base


def quantize_array(array: np.ndarray) -> tuple[bytes, dict[str, Any]]:
    """Codifica a uint16 con planos de byte separados; 0 marca «sin dato».

    Cada matriz se escala por su propio rango: el overlay (índice de elevación)
    no comparte magnitud con el escalar que acompaña, y heredar su paso
    deformaría los contornos.

    Separar el byte alto del bajo agrupa los bytes suaves y deja el ruido de
    baja magnitud en un bloque aparte, que gzip comprime mucho mejor que la
    secuencia intercalada.
    """
    finite = np.isfinite(array)
    if not finite.any():
        codes = np.zeros(array.shape, dtype="<u2")
        return codes.tobytes(), {"offset": 0.0, "step": 1.0}
    offset = float(np.nanmin(array))
    span = float(np.nanmax(array)) - offset
    step = quantization_step(span)
    # Un rango muy amplio no cabe en 16 bits con el paso preferido.
    if span / step > MAX_QUANTIZATION_CODE - 1:
        step = span / (MAX_QUANTIZATION_CODE - 1)
    codes = np.zeros(array.shape, dtype="<u2")
    codes[finite] = 1 + np.round((array[finite] - offset) / step).astype("<u2")
    # Se emiten los bytes altos y luego los bajos, en ese orden explícito, para
    # que el visor no dependa del orden de bytes de la máquina que sirvió.
    high = (codes >> 8).astype("u1")
    low = (codes & 0xFF).astype("u1")
    return high.tobytes(order="C") + low.tobytes(order="C"), {
        "offset": offset,
        "step": step,
    }


def pack_grid(
    product_id: str,
    values: np.ndarray,
    *,
    bounds: tuple[float, float, float, float],
    unit: str,
    vmin: float,
    vmax: float,
    vector_u: np.ndarray | None = None,
    vector_v: np.ndarray | None = None,
    overlay: np.ndarray | None = None,
    overlay_unit: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bytes:
    """Empaqueta un campo ya calculado en el formato de rejilla del visor.

    `metadata` se funde en la cabecera al final: es donde cada modelo mete lo
    suyo —RUN, hora válida, alcance de cálculo, nivel— sin que este módulo
    tenga que conocer ninguno de los dos.
    """
    values = np.asarray(values, dtype="<f4")
    inside = np.isfinite(values)
    arrays = [values]
    has_vectors = vector_u is not None and vector_v is not None
    if has_vectors:
        arrays.append(np.where(inside, vector_u, np.nan).astype("<f4"))
        arrays.append(np.where(inside, vector_v, np.nan).astype("<f4"))
    has_overlay = overlay is not None
    if has_overlay:
        arrays.append(np.where(inside, overlay, np.nan).astype("<f4"))

    height, width = values.shape
    west, south, east, north = (float(value) for value in bounds)
    # Cuando el escalar es el módulo del vector, el visor lo reconstruye y así
    # se ahorra un tercio del cuerpo sin ninguna pérdida.
    names = [
        "value",
        *(["u", "v"] if has_vectors else []),
        *(["overlay"] if has_overlay else []),
    ]
    value_source = None
    if has_vectors:
        finite = np.isfinite(arrays[0])
        modulus = np.hypot(arrays[1], arrays[2])
        if np.allclose(arrays[0][finite], modulus[finite], rtol=0, atol=1e-4):
            value_source = "hypot"
            arrays = arrays[1:]
            names = names[1:]

    encoded_arrays = []
    body_chunks = []
    for name, array in zip(names, arrays):
        chunk, scale = quantize_array(array)
        body_chunks.append(chunk)
        encoded_arrays.append({"name": name, **scale})

    finite_values = values[np.isfinite(values)]
    header = {
        "version": GRID_FORMAT_VERSION,
        "encoding": "u16-planes",
        "arrays": encoded_arrays,
        "value_source": value_source,
        "product": product_id,
        "width": width,
        "height": height,
        "bounds": [west, south, east, north],
        "vmin": float(vmin),
        "vmax": float(vmax),
        "unit": str(unit),
        "maximum": float(finite_values.max()) if finite_values.size else 0.0,
        "has_vectors": has_vectors,
        "has_overlay": has_overlay,
        "overlay_unit": overlay_unit if has_overlay else None,
        "array_order": names,
        **(metadata or {}),
    }
    encoded_header = json.dumps(header, separators=(",", ":")).encode("utf-8")
    body = bytearray(struct.pack("<I", len(encoded_header)))
    body.extend(encoded_header)
    for chunk in body_chunks:
        body.extend(chunk)
    return bytes(body)
