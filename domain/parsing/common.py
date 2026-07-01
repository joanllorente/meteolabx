"""
Utilidades puras compartidas por los adaptadores de proveedores.

Centraliza funciones que estaban duplicadas con variantes sutiles en cada
módulo de proveedor. La versión canónica acepta como mínimo todos los formatos
que aceptaba cualquiera de las copias anteriores.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Union


# Formatos de fecha/hora no-ISO que se han visto en feeds de distintos
# proveedores. El orden importa: el primero que parsee gana.
_FALLBACK_DATETIME_FORMATS: tuple[str, ...] = (
    "%Y%m%d@%H%M",         # POEM
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M",
)


def parse_epoch(value: Any) -> Optional[int]:
    """
    Convierte un timestamp en formato heterogéneo a un epoch UNIX en segundos.

    Soporta:
      * ``None`` o cadena vacía → ``None``.
      * Numéricos (``int`` / ``float``): si son > 10**12 se asume que vienen en
        milisegundos y se dividen por 1000.
      * Cadenas con dígitos: tratadas como numéricos.
      * Cadenas ISO 8601 (incluyendo sufijo ``Z`` y separador ``" "``).
      * Cadenas en los formatos de :data:`_FALLBACK_DATETIME_FORMATS` (se
        asumen en UTC al no ir acompañadas de tz).

    Devuelve ``None`` si no se reconoce el formato o si el valor resulta no
    positivo.
    """
    if value is None:
        return None

    # Numéricos directos
    if isinstance(value, (int, float)):
        try:
            iv = int(value)
        except (TypeError, ValueError):
            return None
        if iv <= 0:
            return None
        if iv > 10**12:  # heurística milisegundos
            iv = int(iv / 1000)
        return iv

    raw = str(value).strip()
    if not raw:
        return None

    # Cadena de dígitos (epoch en string)
    if raw.isdigit():
        iv = int(raw)
        if iv > 10**12:
            iv = int(iv / 1000)
        return iv if iv > 0 else None

    # ISO 8601 (acepta " " como separador y sufijo Z)
    iso_raw = raw.replace(" ", "T")
    if iso_raw.endswith("Z"):
        iso_raw = iso_raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        pass

    # Formatos no ISO observados en proveedores
    for fmt in _FALLBACK_DATETIME_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue

    return None


def parse_epoch_first(values: Iterable[Any]) -> Optional[int]:
    """
    Devuelve el primer epoch parseable de un iterable, o ``None`` si ninguno
    se pudo interpretar. Útil cuando un payload trae varios campos de tiempo
    candidatos (``validity_time`` / ``reference_time`` / ``insert_time``...).
    """
    for value in values:
        epoch = parse_epoch(value)
        if epoch is not None:
            return epoch
    return None


def load_stations_json(
    path: Union[str, os.PathLike],
    *,
    dict_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Carga un fichero JSON con la lista de estaciones de un proveedor.

    Substituye al patrón duplicado en cada ``services/*.py`` donde se hacía un
    ``open() + json.load() + isinstance(...)`` casi idéntico. Manejo de errores
    silencioso (devuelve lista vacía) porque cada servicio espera ese
    contrato.

    Args:
        path: ruta al fichero JSON.
        dict_key: si el JSON está envuelto en un dict (caso MeteoGalicia, que
            entrega ``{"listaEstacionsMeteo": [...]}``), nombre de la clave
            donde vive la lista real. Si es ``None``, se acepta solo cuando el
            payload es una lista en el nivel raíz.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        # OSError cubre FileNotFoundError/PermissionError; ValueError cubre
        # JSONDecodeError. Cualquier otro error sí debe propagarse.
        return []

    if dict_key is not None and isinstance(payload, dict):
        payload = payload.get(dict_key, [])

    return payload if isinstance(payload, list) else []


def find_station_by_field(
    stations: Iterable[Dict[str, Any]],
    *,
    field: str,
    target: Any,
    case_insensitive: bool = True,
) -> Dict[str, Any]:
    """
    Busca en ``stations`` la primera entrada cuyo ``field`` coincida con
    ``target``. Pensado para reemplazar la implementación duplicada de
    ``_find_station`` en cada servicio.

    Args:
        stations: iterable de dicts (típicamente ``_load_stations()``).
        field: nombre del atributo a comparar (``"id"``, ``"codi"``,
            ``"idEstacion"``, ``"stationId"``, ``"id_station"``, etc.).
        target: id buscado. Se trata como cadena tras strip().
        case_insensitive: si es ``True`` (por defecto), normaliza a
            ``upper()`` antes de comparar. Cuando un proveedor distingue
            mayúsculas (caso raro), pasar ``False``.

    Devuelve un ``dict`` vacío si no hay coincidencia.
    """
    target_str = str(target or "").strip()
    if not target_str:
        return {}
    if case_insensitive:
        target_str = target_str.upper()

    for station in stations:
        if not isinstance(station, dict):
            continue
        raw_value = str(station.get(field, "") or "").strip()
        if case_insensitive:
            raw_value = raw_value.upper()
        if raw_value == target_str:
            return station
    return {}


__all__ = [
    "parse_epoch",
    "parse_epoch_first",
    "load_stations_json",
    "find_station_by_field",
]
