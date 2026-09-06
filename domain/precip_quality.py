"""
Plausibilidad de la precipitación acumulada.

Los pluviómetros de balancín averiados no fallan con un valor absurdo que un
récord mundial descarte: fallan disparándose solos, y el contador sube a
saltos. El acumulado del día resultante (200-250 mm) queda por debajo del
récord de 24 h, así que el filtro de récords de ``ranking.py`` lo deja pasar.
Lo que sí delata al sensor es la INTENSIDAD entre dos partes consecutivos.

El techo NO es una constante en mm/min: la intensidad máxima alcanzable
depende de la duración del intervalo. Un aguacero puede dar 38 mm en un
minuto (récord mundial), pero nadie sostiene ese ritmo cinco minutos —el
récord a cinco son 63 mm, o sea 12,6 mm/min— ni una hora —401 mm, 6,7
mm/min—. Un techo plano filtraría lluvia real en los intervalos cortos y
dejaría pasar basura en los largos, así que aquí se interpola la curva
intensidad-duración de los récords mundiales.

Sobre esa curva se aplica un MARGEN amplio (``RECORD_SAFETY_FACTOR``): los
récords están para batirse, y un chaparrón histórico real no puede acabar
descartado por haber superado la marca vigente. El margen convierte la curva
en un umbral de sospecha holgado; quien de verdad pilla a los sensores que
mienten sin pasarse de listo es el control de coherencia de cada proveedor
(en METAR, un pluviómetro que acumula lluvia mientras el sensor de tiempo
presente no ve nada).

Este módulo es puro y no depende de ningún proveedor: opera sobre la serie
canónica ``(epochs, precips)``, donde ``precips`` es el acumulado del día en
milímetros. Al recortar un salto implausible se desplaza también todo el tramo
posterior, porque la serie es acumulada y dejar el escalón dentro
convertiría el recorte en un diente de sierra.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

# Curva intensidad-duración de los récords mundiales de precipitación:
# (duración en minutos, milímetros). Varios de los históricos están
# discutidos; se toman los más citados, y el margen de seguridad de abajo
# absorbe de sobra su incertidumbre.
WORLD_RECORD_CURVE: Tuple[Tuple[float, float], ...] = (
    (1.0, 38.0),        # Barot, Guadalupe, 1970
    (5.0, 63.0),        # Porto Bello, Panamá, 1911
    (15.0, 198.0),      # Plumb Point, Jamaica, 1916
    (42.0, 305.0),      # Holt, Misuri, 1947
    (60.0, 401.0),      # Shangdi, Mongolia Interior, 1975
    (120.0, 483.0),     # Rockport, Virginia Occidental, 1889
    (270.0, 782.0),     # Smethport, Pensilvania, 1942
    (360.0, 840.0),     # Muduocaidang, Mongolia Interior, 1977
    (720.0, 1144.0),    # Foc-Foc, Reunión, 1966
    (1440.0, 1825.0),   # Foc-Foc, Reunión, 1966
)

# Margen sobre el récord vigente. Un récord puede caer —y MeteoLabX no puede
# ser quien dé por falso el aguacero que lo bata—, así que solo se sospecha a
# partir de una vez y media la marca mundial. Nada meteorológico real ha
# superado nunca un récord de precipitación por un 50 %.
RECORD_SAFETY_FACTOR = 1.5

# Un intervalo demasiado corto convierte el ruido de redondeo en intensidades
# enormes; por debajo de esto no se juzga.
_MIN_INTERVAL_S = 30.0


def _is_nan(value: float) -> bool:
    return value != value


def _finite(value: Any) -> Optional[float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def max_plausible_precip_mm(
    minutes: float, *, safety_factor: float = RECORD_SAFETY_FACTOR,
) -> float:
    """Milímetros máximos admisibles en un intervalo de ``minutes`` minutos.

    Interpola la curva de récords en escala logarítmica —que es donde la
    relación duración/lámina es casi una recta— y extrapola por la pendiente
    del tramo extremo cuando la duración cae fuera de la tabla.
    """
    duration = max(float(minutes), 1e-6)
    durations = [point[0] for point in WORLD_RECORD_CURVE]
    amounts = [point[1] for point in WORLD_RECORD_CURVE]

    if duration <= durations[0]:
        low, high = 0, 1
    elif duration >= durations[-1]:
        low, high = len(durations) - 2, len(durations) - 1
    else:
        high = next(i for i, value in enumerate(durations) if value >= duration)
        low = high - 1

    log_d0, log_d1 = math.log(durations[low]), math.log(durations[high])
    log_a0, log_a1 = math.log(amounts[low]), math.log(amounts[high])
    slope = (log_a1 - log_a0) / (log_d1 - log_d0)
    record = math.exp(log_a0 + slope * (math.log(duration) - log_d0))
    return record * float(safety_factor)


@dataclass(frozen=True)
class PrecipJump:
    """Un incremento de precipitación por encima de la curva admisible."""

    epoch: int
    amount_mm: float
    minutes: float
    limit_mm: float

    @property
    def rate_mm_per_min(self) -> float:
        return self.amount_mm / self.minutes if self.minutes > 0 else float("inf")


def sanitize_precip_series(
    epochs: Sequence[Any],
    precips: Sequence[Any],
    *,
    safety_factor: float = RECORD_SAFETY_FACTOR,
) -> Tuple[List[float], List[PrecipJump]]:
    """Recorta de la serie acumulada los saltos que superan la curva.

    Devuelve ``(precips saneados, saltos descartados)``. Sin saltos, la lista
    de salida es equivalente a la de entrada. Los descensos del acumulado
    (reinicio de contador del proveedor a medianoche) no se juzgan: no son
    intensidad.
    """
    out: List[float] = []
    jumps: List[PrecipJump] = []
    offset = 0.0
    previous_value: Optional[float] = None
    previous_epoch: Optional[int] = None

    for epoch_raw, value_raw in zip(epochs, precips):
        value = _finite(value_raw)
        if value is None:
            out.append(float("nan"))
            continue
        epoch = _finite(epoch_raw)
        if epoch is None:
            out.append(value - offset)
            continue
        epoch = int(epoch)

        if previous_value is not None and previous_epoch is not None:
            interval_s = float(epoch - previous_epoch)
            increment = value - previous_value
            if increment > 0.0 and interval_s >= _MIN_INTERVAL_S:
                minutes = interval_s / 60.0
                limit = max_plausible_precip_mm(minutes, safety_factor=safety_factor)
                if increment > limit:
                    jumps.append(PrecipJump(
                        epoch=epoch,
                        amount_mm=increment,
                        minutes=minutes,
                        limit_mm=limit,
                    ))
                    offset += increment
        previous_value = value
        previous_epoch = epoch
        out.append(value - offset)

    return out, jumps


def worst_jump(jumps: Iterable[PrecipJump]) -> Optional[PrecipJump]:
    """El salto que más se pasa de su límite, para redactar el aviso."""
    jumps = list(jumps)
    if not jumps:
        return None
    return max(jumps, key=lambda jump: jump.amount_mm / jump.limit_mm)
