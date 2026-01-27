"""
Gestión de LocalStorage del navegador
"""
from streamlit_local_storage import LocalStorage
from config import LS_STATION, LS_APIKEY, LS_Z

# Instancia global de LocalStorage
localS = LocalStorage()


def set_local_storage(item_key: str, value, key_suffix: str) -> None:
    """Guarda un valor en LocalStorage"""
    try:
        localS.setItem(item_key, value)
    except:
        pass  # Falla silenciosamente si hay problemas


def get_stored_station():
    """Obtiene Station ID guardada"""
    try:
        return localS.getItem(LS_STATION)
    except:
        return None


def get_stored_apikey():
    """Obtiene API Key guardada"""
    try:
        return localS.getItem(LS_APIKEY)
    except:
        return None


def get_stored_z():
    """Obtiene altitud guardada"""
    try:
        return localS.getItem(LS_Z)
    except:
        return None
