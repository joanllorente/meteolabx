"""El backend no puede depender de Streamlit.

El servicio no sirve ninguna pantalla de Streamlit, pero lo importaba a través
de ``tabs/arome_forecast.py``, de donde salían los cálculos de la predicción.
El paquete se quejaba en cada petición de no encontrar su contexto —3.768
avisos en hora y media, el 71 % del log del servicio— y cargaba en memoria una
dependencia de interfaz que nadie usaba.

Estos tests son el criterio de aceptación de esa separación: si alguien vuelve
a colar un import de Streamlit en el camino del backend, fallan aquí y no en
producción.
"""

from __future__ import annotations

import builtins
import importlib
import sys

import pytest


# El stack visual entero de la interfaz anterior. Ninguno está ya en
# ``requirements.txt``; el entorno de desarrollo puede conservarlos instalados
# de antes, así que el test los hace desaparecer para medir de verdad.
_PAQUETES_RETIRADOS = ("streamlit", "plotly", "pydeck", "streamlit_autorefresh")


class _SinStreamlit:
    """Finder que hace desaparecer de la importación los paquetes retirados."""

    @staticmethod
    def _vetado(nombre: str) -> bool:
        return nombre.split(".")[0] in _PAQUETES_RETIRADOS

    def find_module(self, nombre, ruta=None):  # noqa: D102 (protocolo antiguo)
        if self._vetado(nombre):
            raise ImportError(f"{nombre} no está instalado (simulado)")
        return None

    def find_spec(self, nombre, ruta=None, destino=None):  # noqa: D102
        if self._vetado(nombre):
            raise ImportError(f"{nombre} no está instalado (simulado)")
        return None


@pytest.fixture
def sin_streamlit():
    """Ejecuta el bloque como si el paquete no estuviera instalado.

    Deja además un bucle de asyncio activo. Algunos módulos lo piden al
    importarse, y reimportarlos sin bucle falla con un ``RuntimeError`` ajeno a
    Streamlit; sin esto, el test acusaría al paquete de un problema que no es
    suyo. Con el intérprete del proyecto no hace falta, pero sí con uno más
    antiguo, y así el test dice lo que mide en cualquiera de los dos.
    """
    import asyncio

    creado = None
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        creado = asyncio.new_event_loop()
        asyncio.set_event_loop(creado)

    bloqueo = _SinStreamlit()
    guardados = {
        nombre: modulo
        for nombre, modulo in sys.modules.items()
        if nombre.split(".")[0] in _PAQUETES_RETIRADOS
    }
    for nombre in guardados:
        del sys.modules[nombre]
    sys.meta_path.insert(0, bloqueo)
    try:
        yield
    finally:
        sys.meta_path.remove(bloqueo)
        sys.modules.update(guardados)
        if creado is not None:
            asyncio.set_event_loop(None)
            creado.close()


@pytest.mark.parametrize(
    "modulo",
    [
        "server.main",
        "server.routers.climo",
        "server.routers.ecmwf",
        "server.routers.forecast",
        "server.routers.health",
        "server.routers.observations",
        "server.routers.ranking",
        "server.routers.stations",
        "server.routers.stats",
        "server.services.arome_forecast",
        "server.services.arome_wcs",
        # La traducción pura y quienes la usan. ``domain.climograms`` llegaba a
        # Streamlit por un camino indirecto: ``utils/__init__.py`` importaba
        # ``.i18n`` al cargar el paquete, así que un ``from utils.units import``
        # bastaba para arrastrarlo.
        "domain.climograms",
        "domain.historical_details",
        "domain.i18n_catalog",
        "domain.observation_pipeline",
        "utils.helpers",
        "utils.units",
        "scripts.forecast_worker",
    ],
)
def test_the_backend_imports_without_streamlit(sin_streamlit, modulo: str) -> None:
    """Los cinco puntos de entrada del servicio, sin el paquete delante."""
    for nombre in [n for n in sys.modules if n == modulo or n.startswith(f"{modulo}.")]:
        del sys.modules[nombre]
    importlib.import_module(modulo)


def test_the_pieces_the_backend_uses_are_in_the_pure_module(sin_streamlit) -> None:
    """Lo que el backend importaba de ``tabs/`` tiene que estar aquí."""
    modulo = importlib.import_module("server.services.arome_wcs")
    for pieza in (
        "AromeError", "AromeWCS", "LOCAL_TZ", "PALETTE", "RasterField",
        "_align", "_catalonia_geometry", "_compute_shear", "_compute_ship",
        "_get_uv_height", "_height_from_geopotential",
        "_load_forecast_regions_geojson", "_mask_to_catalonia",
        "_resolved_prefixes", "_wait_for_api_request_slot",
        "forecast_calculation_scope",
    ):
        assert hasattr(modulo, pieza), pieza


