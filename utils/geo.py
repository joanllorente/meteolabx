"""Utilidades geográficas compartidas."""

from __future__ import annotations

import math


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcula la distancia en km entre dos coordenadas."""
    earth_radius_km = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return earth_radius_km * c


def is_us_map_center(lat: float, lon: float) -> bool:
    return 17.0 <= float(lat) <= 72.5 and -178.0 <= float(lon) <= -52.0


def is_iberia_map_center(lat: float, lon: float) -> bool:
    return 27.0 <= float(lat) <= 45.5 and -19.5 <= float(lon) <= 5.5


def is_france_map_center(lat: float, lon: float) -> bool:
    return 41.0 <= float(lat) <= 51.8 and -5.8 <= float(lon) <= 10.2


def is_norway_map_center(lat: float, lon: float) -> bool:
    return 57.0 <= float(lat) <= 72.5 and 2.0 <= float(lon) <= 32.5


def is_uk_map_center(lat: float, lon: float) -> bool:
    return 49.0 <= float(lat) <= 61.5 and -9.8 <= float(lon) <= 2.8


def is_italy_map_center(lat: float, lon: float) -> bool:
    return 35.0 <= float(lat) <= 48.5 and 5.0 <= float(lon) <= 19.5


def is_portugal_map_center(lat: float, lon: float) -> bool:
    # Iberia extendida hacia el oeste: incluye Madeira y las Azores.
    return 29.0 <= float(lat) <= 43.5 and -32.5 <= float(lon) <= 5.5


def is_austria_map_center(lat: float, lon: float) -> bool:
    return 46.0 <= float(lat) <= 49.3 and 9.2 <= float(lon) <= 17.4


def is_sweden_map_center(lat: float, lon: float) -> bool:
    # Longitud mínima 11.0: deja fuera Oslo (10.75) pero incluye Gotemburgo.
    return 55.0 <= float(lat) <= 69.3 and 11.0 <= float(lon) <= 24.2


def is_canada_map_center(lat: float, lon: float) -> bool:
    # Norte del paralelo 49 más el corredor Windsor-Quebec-Marítimas; la
    # franja sur se acota para no tragarse el noreste de EE. UU.
    lat, lon = float(lat), float(lon)
    if 49.0 <= lat <= 83.5 and -141.0 <= lon <= -52.6:
        return True
    return 43.0 <= lat <= 49.0 and -83.0 <= lon <= -59.0
