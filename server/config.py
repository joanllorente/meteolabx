"""
Configuración centralizada del backend FastAPI.

Una sola fuente de verdad para variables de entorno (sustituye al uso
disperso de ``st.secrets`` / ``os.environ`` / archivos ``keys/`` que hay
ahora en la app Streamlit). Streamlit puede seguir usando lo suyo durante
la convivencia; este módulo es solo del backend.

Uso:

    from server.config import get_settings
    settings = get_settings()
    print(settings.cors_origins)

Las settings se cargan una sola vez (``lru_cache``) y se inyectan en los
endpoints vía ``Depends(get_settings)``.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import List

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    Settings del backend. Todos los campos tienen defaults razonables para
    desarrollo local; en producción se sobreescriben con env vars con
    prefijo ``METEOLABX_`` (ej.: ``METEOLABX_CORS_ORIGINS``).
    """

    # --- API ---
    api_version: str = "v1"
    """Prefijo de versión para todas las rutas; ej.: ``/v1/health``."""

    debug: bool = False
    """Habilita /docs, /redoc y traces detallados."""

    # --- CORS ---
    cors_origins: List[str] = Field(
        default_factory=lambda: ["http://localhost:8501", "http://127.0.0.1:8501"],
    )
    """
    Orígenes permitidos para CORS. Por defecto solo el Streamlit local.
    En producción: lista del dominio real (``meteolabx.com``, etc.).
    Soporta lista separada por comas en env var:
    ``METEOLABX_CORS_ORIGINS=https://foo.com,https://bar.com``.
    """

    # --- Cache ---
    default_cache_ttl_s: int = 30
    """TTL por defecto para datos meteorológicos en caché (segundos)."""

    cache_ttl_current_s: float = 30.0
    """
    TTL del caché de ``/observations/current``. Coincide con
    ``REFRESH_SECONDS`` del frontend; valores menores aumentan carga al
    proveedor sin ganar frescura visible.
    """

    cache_ttl_series_s: float = 300.0
    """
    TTL del caché de ``/observations/series/today``. Las series del día
    cambian despacio (5-15 min entre puntos), 5 minutos es buen balance
    entre frescura y consumo de API key.
    """

    cache_max_entries: int = 500
    """
    Tope global de entradas por caché. Con 500 estaciones distintas
    cachéadas a la vez gastamos ~500 KB; suficiente para cientos de
    usuarios concurrentes en distintas estaciones.
    """

    ranking_refresh_interval_s: float = 3600.0
    """
    Cadencia del job que refresca el ranking de estaciones (segundos). Por
    defecto 60 min: el ranking es de agregados DIARIOS (máx/mín/racha/lluvia del
    día), que cambian despacio, así que refrescar más a menudo no aporta y solo
    machaca las APIs. Importa sobre todo por IEM (~433 llamadas/ciclo): a 60 min
    son ~10k/día en vez de ~21k. NOTA: el ciclo va ALINEADO a la hora (ver
    ``ranking_refresh_offset_min``); este intervalo solo se usa como referencia.
    Configurar vía ``METEOLABX_RANKING_REFRESH_INTERVAL_S``.
    """

    ranking_refresh_offset_min: int = 5
    """
    Minuto de cada hora en que se lanza el ciclo COMPLETO del ranking (por
    defecto :05). Se alinea justo DESPUÉS de la publicación horaria de los
    proveedores (METAR/synop salen ~en punto) para pillar el dato fresco, en vez
    de a minutos arbitrarios. Al arrancar se hace un ciclo inmediato igualmente.
    Configurar vía ``METEOLABX_RANKING_REFRESH_OFFSET_MIN``.
    """

    ranking_retry_interval_s: float = 60.0
    """
    Si un proveedor falla en un ciclo del ranking, se reintenta (solo ese)
    tras este intervalo (segundos, por defecto 1 min) en vez de esperar el
    ciclo completo. Configurar vía ``METEOLABX_RANKING_RETRY_INTERVAL_S``.
    """

    ranking_state_path: str = ""
    """
    Ruta del snapshot en disco del ``RankingStore`` (gzip JSON). Si se define,
    el estado del ranking (días anteriores + horas acumuladas de AEMET/
    Meteo-France) sobrevive a reinicios y redeploys: se guarda tras cada ciclo
    y se restaura al arrancar. Vacío → sin persistencia (comportamiento
    histórico). En Railway no hace falta configurarlo: si hay un Volume
    adjunto, se usa automáticamente ``$RAILWAY_VOLUME_MOUNT_PATH/
    ranking_state.json.gz``. Configurar vía ``METEOLABX_RANKING_STATE_PATH``.
    """

    usage_stats_path: str = ""
    """
    Ruta del sqlite de estadísticas internas de uso (visitas por estación).
    Vacío → ``$RAILWAY_VOLUME_MOUNT_PATH/usage_stats.sqlite`` si hay Volume,
    o ``data/usage_stats.sqlite`` en local. Configurar vía
    ``METEOLABX_USAGE_STATS_PATH``.
    """

    stats_admin_password: str = "admin"
    """
    Contraseña del panel interno de estadísticas (se introduce en el campo
    API key del formulario WU junto al id especial ``Statics_admin``).
    Cadena vacía → panel deshabilitado (404). CAMBIARLA en producción vía
    ``METEOLABX_STATS_ADMIN_PASSWORD``.
    """

    # --- Provider API keys ---
    aemet_api_key: str = ""
    """
    API key de AEMET OpenData (server-side). A diferencia de WU, donde
    cada usuario aporta su propia key, AEMET usa una key compartida del
    servidor. Si está vacía, los endpoints AEMET responden con
    ``provider_unauthorized``. Configurar vía ``METEOLABX_AEMET_API_KEY``.
    """

    meteocat_api_key: str = ""
    """
    API key de Meteocat XEMA (server-side, mismo modelo que AEMET: key
    compartida del servidor, no per-user). Si está vacía, los endpoints
    METEOCAT responden con ``provider_unauthorized``. Configurar vía
    ``METEOLABX_METEOCAT_API_KEY``.
    """

    euskalmet_jwt: str = ""
    """
    JWT manual de Euskalmet (``METEOLABX_EUSKALMET_JWT``). Si está
    vacío, el servicio intenta autogenerarlo firmando con la clave
    privada PEM (``euskalmet_private_key_path`` o
    ``keys/euskalmet/privateKey.pem`` del repo).
    """

    euskalmet_api_key: str = ""
    """API key opcional de Euskalmet (headers ``apikey``/``x-api-key``)."""

    euskalmet_private_key_path: str = ""
    """Ruta a la clave privada PEM para autogenerar el JWT de Euskalmet."""

    euskalmet_private_key_pem: str = ""
    """
    Contenido PEM de la clave privada de Euskalmet, como alternativa a
    ``euskalmet_private_key_path`` en plataformas SIN sistema de ficheros
    persistente ni acceso al repo (p. ej. Railway, donde ``keys/`` está en
    ``.gitignore`` y no se despliega). Si está y la ruta configurada no
    apunta a un fichero existente, el lifespan del backend lo materializa a
    un fichero temporal (0600) y ajusta ``euskalmet_private_key_path`` para
    que la firma del JWT con ``openssl`` funcione. Configurar vía
    ``METEOLABX_EUSKALMET_PRIVATE_KEY_PEM`` (pega el contenido del ``.pem``).
    """

    euskalmet_jwt_iss: str = "meteolabx"
    """Claim ``iss`` del JWT autogenerado."""

    euskalmet_jwt_email: str = "meteolabx@gmail.com"
    """Claim ``email`` del JWT autogenerado. DEBE ser la cuenta registrada en
    api.euskadi.eus; si va vacío/incorrecto, Euskalmet rechaza CADA lectura con
    HTTP 500. Mismo default que el legacy (``EUSKALMET_JWT_EMAIL``)."""

    meteofrance_api_key: str = ""
    """
    API key de Météo-France DPObs (server-side, header ``apikey``).
    Configurar vía ``METEOLABX_METEOFRANCE_API_KEY``. Cuota 50 req/min.
    """

    metoffice_api_key: str = ""
    """
    API key de Met Office Weather DataHub (server-side, header
    ``apikey``). Configurar vía ``METEOLABX_METOFFICE_API_KEY``.
    """

    frost_client_id: str = ""
    """
    Client ID de Frost (frost.met.no, HTTP Basic). Configurar vía
    ``METEOLABX_FROST_CLIENT_ID``.
    """

    frost_client_secret: str = ""
    """
    Client secret de Frost (frost.met.no, HTTP Basic). Configurar vía
    ``METEOLABX_FROST_CLIENT_SECRET``.
    """

    # --- POEM (Puertos del Estado) — auth opcional ---
    poem_bearer_token: str = ""
    """Token Bearer opcional para POEM (``METEOLABX_POEM_BEARER_TOKEN``)."""

    poem_api_key: str = ""
    """API key opcional para POEM (``METEOLABX_POEM_API_KEY``)."""

    poem_api_key_header: str = "X-API-Key"
    """Header donde viaja la API key de POEM."""

    poem_basic_user: str = ""
    """Usuario HTTP Basic opcional para POEM."""

    poem_basic_password: str = ""
    """Password HTTP Basic opcional para POEM."""

    # --- Logging ---
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_prefix="METEOLABX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def _resolve_environment_aliases(self) -> "Settings":
        """
        Admite temporalmente los nombres de entorno antiguos, pero nunca
        lee credenciales de archivos ni de constantes del código.
        """
        def _fallback(current: str, legacy_env: str) -> str:
            if str(current or "").strip():
                return current
            return os.getenv(legacy_env, "").strip()

        self.aemet_api_key = _fallback(self.aemet_api_key, "AEMET_API_KEY")
        self.meteocat_api_key = _fallback(self.meteocat_api_key, "METEOCAT_API_KEY")
        self.meteofrance_api_key = _fallback(self.meteofrance_api_key, "METEOFRANCE_API_KEY")
        self.metoffice_api_key = _fallback(self.metoffice_api_key, "METOFFICE_API_KEY")
        self.frost_client_id = _fallback(self.frost_client_id, "FROST_CLIENT_ID")
        self.frost_client_secret = _fallback(self.frost_client_secret, "FROST_CLIENT_SECRET")
        self.euskalmet_jwt = _fallback(self.euskalmet_jwt, "EUSKALMET_JWT")
        self.euskalmet_api_key = _fallback(self.euskalmet_api_key, "EUSKALMET_API_KEY")
        self.euskalmet_private_key_path = _fallback(
            self.euskalmet_private_key_path, "EUSKALMET_PRIVATE_KEY_PATH",
        )
        self.euskalmet_private_key_pem = _fallback(
            self.euskalmet_private_key_pem, "EUSKALMET_PRIVATE_KEY_PEM",
        )
        self.poem_bearer_token = _fallback(self.poem_bearer_token, "POEM_BEARER_TOKEN")
        self.poem_api_key = _fallback(self.poem_api_key, "POEM_API_KEY")
        self.poem_basic_user = _fallback(self.poem_basic_user, "POEM_BASIC_USER")
        self.poem_basic_password = _fallback(self.poem_basic_password, "POEM_BASIC_PASSWORD")
        return self

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value):
        """Acepta lista separada por comas en env var."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Devuelve la instancia única de Settings.

    Cacheado con ``lru_cache`` para no releer el entorno en cada request.
    En tests se puede limpiar con ``get_settings.cache_clear()``.
    """
    return Settings()
