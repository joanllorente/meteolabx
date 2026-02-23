"""
Gestión de LocalStorage del navegador
"""
import json
from uuid import uuid4
from typing import Optional

import streamlit as st
from streamlit_local_storage import LocalStorage
from config import LS_STATION, LS_APIKEY, LS_Z, LS_AUTOCONNECT, LS_AUTOCONNECT_TARGET

_FORGET_MARKER = "__MLX_FORGOTTEN__"


def _session_storage_key() -> str:
    """
    Devuelve una key estable por sesión para el componente de localStorage.
    Evita compartir accidentalmente estado en memoria entre sesiones.
    """
    try:
        key = str(st.session_state.get("_mlx_local_storage_key", "")).strip()
        if not key:
            key = f"mlx_storage_{uuid4().hex}"
            st.session_state["_mlx_local_storage_key"] = key
        return key
    except Exception:
        return "mlx_storage_fallback"


def _get_local_storage() -> Optional[LocalStorage]:
    """
    Crea una instancia de LocalStorage ligada a la sesión actual.
    Nunca reutiliza un singleton global con estado interno compartido.
    """
    try:
        return LocalStorage(key=_session_storage_key())
    except TypeError:
        try:
            return LocalStorage()
        except Exception:
            return None
    except Exception:
        return None


class _LocalStorageProxy:
    """
    Proxy compatible con imports legacy (`localS`) sin estado global persistente.
    """

    def __getattr__(self, name):
        storage = _get_local_storage()
        if storage is None:
            raise AttributeError(name)
        return getattr(storage, name)


# Compatibilidad legacy: no guarda estado global, delega por llamada.
localS = _LocalStorageProxy()


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
        storage = _get_local_storage()
        if storage is None:
            return

        k = _mk_key("set", item_key, key_suffix)

        # Si queremos "olvidar", intentamos borrar
        if value is None or value == "":
            # Intentar métodos de borrado si existen
            for method_name in ("eraseItem", "deleteItem", "removeItem", "delItem", "remove"):
                fn = getattr(storage, method_name, None)
                if callable(fn):
                    try:
                        # Algunas libs aceptan key=..., otras no
                        try:
                            fn(item_key, key=k)
                        except TypeError:
                            fn(item_key)
                        try:
                            items = getattr(storage, "storedItems", None)
                            if isinstance(items, dict):
                                items.pop(item_key, None)
                        except Exception:
                            pass
                        break
                    except KeyError:
                        # En algunas versiones deleteItem lanza KeyError si no existía.
                        break
                    except Exception:
                        pass

            # Reforzar borrado escribiendo un marcador no utilizable para el flujo de app.
            # Así evitamos cualquier "resurrección" si el método delete no es fiable.
            forget_value = "0" if item_key == LS_AUTOCONNECT else _FORGET_MARKER
            try:
                storage.setItem(item_key, forget_value, key=_mk_key("forgetset", item_key, key_suffix))
            except TypeError:
                storage.setItem(item_key, forget_value)
            except Exception:
                pass
            try:
                items = getattr(storage, "storedItems", None)
                if isinstance(items, dict):
                    items[item_key] = forget_value
            except Exception:
                pass

            # Sincronizar estado local del componente (si lo soporta)
            try:
                refresh_fn = getattr(storage, "refreshItems", None)
                if callable(refresh_fn):
                    refresh_fn()
            except Exception:
                pass
            return

        # Guardado normal
        try:
            storage.setItem(item_key, value, key=k)
        except TypeError:
            storage.setItem(item_key, value)

    except Exception:
        # Falla silenciosamente si hay problemas
        pass


def get_stored_station():
    """Obtiene Station ID guardada"""
    try:
        storage = _get_local_storage()
        if storage is None:
            return None
        # key estable para evitar colisiones si se llama varias veces
        try:
            raw = storage.getItem(LS_STATION, key="mlx_get_station")
        except TypeError:
            raw = storage.getItem(LS_STATION)
    except Exception:
        return None
    txt = str(raw or "").strip()
    if not txt or txt == _FORGET_MARKER:
        return None
    return txt


def get_stored_apikey():
    """Obtiene API Key guardada"""
    try:
        storage = _get_local_storage()
        if storage is None:
            return None
        try:
            raw = storage.getItem(LS_APIKEY, key="mlx_get_apikey")
        except TypeError:
            raw = storage.getItem(LS_APIKEY)
    except Exception:
        return None
    txt = str(raw or "").strip()
    if not txt or txt == _FORGET_MARKER:
        return None
    return txt


def get_stored_z():
    """Obtiene altitud guardada"""
    try:
        storage = _get_local_storage()
        if storage is None:
            return None
        try:
            raw = storage.getItem(LS_Z, key="mlx_get_z")
        except TypeError:
            raw = storage.getItem(LS_Z)
    except Exception:
        return None
    txt = str(raw or "").strip()
    if not txt or txt == _FORGET_MARKER:
        return None
    return txt


def get_stored_autoconnect():
    """Obtiene la preferencia de autoconexion guardada (bool)."""
    try:
        storage = _get_local_storage()
        if storage is None:
            return False
        try:
            raw = storage.getItem(LS_AUTOCONNECT, key="mlx_get_autoconnect")
        except TypeError:
            raw = storage.getItem(LS_AUTOCONNECT)
    except Exception:
        return False

    if isinstance(raw, bool):
        return raw

    txt = str(raw or "").strip().lower()
    if txt in ("", _FORGET_MARKER.lower()):
        return False
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
        storage = _get_local_storage()
        if storage is None:
            return None
        try:
            raw = storage.getItem(LS_AUTOCONNECT_TARGET, key="mlx_get_autoconnect_target")
        except TypeError:
            raw = storage.getItem(LS_AUTOCONNECT_TARGET)
    except Exception:
        return None

    if isinstance(raw, dict):
        return raw

    txt = str(raw or "").strip()
    if not txt or txt == _FORGET_MARKER:
        return None

    try:
        payload = json.loads(txt)
    except Exception:
        return None

    return payload if isinstance(payload, dict) else None
