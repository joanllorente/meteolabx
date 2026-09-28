"""Ciclones tropicales activos del NHC, para poner nombre a las bajas del mapa.

El modelo solo da el campo de presión: el nombre sale del aviso oficial. De
cada ciclón activo se lee su aviso de previsión (TCM), que trae la posición
actual y la prevista hasta cinco días. El visor pone el nombre a la baja del
modelo que caiga cerca de esa trayectoria en cada hora válida; más allá del
último punto del aviso, o si se disipa, el ciclón no se nombra.

Fuentes:
- NHC, https://www.nhc.noaa.gov/CurrentStorms.json: Atlántico y Pacífico
  oriental y central, con la trayectoria del propio aviso.
- GDACS (UE y ONU), con los avisos del JTWC: el resto de cuencas —Pacífico
  occidental, Índico y hemisferio sur—. De GDACS solo se toman los ciclones
  que caen fuera de las cuencas del NHC, para no duplicarlos.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
import threading
import time
from typing import Any

import requests


logger = logging.getLogger(__name__)

CURRENT_STORMS_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"
GDACS_EVENTS_URL = (
    "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
    "?eventlist=TC&alertlevel=Green;Orange;Red"
)
# Un evento cuyo último aviso tiene más de esto ya no está activo, aunque
# GDACS lo siga marcando como vigente.
GDACS_MAX_AGE = timedelta(hours=36)
# Las depresiones sin nombre salen numeradas («ONE-26», «EIGHTEEN-E-26»).
_SIN_NOMBRE = re.compile(
    r"^(ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE|THIRTEEN|"
    r"FOURTEEN|FIFTEEN|SIXTEEN|SEVENTEEN|EIGHTEEN|NINETEEN|TWENTY|THIRTY|FORTY|"
    r"INVEST|\d+)\b"
)
CACHE_TTL_S = 30 * 60
TIMEOUT_S = 15

_CLASES = {
    "TD": "depression", "TS": "storm", "HU": "hurricane", "STD": "subtropical",
    "STS": "subtropical", "PTC": "potential", "PC": "post-tropical", "TY": "typhoon",
}

# «CENTER LOCATED NEAR 28.9N  44.0W AT 27/2100Z»,
# «AT 27/1800Z CENTER WAS LOCATED NEAR 29.1N  43.9W»,
# «FORECAST VALID 28/0600Z 28.1N  44.1W...POST-TROP/REMNT LOW»,
# «OUTLOOK VALID 01/1800Z...DISSIPATED».
_POSICION = r"(\d+(?:\.\d+)?)([NS])\s+(\d+(?:\.\d+)?)([EW])"
_ACTUAL = re.compile(r"^(?:[A-Z ]+ )?CENTER LOCATED NEAR " + _POSICION + r" AT (\d{2})/(\d{2})(\d{2})Z", re.M)
_PASADA = re.compile(r"^AT (\d{2})/(\d{2})(\d{2})Z CENTER WAS LOCATED NEAR " + _POSICION, re.M)
_PREVISTA = re.compile(r"^(?:FORECAST|OUTLOOK) VALID (\d{2})/(\d{2})(\d{2})Z\s+" + _POSICION + r"(\.\.\.[A-Z/ -]+)?", re.M)

_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_lock = threading.Lock()


def _grados(valor: str, hemisferio: str) -> float:
    numero = float(valor)
    return -numero if hemisferio in ("S", "W") else numero


def _fecha(dia: str, hora: str, minuto: str, referencia: datetime) -> datetime:
    """Día y hora del aviso, que no traen mes: se toma el más cercano al aviso."""
    base = referencia.replace(day=1, hour=int(hora), minute=int(minuto), second=0, microsecond=0)
    candidatas = []
    for meses in (-1, 0, 1):
        mes = base.month + meses
        anio = base.year + (mes - 1) // 12
        mes = (mes - 1) % 12 + 1
        try:
            candidatas.append(base.replace(year=anio, month=mes, day=int(dia)))
        except ValueError:
            continue
    return min(candidatas, key=lambda fecha: abs(fecha - referencia))


def parse_forecast_advisory(texto: str, referencia: datetime) -> list[dict[str, Any]]:
    """Trayectoria de un aviso TCM: posición pasada, actual y previstas, en orden."""
    puntos: dict[datetime, dict[str, Any]] = {}

    def anadir(dia, hora, minuto, lat, ns, lon, ew, etapa):
        fecha = _fecha(dia, hora, minuto, referencia)
        puntos[fecha] = {
            "time": fecha.isoformat().replace("+00:00", "Z"),
            "latitude": _grados(lat, ns),
            "longitude": _grados(lon, ew),
            "stage": etapa,
        }

    for m in _PASADA.finditer(texto):
        anadir(*m.group(1, 2, 3), *m.group(4, 5, 6, 7), "tropical")
    for m in _ACTUAL.finditer(texto):
        anadir(*m.group(5, 6, 7), *m.group(1, 2, 3, 4), "tropical")
    for m in _PREVISTA.finditer(texto):
        sufijo = (m.group(8) or "").upper()
        etapa = "extratropical" if "EXTRATROP" in sufijo else "remnant" if "POST-TROP" in sufijo or "REMNT" in sufijo else "tropical"
        anadir(*m.group(1, 2, 3), *m.group(4, 5, 6, 7), etapa)
    return [puntos[fecha] for fecha in sorted(puntos)]


def _storm(item: dict[str, Any]) -> dict[str, Any] | None:
    aviso = (item.get("forecastAdvisory") or {})
    url = aviso.get("url")
    if not url:
        return None
    emision = datetime.fromisoformat(str(aviso.get("issuance") or item.get("lastUpdate")).replace("Z", "+00:00"))
    respuesta = requests.get(url, timeout=TIMEOUT_S)
    respuesta.raise_for_status()
    trayectoria = parse_forecast_advisory(respuesta.text, emision)
    if not trayectoria:
        return None
    return {
        "id": item.get("id"),
        "name": str(item.get("name") or "").strip().title(),
        "classification": _CLASES.get(str(item.get("classification") or "").upper(), "storm"),
        "track": trayectoria,
    }


def en_cuencas_nhc(latitud: float, longitud: float) -> bool:
    """Atlántico norte y Pacífico nororiental y central, hasta la línea de cambio de fecha."""
    return latitud >= 0 and -180 <= longitud <= 0


def _gdacs_track(geometria: dict[str, Any], referencia: datetime) -> list[dict[str, Any]]:
    """Puntos de la trayectoria de un evento de GDACS, pasados y previstos.

    Cada punto es un círculo diminuto (`Point_Polygon_Point_N`) con la fecha
    en `key` como MMDDHHMM; su centro es la media del anillo.
    """
    puntos: dict[datetime, dict[str, Any]] = {}
    for feature in geometria.get("features") or []:
        props = feature.get("properties") or {}
        if not str(props.get("Class") or "").startswith("Point_Polygon_Point"):
            continue
        clave = str(props.get("key") or "")
        anillo = ((feature.get("geometry") or {}).get("coordinates") or [[]])[0]
        if len(clave) != 8 or not anillo:
            continue
        try:
            fecha = datetime(referencia.year, int(clave[:2]), int(clave[2:4]), int(clave[4:6]),
                             int(clave[6:8]), tzinfo=timezone.utc)
        except ValueError:
            continue
        # La clave no lleva año: a caballo de fin de año, el más cercano.
        if fecha - referencia > timedelta(days=180):
            fecha = fecha.replace(year=fecha.year - 1)
        elif referencia - fecha > timedelta(days=180):
            fecha = fecha.replace(year=fecha.year + 1)
        lon = sum(p[0] for p in anillo) / len(anillo)
        lat = sum(p[1] for p in anillo) / len(anillo)
        puntos[fecha] = {
            "time": fecha.isoformat().replace("+00:00", "Z"),
            "latitude": round(lat, 2),
            "longitude": round(((lon + 180) % 360) - 180, 2),
            "stage": "tropical",
        }
    return [puntos[fecha] for fecha in sorted(puntos)]


def fetch_gdacs_storms(nombres_nhc: set[str]) -> list[dict[str, Any]]:
    respuesta = requests.get(GDACS_EVENTS_URL, timeout=TIMEOUT_S)
    respuesta.raise_for_status()
    ahora = datetime.now(timezone.utc)
    ciclones = []
    for feature in respuesta.json().get("features") or []:
        props = feature.get("properties") or {}
        if str(props.get("iscurrent")).lower() != "true":
            continue
        nombre = str(props.get("eventname") or "").rsplit("-", 1)[0].strip()
        if not nombre or _SIN_NOMBRE.match(nombre) or nombre.title() in nombres_nhc:
            continue
        lon, lat = ((feature.get("geometry") or {}).get("coordinates") or [None, None])[:2]
        if lat is None or en_cuencas_nhc(float(lat), float(lon)):
            continue
        try:
            ultimo = datetime.fromisoformat(str(props.get("todate"))).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ahora - ultimo > GDACS_MAX_AGE:
            continue
        url = (props.get("url") or {}).get("geometry")
        if not url:
            continue
        try:
            geometria = requests.get(url, timeout=TIMEOUT_S)
            geometria.raise_for_status()
            trayectoria = _gdacs_track(geometria.json(), ultimo)
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Trayectoria de GDACS ilegible para %s: %s", nombre, exc)
            continue
        if trayectoria:
            ciclones.append({
                "id": f"gdacs-{props.get('eventid')}",
                "name": nombre.title(),
                "classification": "storm",
                "source": "GDACS",
                "track": trayectoria,
            })
    return ciclones


def fetch_active_storms() -> dict[str, Any]:
    ciclones = _fetch_nhc_storms()
    try:
        ciclones += fetch_gdacs_storms({ciclon["name"] for ciclon in ciclones})
    except (requests.RequestException, ValueError) as exc:
        # Sin GDACS siguen valiendo los del NHC.
        logger.warning("GDACS sin responder: %s", exc)
    return {
        "source": "NHC+GDACS",
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "storms": ciclones,
    }


def _fetch_nhc_storms() -> list[dict[str, Any]]:
    respuesta = requests.get(CURRENT_STORMS_URL, timeout=TIMEOUT_S)
    respuesta.raise_for_status()
    ciclones = []
    for item in respuesta.json().get("activeStorms") or []:
        try:
            ciclon = _storm(item)
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Aviso del NHC ilegible para %s: %s", item.get("id"), exc)
            continue
        if ciclon and ciclon["name"]:
            ciclones.append({**ciclon, "source": "NHC"})
    return ciclones


def active_storms() -> dict[str, Any]:
    """Ciclones activos, guardados media hora. Si el NHC falla, lo último que hubo."""
    with _lock:
        if _cache["payload"] is not None and time.monotonic() - _cache["at"] < CACHE_TTL_S:
            return _cache["payload"]
        try:
            _cache["payload"] = fetch_active_storms()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("NHC sin responder: %s", exc)
            if _cache["payload"] is None:
                return {"source": "NHC+GDACS", "updated_at": None, "storms": []}
            # Reintentar en cinco minutos, no en cada petición.
            _cache["at"] = time.monotonic() - CACHE_TTL_S + 5 * 60
            return _cache["payload"]
        _cache["at"] = time.monotonic()
        return _cache["payload"]
