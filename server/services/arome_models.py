"""Qué AROME se está calculando: el operativo o el acoplado a IFS.

Météo-France publica dos AROME sobre la misma rejilla de 0,025°, con los
mismos 241 campos WCS, los mismos once paquetes GRIB, los mismos niveles y los
mismos bloques de plazos. Solo cambia de dónde salen las condiciones de
contorno: AROME-IFS se inicializa y se acopla con el IFS del CEPPM en vez de
con ARPEGE. Todo el cálculo es, por tanto, el mismo, y lo único que varía es
la dirección a la que se piden los datos y el sitio donde se guardan.

El modelo activo no viaja como argumento por las ~3.000 líneas de cálculo. Se
fija una vez donde empieza el trabajo —la petición HTTP, el proceso del
worker— y lo leen los tres sitios donde importa: las URL de Météo-France, las
claves del almacén y las cachés en memoria.

- En el worker, cada modelo tiene su propio proceso, así que basta con el
  valor por defecto del proceso (`set_process_model`). Lo heredan todos sus
  hilos y, por la variable de entorno, los subprocesos que lanza.
- En la API, un mismo proceso sirve los dos: cada petición lo fija con
  `using_model`, que vale para el hilo donde se ejecuta. Los hilos que se
  lancen desde ahí no lo heredan solos; hay que pasarles el contexto con
  `run_in_model_context`.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from functools import lru_cache, wraps
import os
from typing import Any, Callable, Iterator, TypeVar


@dataclass(frozen=True)
class AromeSource:
    id: str
    label: str
    # Servicio WCS dentro de la API AROME (una sola suscripción para los dos).
    wcs_service: str
    # API de paquetes GRIB: cada modelo tiene la suya y su propio nombre de
    # modelo y de producto en la ruta.
    package_api: str
    package_model: str
    package_product: str


AROME = AromeSource(
    id="arome",
    label="AROME France",
    wcs_service="MF-NWP-HIGHRES-AROME-0025-FRANCE-WCS",
    package_api="DPPaquetAROME",
    package_model="AROME",
    package_product="productARO",
)
AROME_IFS = AromeSource(
    id="arome-ifs",
    label="AROME-IFS",
    wcs_service="MF-NWP-HIGHRES-AROMEIFS-0025-FRANCE-WCS",
    package_api="DPPaquetAROMEIFS",
    package_model="AROMEIFS",
    package_product="productAROIFS",
)
AROME_SOURCES: dict[str, AromeSource] = {AROME.id: AROME, AROME_IFS.id: AROME_IFS}
DEFAULT_AROME_MODEL = AROME.id

# Lo leen los subprocesos que lanza el worker: con `spawn` no heredan la
# memoria del padre, pero sí su entorno.
MODEL_ENV = "METEOLABX_AROME_MODEL"

_override: ContextVar[str | None] = ContextVar("arome_model", default=None)


def _validate(model_id: str | None) -> str:
    name = str(model_id or DEFAULT_AROME_MODEL).strip().lower()
    if name not in AROME_SOURCES:
        raise ValueError(f"El modelo AROME «{model_id}» no está registrado.")
    return name


def _process_model() -> str:
    return _validate(os.getenv(MODEL_ENV, "").strip() or None)


def set_process_model(model_id: str) -> None:
    """Fija el modelo de todo el proceso y de los subprocesos que lance."""
    os.environ[MODEL_ENV] = _validate(model_id)


def current_model() -> str:
    return _override.get() or _process_model()


def current_source() -> AromeSource:
    return AROME_SOURCES[current_model()]


def is_arome_model(model_id: str | None) -> bool:
    return str(model_id or "").strip().lower() in AROME_SOURCES


@contextmanager
def using_model(model_id: str) -> Iterator[AromeSource]:
    """Fija el modelo para lo que se ejecute dentro, en este hilo."""
    token = _override.set(_validate(model_id))
    try:
        yield AROME_SOURCES[_override.get()]
    finally:
        _override.reset(token)


T = TypeVar("T")


def run_in_model_context(function: Callable[..., T]) -> Callable[..., T]:
    """Envuelve `function` para que se ejecute con el modelo de quien la envía.

    `ThreadPoolExecutor.submit` y `threading.Thread` no copian el contexto:
    sin esto, un hilo lanzado desde una petición de AROME-IFS pediría los
    datos de AROME.
    """
    context = copy_context()

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        # Una copia por llamada: el mismo envoltorio se usa desde varios hilos
        # a la vez (`pool.map`), y un Context solo puede estar activo en uno.
        return context.copy().run(function, *args, **kwargs)

    return wrapper


def per_model_lru_cache(maxsize: int):
    """`lru_cache` con el modelo activo dentro de la clave.

    Las pasadas de los dos modelos tienen las mismas horas: sin el modelo en
    la clave, el mapa de las 00Z de AROME-IFS saldría de la caché de AROME.
    """

    def decorator(function: Callable[..., T]) -> Callable[..., T]:
        @lru_cache(maxsize=maxsize)
        def cached(model_id: str, *args: Any, **kwargs: Any) -> T:
            with using_model(model_id):
                return function(*args, **kwargs)

        @wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return cached(current_model(), *args, **kwargs)

        wrapper.cache_clear = cached.cache_clear  # type: ignore[attr-defined]
        wrapper.cache_info = cached.cache_info  # type: ignore[attr-defined]
        return wrapper

    return decorator
