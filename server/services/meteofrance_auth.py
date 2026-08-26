"""Credencial OAuth del portal de Météo-France, compartida entre procesos.

La API de paquetes no acepta la clave de aplicación directamente: exige un
token de acceso que caduca en una hora. El portal mantiene **un solo token
vivo por aplicación**, así que emitir uno nuevo invalida el anterior: si dos
procesos renovaran a la vez, el primero se quedaría con una credencial muerta
a mitad de descarga.

Por eso el token se guarda en disco y su renovación se serializa con un
cerrojo de fichero: cada trabajo del worker es un proceso distinto, y todos
deben compartir exactamente la misma credencial.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time

import requests


TOKEN_URL = "https://portail-api.meteofrance.fr/token"
# Margen antes de la caducidad: una descarga larga no debe quedarse a medias
# con el token expirando por el camino.
RENEWAL_MARGIN_S = 300.0


class MeteoFranceAuthError(RuntimeError):
    """No se pudo obtener una credencial válida del portal."""


def _cache_path() -> Path:
    configured = os.getenv("METEOLABX_METEOFRANCE_TOKEN_CACHE", "").strip()
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "meteolabx-meteofrance-token.json"


def _application_id() -> str:
    value = os.getenv("METEOLABX_METEOFRANCE_APPLICATION_ID", "").strip()
    if not value:
        raise MeteoFranceAuthError(
            "METEOLABX_METEOFRANCE_APPLICATION_ID no está configurada; es el "
            "identificador Basic con el que el portal emite el token."
        )
    return value


def _read_cached() -> tuple[str, float]:
    try:
        stored = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", 0.0
    return str(stored.get("access_token") or ""), float(stored.get("expires_at") or 0.0)


def _is_usable(token: str, expires_at: float) -> bool:
    # Tiempo absoluto: el caché se comparte entre procesos y el reloj monótono
    # no es comparable fuera del proceso que lo midió.
    return bool(token) and expires_at - time.time() > RENEWAL_MARGIN_S


def _store(token: str, expires_at: float) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporal = path.with_suffix(f".{os.getpid()}.tmp")
        temporal.write_text(
            json.dumps({"access_token": token, "expires_at": expires_at}),
            encoding="utf-8",
        )
        temporal.replace(path)
    except OSError:
        # Sin caché el token vale igual; solo se pierde el poder compartirlo.
        pass


def _request_token() -> tuple[str, float]:
    response = requests.post(
        TOKEN_URL,
        params={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {_application_id()}"},
        timeout=60,
    )
    if response.status_code != 200:
        raise MeteoFranceAuthError(
            f"El portal rechazó la petición de token (HTTP {response.status_code})."
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise MeteoFranceAuthError("El portal devolvió un token ilegible.") from exc
    token = str(payload.get("access_token") or "")
    if not token:
        raise MeteoFranceAuthError("El portal no devolvió ningún access_token.")
    return token, time.time() + float(payload.get("expires_in") or 3600.0)


def _renew_exclusively(rejected: str) -> str:
    """Renueva bajo cerrojo para que solo un proceso pida token nuevo.

    Al entrar se vuelve a mirar el caché: otro proceso puede haber renovado
    mientras se esperaba, y pedir otro invalidaría el suyo.
    """
    lock_path = _cache_path().with_suffix(".lock")
    try:
        import fcntl
    except ImportError:  # pragma: no cover - producción y desarrollo son Unix
        token, expires_at = _request_token()
        _store(token, expires_at)
        return token

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="ascii") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            token, expires_at = _read_cached()
            if _is_usable(token, expires_at) and token != rejected:
                return token
            token, expires_at = _request_token()
            _store(token, expires_at)
            return token
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def access_token(rejected: str = "") -> str:
    """Token vigente del portal.

    Se renueva si no hay ninguno guardado, si al guardado le quedan menos de
    cinco minutos, o si quien llama indica en `rejected` el token que la API
    acaba de rechazar. Ese dato evita renovar cuando el rechazo se debía a que
    otro proceso ya había renovado y este seguía con el anterior.
    """
    token, expires_at = _read_cached()
    if _is_usable(token, expires_at) and token != rejected:
        return token
    return _renew_exclusively(rejected)


def authorization_headers(rejected: str = "") -> dict[str, str]:
    """Cabeceras que espera la API de paquetes."""
    return {"Authorization": f"Bearer {access_token(rejected)}", "Accept": "*/*"}
