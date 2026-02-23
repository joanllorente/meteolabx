"""
Gestión de LocalStorage del navegador
"""
import json
from typing import Optional

from streamlit_local_storage import LocalStorage
from config import LS_STATION, LS_APIKEY, LS_Z, LS_AUTOCONNECT, LS_AUTOCONNECT_TARGET

# Instancia global de LocalStorage
localS = LocalStorage()


def _mk_key(prefix: str, item_key: str, key_suffix: str) -> str:
    """
    Genera una key estable para los componentes de streamlit_local_storage.
    NO cambia ninguna variable externa, solo ayuda a evitar colisiones.
    """
    if key_suffix:
        return f"mlx_{prefix}_{item_key}_{key_suffix}"
    return f"mlx_{prefix}_{item_key}"


def set_local_storage(item_key: str, value, key_suffix: str) -> None:
    """Guarda un valor en LocalStorage (o lo borra si value es None/'')"""
    try:
        k = _mk_key("set", item_key, key_suffix)

        # Si queremos "olvidar", intentamos borrar
        if value is None or value == "":
            # Intentar métodos de borrado si existen
            for method_name in ("removeItem", "deleteItem", "delItem", "remove"):
                fn = getattr(localS, method_name, None)
                if callable(fn):
                    try:
                        # Algunas libs aceptan key=..., otras no
                        try:
                            fn(item_key, key=k)
                        except TypeError:
                            fn(item_key)
                        return
                    except Exception:
                        pass

            # Fallback: si no hay método de borrado, guardamos vacío
            try:
                localS.setItem(item_key, "", key=k)
            except TypeError:
                localS.setItem(item_key, "")
            return

        # Guardado normal
        try:
            localS.setItem(item_key, value, key=k)
        except TypeError:
            localS.setItem(item_key, value)

    except Exception:
        # Falla silenciosamente si hay problemas
        pass


def get_stored_station():
    """Obtiene Station ID guardada"""
    try:
        # key estable para evitar colisiones si se llama varias veces
        try:
            return localS.getItem(LS_STATION, key="mlx_get_station")
        except TypeError:
            return localS.getItem(LS_STATION)
    except Exception:
        return None


def get_stored_apikey():
    """Obtiene API Key guardada"""
    try:
        try:
            return localS.getItem(LS_APIKEY, key="mlx_get_apikey")
        except TypeError:
            return localS.getItem(LS_APIKEY)
    except Exception:
        return None


def get_stored_z():
    """Obtiene altitud guardada"""
    try:
        try:
            return localS.getItem(LS_Z, key="mlx_get_z")
        except TypeError:
            return localS.getItem(LS_Z)
    except Exception:
        return None


def get_stored_autoconnect():
    """Obtiene la preferencia de autoconexion guardada (bool)."""
    try:
        try:
            raw = localS.getItem(LS_AUTOCONNECT, key="mlx_get_autoconnect")
        except TypeError:
            raw = localS.getItem(LS_AUTOCONNECT)
    except Exception:
        return False

    if isinstance(raw, bool):
        return raw

    txt = str(raw or "").strip().lower()
    return txt in ("1", "true", "yes", "si", "on")


def set_stored_autoconnect_target(target: Optional[dict]):
    """Guarda el objetivo de autoconexión (WU o proveedor) en localStorage."""
    if not target:
        set_local_storage(LS_AUTOCONNECT_TARGET, "", "forget")
        return

    try:
        payload = json.dumps(target, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        return

    set_local_storage(LS_AUTOCONNECT_TARGET, payload, "save")


def get_stored_autoconnect_target():
    """Obtiene el objetivo de autoconexión guardado."""
    try:
        try:
            raw = localS.getItem(LS_AUTOCONNECT_TARGET, key="mlx_get_autoconnect_target")
        except TypeError:
            raw = localS.getItem(LS_AUTOCONNECT_TARGET)
    except Exception:
        return None

    if isinstance(raw, dict):
        return raw

    txt = str(raw or "").strip()
    if not txt:
        return None

    try:
        payload = json.loads(txt)
    except Exception:
        return None

    return payload if isinstance(payload, dict) else None
