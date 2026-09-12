"""
MeteoLabX backend (FastAPI).

Convive con la app Streamlit existente. El frontend Streamlit consumirá estos
endpoints progresivamente; mientras tanto sigue funcionando como hasta ahora.
"""

from version import APP_VERSION as _APP_VERSION

__version__ = _APP_VERSION
