"""
Favoritos persistidos en el navegador.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from config import LS_FAVORITES
from utils.helpers import coerce_str
from utils.storage import get_local_storage_value, set_local_storage


def _safe_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _station_value(station: Any, key: str, default: Any = None) -> Any:
    if isinstance(station, dict):
        return station.get(key, default)
    return getattr(station, key, default)


def favorite_key(favorite: dict[str, Any]) -> str:
    provider_id = coerce_str(favorite.get("provider_id") or favorite.get("kind"), upper=True)
    station_id = str(favorite.get("station_id") or "").strip()
    if provider_id == "WU":
        station_id = station_id.upper()
    return f"{provider_id}:{station_id}"


def normalize_favorite(payload: Any) -> Optional[dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    provider_id = coerce_str(payload.get("provider_id") or payload.get("kind"), upper=True)
    if not provider_id:
        return None
    raw_station_id = payload.get("station_id") or payload.get("station") or ""
    # Defensa contra regresiones: si por error nos pasaron un dict/list
    # como station_id (era un bug en versiones previas del path
    # WeatherLink), lo rechazamos. Un favorito con dict serializado como
    # ID no es reconectable y mostraría texto basura en la UI.
    if not isinstance(raw_station_id, (str, int, float)):
        return None
    station_id = str(raw_station_id).strip()
    if not station_id or station_id.startswith("{") or station_id.startswith("["):
        return None

    station_name = str(payload.get("station_name") or payload.get("name") or station_id).strip() or station_id
    favorite = {
        # ``kind`` mantiene tres valores: WU, WEATHERLINK, PROVIDER.
        # Los dos primeros llevan credenciales del usuario; PROVIDER no.
        "kind": (
            "WU" if provider_id == "WU"
            else "WEATHERLINK" if provider_id == "WEATHERLINK"
            else "PROVIDER"
        ),
        "provider_id": provider_id,
        "provider_label": str(payload.get("provider_label") or payload.get("provider_name") or provider_id).strip() or provider_id,
        "station_id": station_id.upper() if provider_id == "WU" else station_id,
        "station_name": station_name,
        "locality": str(payload.get("locality") or "").strip(),
        "station_tz": str(payload.get("station_tz") or payload.get("tz") or "").strip(),
        "saved_at": int(float(payload.get("saved_at") or time.time())),
    }
    for source_key, target_key in (
        ("lat", "lat"),
        ("lon", "lon"),
        ("elevation_m", "elevation_m"),
    ):
        value = _safe_float(payload.get(source_key))
        if value is not None:
            favorite[target_key] = value

    if provider_id == "WU":
        api_key = str(payload.get("api_key") or "").strip()
        if not api_key:
            return None
        favorite["api_key"] = api_key
        favorite["z"] = str(payload.get("z") or payload.get("altitude") or "").strip()

    if provider_id == "WEATHERLINK":
        # WeatherLink necesita api_key + api_secret + altitud. Sin las
        # dos credenciales el favorito no puede reconectar, así que
        # rechazamos.
        api_key = str(payload.get("api_key") or "").strip()
        api_secret = str(payload.get("api_secret") or "").strip()
        if not api_key or not api_secret:
            return None
        favorite["api_key"] = api_key
        favorite["api_secret"] = api_secret
        favorite["z"] = str(payload.get("z") or payload.get("altitude") or "").strip()
        # UUID alternativo que la API v2 a veces usa en vez del numérico.
        station_uuid = str(payload.get("station_id_uuid") or "").strip()
        if station_uuid:
            favorite["station_id_uuid"] = station_uuid

    return favorite


def get_stored_favorites() -> list[dict[str, Any]]:
    txt = get_local_storage_value(LS_FAVORITES)
    if not txt:
        return []
    try:
        payload = json.loads(txt)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []

    favorites: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload:
        favorite = normalize_favorite(item)
        if not favorite:
            continue
        key = favorite_key(favorite)
        if key in seen:
            continue
        seen.add(key)
        favorites.append(favorite)
    return favorites


def set_stored_favorites(favorites: list[dict[str, Any]]) -> None:
    normalized = []
    seen: set[str] = set()
    for item in favorites:
        favorite = normalize_favorite(item)
        if not favorite:
            continue
        key = favorite_key(favorite)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(favorite)

    raw = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    set_local_storage(LS_FAVORITES, raw, "save")


def upsert_favorite(favorite: dict[str, Any]) -> bool:
    normalized = normalize_favorite(favorite)
    if not normalized:
        return False
    key = favorite_key(normalized)
    favorites = [item for item in get_stored_favorites() if favorite_key(item) != key]
    favorites.insert(0, normalized)
    set_stored_favorites(favorites)
    return True


def remove_favorite(favorite: dict[str, Any]) -> bool:
    normalized = normalize_favorite(favorite)
    if not normalized:
        return False
    key = favorite_key(normalized)
    favorites = get_stored_favorites()
    kept = [item for item in favorites if favorite_key(item) != key]
    if len(kept) == len(favorites):
        return False
    set_stored_favorites(kept)
    return True


def remove_favorites_by_provider(provider_id: str) -> bool:
    provider = coerce_str(provider_id, upper=True)
    if not provider:
        return False
    favorites = get_stored_favorites()
    kept = [item for item in favorites if coerce_str(item.get("provider_id"), upper=True) != provider]
    if len(kept) == len(favorites):
        return False
    set_stored_favorites(kept)
    return True


def favorite_from_wu(
    station_id: str,
    api_key: str,
    altitude_text: str = "",
    *,
    station_name: str = "",
    lat: Any = None,
    lon: Any = None,
    elevation_m: Any = None,
) -> Optional[dict[str, Any]]:
    return normalize_favorite(
        {
            "kind": "WU",
            "provider_id": "WU",
            "provider_label": "Weather Underground",
            "station_id": station_id,
            "station_name": station_name or station_id,
            "api_key": api_key,
            "z": altitude_text,
            "lat": lat,
            "lon": lon,
            "elevation_m": elevation_m if elevation_m not in (None, "") else altitude_text,
        }
    )


def favorite_from_provider_station(station: Any) -> Optional[dict[str, Any]]:
    provider_id = coerce_str(_station_value(station, "provider_id", ""), upper=True)
    if provider_id in ("", "WU", "WEATHERLINK"):
        return None
    return normalize_favorite(
        {
            "kind": "PROVIDER",
            "provider_id": provider_id,
            "provider_label": _station_value(station, "provider_name", _station_value(station, "provider", provider_id)),
            "station_id": _station_value(station, "station_id"),
            "station_name": _station_value(station, "name", _station_value(station, "station_name", "")),
            "locality": _station_value(station, "locality", ""),
            "lat": _station_value(station, "lat"),
            "lon": _station_value(station, "lon"),
            "elevation_m": _station_value(station, "elevation_m"),
            "station_tz": _station_value(station, "station_tz", ""),
        }
    )


def favorite_from_weatherlink(
    station_id: str,
    api_key: str,
    api_secret: str,
    altitude_text: str = "",
    *,
    station_name: str = "",
    station_id_uuid: str = "",
    lat: Any = None,
    lon: Any = None,
    elevation_m: Any = None,
) -> Optional[dict[str, Any]]:
    """
    Crea un favorito WeatherLink listo para ``upsert_favorite``.

    Persistir un favorito WeatherLink en localStorage incluye la
    ``api_secret`` del usuario, igual que WU persiste su ``api_key``.
    Sensibilidad equivalente; el usuario ya ha optado por ese tradeoff
    al elegir "Guardar".
    """
    return normalize_favorite(
        {
            "kind": "WEATHERLINK",
            "provider_id": "WEATHERLINK",
            "provider_label": "WeatherLink",
            "station_id": station_id,
            "station_id_uuid": station_id_uuid,
            "station_name": station_name or station_id,
            "api_key": api_key,
            "api_secret": api_secret,
            "z": altitude_text,
            "lat": lat,
            "lon": lon,
            "elevation_m": elevation_m if elevation_m not in (None, "") else altitude_text,
        }
    )
