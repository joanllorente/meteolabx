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

# Keys legacy que la librería puede haber creado en versiones anteriores
# (sin prefijo "active_"). Se incluyen en el borrado para limpiar datos residuales.
_LS_LEGACY_STATION = "meteolabx_station"
_LS_LEGACY_APIKEY  = "meteolabx_apikey"
_LS_LEGACY_Z       = "meteolabx_z"


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


def _unwrap_ls_value(raw, item_key: str) -> str:
    """
    La librería streamlit_local_storage almacena los valores en el navegador
    envueltos en un objeto JSON: {"meteolabx_active_station": "ILHOSP26"}.
    Cuando getItem() lo deserializa, devuelve ese dict Python.
    Esta función extrae el valor real sea cual sea el formato recibido.
    """
    if raw is None:
        return ""
    # Caso normal: la librería ya extrajo el valor escalar
    if isinstance(raw, bool):
        return "1" if raw else "0"
    if isinstance(raw, (int, float)):
        return str(raw).strip()
    # Caso wrapper dict: {"meteolabx_active_station": "ILHOSP26"}
    if isinstance(raw, dict):
        # Intentar extraer por la key exacta primero
        if item_key in raw:
            inner = raw[item_key]
            if isinstance(inner, bool):
                return "1" if inner else "0"
            return str(inner or "").strip()
        # Si solo hay un valor en el dict, devolverlo
        if len(raw) == 1:
            inner = next(iter(raw.values()))
            if isinstance(inner, bool):
                return "1" if inner else "0"
            return str(inner or "").strip()
        return ""
    # Caso string normal
    return str(raw).strip()


def forget_local_storage_keys() -> None:
    """
    Marca todas las keys de credenciales como olvidadas en el localStorage
    usando setItem del componente streamlit_local_storage (que opera en el
    localStorage real de la página, no en un iframe sandboxed).

    Usa UUIDs únicos como key de componente para garantizar que Streamlit
    envíe el widget al frontend en este ciclo de render.
    También borra keys legacy que versiones anteriores pudieron haber escrito.
    """
    from config import LS_WU_FORGOTTEN

    storage = _get_local_storage()

    # Keys actuales a marcar con el marcador de olvidado
    keys_to_forget = [LS_STATION, LS_APIKEY, LS_Z, LS_AUTOCONNECT_TARGET]
    # Keys legacy a marcar también (pueden tener datos de versiones anteriores)
    legacy_keys = [_LS_LEGACY_STATION, _LS_LEGACY_APIKEY, _LS_LEGACY_Z]

    if storage is not None:
        for item_key in keys_to_forget + legacy_keys:
            ck = f"mlx_forget_{item_key}_{uuid4().hex[:8]}"
            try:
                storage.setItem(item_key, _FORGET_MARKER, key=ck)
            except TypeError:
                try:
                    storage.setItem(item_key, _FORGET_MARKER)
                except Exception:
                    pass
            except Exception:
                pass

        # LS_AUTOCONNECT: su getter usa "0" como False
        try:
            storage.setItem(LS_AUTOCONNECT, "0",
                            key=f"mlx_forget_{LS_AUTOCONNECT}_{uuid4().hex[:8]}")
        except Exception:
            pass

        # LS_WU_FORGOTTEN = "1" → sidebar detecta estado olvidado en recargas
        try:
            storage.setItem(LS_WU_FORGOTTEN, "1",
                            key=f"mlx_forget_{LS_WU_FORGOTTEN}_{uuid4().hex[:8]}")
        except Exception:
            pass

    # Actualizar el caché Python para que el rerun inmediato vea campos vacíos
    updates = {k: _FORGET_MARKER for k in keys_to_forget + legacy_keys}
    updates[LS_AUTOCONNECT] = "0"
    updates[LS_WU_FORGOTTEN] = "1"

    session_key = _session_storage_key()
    try:
        if storage is not None:
            storage.storedItems.update(updates)
    except Exception:
        pass
    try:
        cached = st.session_state.get(session_key)
        if isinstance(cached, dict):
            cached.update(updates)
            st.session_state[session_key] = cached
    except Exception:
        pass


def set_local_storage(item_key: str, value, key_suffix: str) -> None:
    """Guarda un valor en LocalStorage (o lo marca como olvidado si value es None/'')"""
    try:
        storage = _get_local_storage()
        if storage is None:
            return

        k = _mk_key("set", item_key, key_suffix)

        if value is None or value == "":
            forget_value = "0" if item_key == LS_AUTOCONNECT else _FORGET_MARKER
            forget_key = _mk_key("forget", item_key, key_suffix)
            try:
                storage.setItem(item_key, forget_value, key=forget_key)
            except TypeError:
                try:
                    storage.setItem(item_key, forget_value)
                except Exception:
                    pass
            except Exception:
                pass

            session_key = _session_storage_key()
            try:
                storage.storedItems[item_key] = forget_value
            except Exception:
                pass
            try:
                cached = st.session_state.get(session_key)
                if isinstance(cached, dict):
                    cached[item_key] = forget_value
                    st.session_state[session_key] = cached
            except Exception:
                pass
            return

        # Guardado normal
        try:
            storage.setItem(item_key, value, key=k)
        except TypeError:
            storage.setItem(item_key, value)

    except Exception:
        pass


def _read_ls_item(storage: LocalStorage, item_key: str, getter_key: str) -> str:
    """Lee una key del localStorage y desenvuelve el formato wrapper si es necesario."""
    try:
        raw = storage.getItem(item_key, key=getter_key)
    except TypeError:
        raw = storage.getItem(item_key)
    return _unwrap_ls_value(raw, item_key)


def get_stored_station():
    """Obtiene Station ID guardada"""
    try:
        storage = _get_local_storage()
        if storage is None:
            return None
        txt = _read_ls_item(storage, LS_STATION, "mlx_get_station")
    except Exception:
        return None
    if not txt or txt == _FORGET_MARKER:
        return None
    return txt


def get_stored_apikey():
    """Obtiene API Key guardada"""
    try:
        storage = _get_local_storage()
        if storage is None:
            return None
        txt = _read_ls_item(storage, LS_APIKEY, "mlx_get_apikey")
    except Exception:
        return None
    if not txt or txt == _FORGET_MARKER:
        return None
    return txt


def get_stored_z():
    """Obtiene altitud guardada"""
    try:
        storage = _get_local_storage()
        if storage is None:
            return None
        txt = _read_ls_item(storage, LS_Z, "mlx_get_z")
    except Exception:
        return None
    if not txt or txt == _FORGET_MARKER:
        return None
    return txt


def get_stored_autoconnect():
    """Obtiene la preferencia de autoconexion guardada (bool)."""
    try:
        storage = _get_local_storage()
        if storage is None:
            return False
        txt = _read_ls_item(storage, LS_AUTOCONNECT, "mlx_get_autoconnect")
    except Exception:
        return False

    txt = txt.lower()
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
        txt = _read_ls_item(storage, LS_AUTOCONNECT_TARGET, "mlx_get_autoconnect_target")
    except Exception:
        return None

    if not txt or txt == _FORGET_MARKER:
        return None

    try:
        payload = json.loads(txt)
    except Exception:
        return None

    return payload if isinstance(payload, dict) else None


def get_local_storage_value(item_key: str):
    """Lee una clave arbitraria de localStorage con saneado básico."""
    try:
        storage = _get_local_storage()
        if storage is None:
            return None
        txt = _read_ls_item(storage, item_key, f"mlx_get_raw_{item_key}")
    except Exception:
        return None

    if not txt or txt == _FORGET_MARKER:
        return None
    return txt