def test_the_cache_keeps_the_old_behaviour() -> None:
    """La sustitución de ``st.cache_data`` no es mecánica.

    Aquel decorador serializaba el resultado y devolvía una copia nueva en cada
    llamada; un ``lru_cache`` entregaría siempre el mismo objeto y un consumidor
    descuidado envenenaría la caché para todo el día.
    """
    from server.services import arome_wcs

    arome_wcs.cache_clear()
    arome_wcs._cache_put("prueba", ("k",), {"a": 1}, ttl_s=60, max_entries=8)
    assert arome_wcs._cache_get("prueba", ("k",)) == {"a": 1}

    # Caducidad: con TTL agotado, la entrada desaparece.
    arome_wcs._cache_put("prueba", ("viejo",), "x", ttl_s=-1, max_entries=8)
    assert arome_wcs._cache_get("prueba", ("viejo",)) is None

    # Límite de entradas: no crece sin freno.
    for i in range(20):
        arome_wcs._cache_put("prueba", (i,), i, ttl_s=60, max_entries=8)
    assert len(arome_wcs._CACHE) <= 8
    arome_wcs.cache_clear()


def test_the_regions_cache_hands_out_copies() -> None:
    """El GeoJSON se guarda serializado: quien lo recibe puede tocarlo sin
    estropearlo para el resto del día."""
    from server.services import arome_wcs

    arome_wcs.cache_clear()
    llamadas = []

    def falso():
        llamadas.append(1)
        return {"features": [{"properties": {"NAMEUNIT": "Barcelona"}}]}

    original = arome_wcs._load_forecast_regions_geojson_sin_cache
    arome_wcs._load_forecast_regions_geojson_sin_cache = falso
    try:
        primero = arome_wcs._load_forecast_regions_geojson()
        primero["features"].append("basura")          # un consumidor descuidado
        segundo = arome_wcs._load_forecast_regions_geojson()
        assert len(llamadas) == 1                      # sí se cacheó
        assert segundo["features"] == [{"properties": {"NAMEUNIT": "Barcelona"}}]
    finally:
        arome_wcs._load_forecast_regions_geojson_sin_cache = original
        arome_wcs.cache_clear()


def test_translating_does_not_need_streamlit(sin_streamlit) -> None:
    """El idioma va por contexto, no por ``st.session_state``.

    Ese estado era global al proceso: dos peticiones en idiomas distintos se
    pisaban, y el router de climatología necesitaba un candado que serializaba
    la construcción de todas las tablas. Con ``ContextVar`` cada una tiene el
    suyo y el candado sobra.
    """
    for nombre in [n for n in sys.modules if n.startswith("domain.i18n_catalog")]:
        del sys.modules[nombre]
    i18n = importlib.import_module("domain.i18n_catalog")

    assert i18n.get_language() == i18n.DEFAULT_LANG
    with i18n.language_scope("en"):
        assert i18n.get_language() == "en"
        en_ingles = i18n.t("warnings.missing_elevation")
    # Al salir del bloque se restaura: no queda idioma pegado para el siguiente.
    assert i18n.get_language() == i18n.DEFAULT_LANG
    assert en_ingles != i18n.t("warnings.missing_elevation")

    # Un idioma desconocido cae al de por defecto en vez de romper.
    with i18n.language_scope("klingon"):
        assert i18n.get_language() == i18n.DEFAULT_LANG


def test_the_climo_router_no_longer_serialises_on_a_language_lock() -> None:
    """El candado existía solo por el estado global; si vuelve, es que alguien
    ha reintroducido el problema."""
    from pathlib import Path

    fuente = Path("server/routers/climo.py").read_text(encoding="utf-8")
    assert "_LANGUAGE_LOCK" not in fuente
    assert "language_scope" in fuente


def test_utils_no_longer_carries_streamlit_names() -> None:
    """``utils`` ya no reexporta nada de Streamlit.

    Cargaba ``i18n`` y ``storage`` al importar el paquete, así que un
    ``from utils.units import ...`` arrastraba Streamlit entero. Primero se
    hicieron perezosos y luego, retirada la interfaz, desaparecieron: lo que
    traduce sin pantalla es ``domain.i18n_catalog``.
    """
    import utils

    assert callable(utils.html_clean)          # lo puro sigue estando
    for retirado in ("t", "set_language", "get_stored_station"):
        with pytest.raises(AttributeError):
            getattr(utils, retirado)


def test_the_visual_stack_is_not_a_dependency_any_more() -> None:
    """``requirements.txt`` no puede volver a traerlos.

    Iban fijados con ``==`` porque el CSS de la interfaz dependía de los
    internals del DOM de Streamlit. Retirada la interfaz, instalarlos en cada
    despliegue es peso muerto.
    """
    from pathlib import Path

    requisitos = Path("requirements.txt").read_text(encoding="utf-8")
    declarados = [
        linea.split("=")[0].split(">")[0].split("<")[0].strip().lower()
        for linea in requisitos.splitlines()
        if linea.strip() and not linea.lstrip().startswith("#")
    ]
    for paquete in ("streamlit", "streamlit-autorefresh", "plotly", "pydeck"):
        assert paquete not in declarados, f"{paquete} ha vuelto a requirements.txt"
