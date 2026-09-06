"""
Plausibilidad de la temperatura observada.

Tres averías distintas, que ningún criterio único cubre. Las tres salieron de
estaciones reales de IEM en un mismo día:

- **Serie congelada.** Skriveri (Letonia) llevaba 24 h clavada entre 0,0 y
  0,2 °C con la humedad al 1 %, mientras su presión subía de 1002 a 1017 hPa
  de forma perfectamente coherente. La estación transmite y el boletín llega:
  lo que viene podrido son dos campos. Un valor así no es imposible —0 °C en
  Letonia existe—, lo que lo delata es que no varía.

- **Frío climatológicamente imposible.** Squaw Valley (California, 39 °N)
  publicando −73,3 °C en septiembre. No hay física que lo prohíba, pero sí
  climatología: a esa latitud y en esa época no existe.

- **Amplitud diurna imposible.** Gettysburg (Dakota del Sur) con 23,0 °C de
  máxima y −22,0 °C de mínima el mismo día. Cada valor por separado es
  creíble; juntos, no.

El suelo NO puede ser global: Concordia baja de −84 °C de verdad y un suelo
fijo le borraría sus récords, que es el error que este proyecto ya cometió una
vez. Por eso el suelo depende de la latitud y del mes, y lleva un margen
amplio: los récords están para batirse.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Suelo de frío por franja de latitud: (|lat| máxima, suelo invernal, suelo
# estival) en °C, con el margen ya incorporado sobre los récords conocidos.
# Referencias: Oymyakon −67,8 (63 °N), Verkhoyansk −67,8, Asia central −45,
# Rocosas en verano −15. Se redondea a la baja con holgura.
_COLD_FLOOR_BY_LATITUDE: Tuple[Tuple[float, float, float], ...] = (
    (23.5, -25.0, -15.0),   # trópicos (el altiplano andino ya baja de −20)
    (35.0, -50.0, -30.0),   # subtropical
    (50.0, -60.0, -35.0),   # latitudes medias
    (60.0, -72.0, -40.0),   # subpolar (Oymyakon con margen)
    (90.0, -75.0, -45.0),   # polar ártico
)

# La Antártida juega aparte: Vostok −89,2 y Concordia por debajo de −84 son
# reales, y la meseta puede dar frío extremo en cualquier mes.
ANTARCTIC_LATITUDE = -60.0
ANTARCTIC_FLOOR_C = -95.0

# Amplitud diurna máxima. Es el mismo umbral que el ranking lleva usando en
# producción (``ranking._MAX_DIURNAL_RANGE_C``), y se mantiene igual a
# propósito: que la ficha y el ranking discrepen sobre si un dato vale ya causó
# bastante confusión. Deja fuera el récord de Loma (Montana, 57 °C en 24 h),
# un chinook irrepetible, a cambio de descartar los 45 °C que separaban la
# máxima y la mínima de Gettysburg el mismo día de septiembre.
MAX_DIURNAL_RANGE_C = 40.0

# Serie congelada: cuánto puede variar como MUCHO una temperatura real a lo
# largo de una ventana larga. Skriveri se movió 0,2 °C en 24 h.
FLATLINE_SPAN_C = 0.5
FLATLINE_MIN_HOURS = 6.0
FLATLINE_MIN_SAMPLES = 12


def _is_nan(value: float) -> bool:
    return value != value


def _finite(value: Any) -> Optional[float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _winter_weight(latitude: float, month: int) -> float:
    """0 en pleno verano local, 1 en pleno invierno local."""
    # Enero es el mes más frío del hemisferio norte; julio, del sur.
    coldest = 1.0 if latitude >= 0 else 7.0
    phase = 2.0 * math.pi * (float(month) - coldest) / 12.0
    return (1.0 + math.cos(phase)) / 2.0


def cold_floor_c(latitude: Optional[float], month: Optional[int]) -> float:
    """Temperatura mínima plausible para ese lugar y esa época del año.

    Sin latitud o sin mes no se juzga: devuelve el suelo antártico, que no
    descarta nada realista.
    """
    lat = _finite(latitude)
    if lat is None or month is None or not 1 <= int(month) <= 12:
        return ANTARCTIC_FLOOR_C
    if lat <= ANTARCTIC_LATITUDE:
        return ANTARCTIC_FLOOR_C
    winter = _winter_weight(lat, int(month))
    for limite, suelo_invierno, suelo_verano in _COLD_FLOOR_BY_LATITUDE:
        if abs(lat) <= limite:
            return suelo_verano + winter * (suelo_invierno - suelo_verano)
    return ANTARCTIC_FLOOR_C


def is_climatologically_impossible(
    temperature_c: Any, latitude: Optional[float], month: Optional[int],
) -> bool:
    """El valor cae por debajo de lo que ese lugar puede dar en ese mes."""
    value = _finite(temperature_c)
    if value is None:
        return False
    return value < cold_floor_c(latitude, month)


def is_diurnal_range_impossible(tmax: Any, tmin: Any) -> bool:
    """Máxima y mínima que no pueden ser del mismo día."""
    alto, bajo = _finite(tmax), _finite(tmin)
    if alto is None or bajo is None:
        return False
    return (alto - bajo) > MAX_DIURNAL_RANGE_C


def flatlined_fields(
    epochs: Sequence[Any],
    values_by_field: Dict[str, Sequence[Any]],
    *,
    span_by_field: Optional[Dict[str, float]] = None,
) -> List[str]:
    """Campos que llevan horas sin variar de forma verosímil.

    Generaliza el control que solo se aplicaba a Windy. La tolerancia es por
    campo: una humedad puede quedarse clavada en 100 % con niebla, pero una
    temperatura que se mueve dos décimas en un día está rota.
    """
    spans = span_by_field or {}
    congelados: List[str] = []
    for field, values in values_by_field.items():
        validos = [
            (int(epoch), float(value))
            for epoch, value in zip(epochs, values)
            if _finite(epoch) is not None and _finite(value) is not None
        ]
        if len(validos) < FLATLINE_MIN_SAMPLES:
            continue
        if validos[-1][0] - validos[0][0] < FLATLINE_MIN_HOURS * 3600:
            continue
        medidas = [value for _epoch, value in validos]
        span = spans.get(field, FLATLINE_SPAN_C)
        if max(medidas) - min(medidas) < span:
            congelados.append(field)
    return congelados
