"""
Tests del helper ``_station_name`` de ``domain.parsing.weatherlink``.

Cubre la cadena de fallbacks que MeteoLabX usa para mostrar un nombre
amistoso de una estación WeatherLink. WeatherLink no es consistente:
algunas estaciones tienen ``station_name`` poblado, otras usan
``username`` como alias público, otras solo el gateway_id.
"""

from __future__ import annotations

from domain.parsing.weatherlink import _station_name, normalize_weatherlink_stations


def test_station_name_uses_station_name_when_present() -> None:
    station = {
        "station_id": "123631",
        "station_name": "My Backyard Station",
        "username": "should_not_be_used",
    }
    assert _station_name(station, fallback="123631") == "My Backyard Station"


def test_station_name_falls_back_to_username() -> None:
    """
    Caso real reportado: WeatherLink no devuelve ``station_name`` pero
    sí ``username``. El usuario espera ver ese alias y no el station_id.
    """
    station = {
        "station_id": "123631",
        "station_name": "",  # vacío
        "username": "meteo_roses",
    }
    assert _station_name(station, fallback="123631") == "meteo_roses"


def test_station_name_falls_back_to_name() -> None:
    station = {"station_id": "1", "name": "Plain Name"}
    assert _station_name(station, fallback="1") == "Plain Name"


def test_station_name_falls_back_to_device_name() -> None:
    station = {"station_id": "1", "device_name": "ISS-01"}
    assert _station_name(station, fallback="1") == "ISS-01"


def test_station_name_falls_back_to_gateway_hex() -> None:
    station = {"station_id": "1", "gateway_id_hex": "abc-123"}
    assert _station_name(station, fallback="1") == "abc-123"


def test_station_name_returns_fallback_when_all_empty() -> None:
    """Cuando ningún campo legible está disponible → station_id."""
    station = {"station_id": "123631"}
    assert _station_name(station, fallback="123631") == "123631"


def test_station_name_priority_order() -> None:
    """
    Prioridad: station_name > name > username > device_name > gateway_id_hex.
    Si todos están, gana el primero.
    """
    station = {
        "station_name": "primary",
        "name": "secondary",
        "username": "tertiary",
        "device_name": "qua",
        "gateway_id_hex": "quin",
    }
    assert _station_name(station, fallback="x") == "primary"


def test_normalize_stations_picks_username_when_station_name_missing() -> None:
    payload = {
        "stations": [
            {"station_id": "123631", "username": "meteo_roses"},
        ],
    }
    stations = normalize_weatherlink_stations(payload)
    assert len(stations) == 1
    assert stations[0]["station_name"] == "meteo_roses"
