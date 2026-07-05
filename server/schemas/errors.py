"""
Contrato común de errores para la API.

Filosofía: **lo mínimo posible**. Solo definimos el shape que sale por
HTTP y una excepción interna ``ProviderError`` que los servicios lanzan
para hablar entre capas. La traducción del código de error a texto humano
vive en el frontend (i18n).

Si en el futuro hacen falta más campos (``retry_after``, ``request_id``,
``trace_id``…), se añaden aquí. Evitar tentación de jerarquías de
excepciones; un único ``ProviderError`` + ``error_code`` string es
suficiente para un buen rato.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Nombres de parámetro de credencial que usan los proveedores de la app:
# ``apiKey`` (WU), ``api-key`` (WeatherLink), ``api_key`` (AEMET),
# ``api_secret``/``client_secret``/``token`` (varios). Cubre tanto query
# strings de URLs como pares clave=valor sueltos en texto libre.
_SECRET_PARAM_RE = re.compile(
    r"(?i)\b(api[-_]?key|apikey|api[-_]?secret|client[-_]?secret|access[-_]?token|token)"
    r"\s*=\s*[^&\s'\"]+"
)


def _mask_secrets(text: Optional[str]) -> Optional[str]:
    """Enmascara valores de credenciales en un texto libre.

    Red de seguridad para ``ProviderError.detail``: los servicios sanean sus
    mensajes a mano, pero nada impide que un servicio futuro haga
    ``detail=str(exc)`` con un ``httpx.HTTPStatusError``, cuyo ``str``
    incluye la URL COMPLETA con la query (``...?apiKey=SECRETO``). El detail
    acaba en los logs, en ``/v1/diagnostics`` (vía metrics) y en la
    respuesta HTTP de error — por aquí no debe salir ninguna key.
    """
    if not text:
        return text
    return _SECRET_PARAM_RE.sub(lambda m: f"{m.group(1)}=***", text)


class ErrorResponse(BaseModel):
    """
    Shape estable de un error devuelto por la API.

    Ejemplo::

        {
          "ok": false,
          "error_code": "provider_timeout",
          "provider": "WU",
          "detail": "Read timeout after 10s"
        }

    El frontend traduce ``error_code`` a un mensaje localizado y opcionalmente
    muestra ``detail`` solo en modo debug.
    """

    ok: Literal[False] = False
    error_code: str = Field(
        description=(
            "Identificador estable del error. Convención: snake_case en "
            "inglés. Ejemplos: ``provider_timeout``, ``provider_unauthorized``, "
            "``provider_ratelimit``, ``station_not_found``, ``bad_request``, "
            "``internal_error``."
        )
    )
    provider: Optional[str] = Field(
        default=None,
        description=(
            "Identificador del proveedor cuando el error viene de uno "
            "concreto (``WU``, ``AEMET``, ``METEOCAT``…). ``None`` si es "
            "un error genérico del backend."
        ),
    )
    detail: Optional[str] = Field(
        default=None,
        description=(
            "Texto libre en inglés con contexto adicional (mensaje crudo del "
            "proveedor, código HTTP, etc.). Pensado para logs y modo debug; "
            "el frontend no lo muestra en producción salvo que el usuario "
            "lo pida."
        ),
    )


class ProviderError(Exception):
    """
    Excepción interna que cualquier servicio de proveedor puede lanzar.

    Se serializa a ``ErrorResponse`` en la capa HTTP (vía un exception
    handler de FastAPI). De este modo los ``server/services/*`` no
    importan FastAPI ni saben de HTTP — solo lanzan ``ProviderError``.

    ``status_code`` permite al handler decidir el código HTTP de salida
    (502 Bad Gateway por defecto para errores upstream; 401 si la API key
    es mala; 429 si hay rate limit; etc.).
    """

    def __init__(
        self,
        error_code: str,
        *,
        provider: Optional[str] = None,
        detail: Optional[str] = None,
        status_code: int = 502,
    ) -> None:
        # Enmascarado en el constructor (no en to_response): así el detail
        # ya sale limpio por TODOS los caminos — str(exc) en logs, el
        # record_error de metrics/diagnostics y la respuesta HTTP.
        detail = _mask_secrets(detail)
        super().__init__(detail or error_code)
        self.error_code = error_code
        self.provider = provider
        self.detail = detail
        self.status_code = status_code

    def to_response(self) -> ErrorResponse:
        """Convierte la excepción en el shape público de respuesta."""
        return ErrorResponse(
            error_code=self.error_code,
            provider=self.provider,
            detail=self.detail,
        )
