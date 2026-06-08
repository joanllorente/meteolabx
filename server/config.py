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

from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # --- Provider API keys ---
    aemet_api_key: str = ""
    """
    API key de AEMET OpenData (server-side). A diferencia de WU, donde
    cada usuario aporta su propia key, AEMET usa una key compartida del
    servidor. Si está vacía, los endpoints AEMET responden con
    ``provider_unauthorized``. Configurar vía ``METEOLABX_AEMET_API_KEY``.
    """

    # --- Logging ---
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_prefix="METEOLABX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

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
