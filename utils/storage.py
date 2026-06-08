"""
Gestión de LocalStorage del navegador
"""
import json
import logging
from uuid import uuid4
from typing import Optional

import streamlit as st
from streamlit_local_storage import LocalStorage
from config import (
    LS_STATION,
    LS_APIKEY,
    LS_Z,
    LS_AUTOCONNECT,
    LS_AUTOCONNECT_TARGET,
    LS_WEATHERLINK_APIKEY,
    LS_WEATHERLINK_APISECRET,
    LS_WEATHERLINK_STATION,
    LS_WEATHERLINK_Z,
    LS_WU_CALIBRATIONS,
    LS_UNIT_PREFERENCES,
)
from utils.units import DEFAULT_UNIT_PREFERENCES, normalize_unit_preferences

logger = logging.getLogger(__name__)

_FORGET_MARKER = "__MLX_FORGOTTEN__"

# Keys legacy que la librería puede haber creado en versiones anteriores
# (sin prefijo "active_"). Se incluyen en el borrado para limpiar datos residuales.
_LS_LEGACY_STATION = "meteolabx_station"
_LS_LEGACY_APIKEY  = "meteolabx_apikey"
_LS_LEGACY_Z       = "meteolabx_z"

_SNAPSHOT_STATE_KEY = "_mlx_local_storage_snapshot"
_SNAPSHOT_READY_KEY = "_mlx_local_storage_snapshot_ready"
_SESSION_AUTOCONNECT_TARGET_KEY = "_mlx_session_autoconnect_target"
_SESSION_AUTOCONNECT_ENABLED_KEY = "_mlx_session_autoconnect_enabled"
_PENDING_WRITES_KEY = "_mlx_local_storage_pending_writes"
_WRITE_CACHE_KEY = "_mlx_local_storage_write_cache"


def _migrate_legacy_unit_preferences(payload) -> dict:
    """
    Corrige el antiguo perfil por defecto que arrancaba temperatura en Kelvin.
    Solo migra el caso legacy exacto para no pisar elecciones reales del usuario.
    """
    normalized = normalize_unit_preferences(payload if isinstance(payload, dict) else None)
    looks_like_legacy_default = (
        normalized.get("temperature") == "k"
        and normalized.get("wind") == DEFAULT_UNIT_PREFERENCES["wind"]
        and normalized.get("pressure") == DEFAULT_UNIT_PREFERENCES["pressure"]
        and normalized.get("precip") == DEFAULT_UNIT_PREFERENCES["precip"]
        and normalized.get("radiation") == DEFAULT_UNIT_PREFERENCES["radiation"]
    )
    if looks_like_legacy_default:
        normalized["temperature"] = DEFAULT_UNIT_PREFERENCES["temperature"]
    return normalized


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
    except Exception as exc:
        logger.warning("No se pudo resolver la key de sesión para localStorage: %s", exc)
        return "mlx_storage_fallback"


def _get_local_storage() -> Optional[LocalStorage]:
    """
    Crea una instancia de ``LocalStorage`` ligada a la sesión actual.

    IMPORTANTE: NO cachear la instancia entre llamadas. ``streamlit_local_storage``
    renderiza widgets de Streamlit internamente cada vez que se llama a
    ``setItem``/``getItem``, y reusar la misma instancia para varias
    operaciones provoca colisiones del estilo
    ``"multiple elements with the same key='set'"`` y warnings tipo
    ``'NoneType' object does not support item assignment``. Cada llamada
    necesita su propia instancia para que el componente reciba una key fresca.
    """
    try:
        return LocalStorage(key=_session_storage_key())
    except TypeError as exc:
        logger.error(
            "LocalStorage no acepta key de sesión; se desactiva la persistencia local por seguridad: %s",
            exc,
        )
        return None
    except Exception as exc:
        logger.warning("No se pudo inicializar LocalStorage: %s", exc)
        return None


def _merge_session_storage_cache(updates: dict, *, authoritative: bool = False) -> None:
    """
    Mantiene una copia en ``st.session_state`` de valores de localStorage.

    ``streamlit_local_storage`` crea instancias nuevas y su cache interno puede
    llegar vacío justo después de un rerun. Para evitar carreras, distinguimos
    snapshots pasivos del navegador de escrituras reales hechas por la app:
    solo las escrituras autoritativas ganan en lecturas inmediatas.
    """
    if not isinstance(updates, dict) or not updates:
        return
    try:
        session_key = _session_storage_key()
        normalized = {str(item_key): value for item_key, value in updates.items()}
        cached = st.session_state.get(session_key)
        if not isinstance(cached, dict):
            cached = {}
        cached.update(normalized)
        st.session_state[session_key] = cached

        if authoritative:
            write_cached = st.session_state.get(_WRITE_CACHE_KEY)
            if not isinstance(write_cached, dict):
                write_cached = {}
            write_cached.update(normalized)
            st.session_state[_WRITE_CACHE_KEY] = write_cached
    except Exception:
        pass


def _session_cached_item(item_key: str) -> tuple[bool, str]:
    try:
        cached = st.session_state.get(_WRITE_CACHE_KEY)
    except Exception:
        return False, ""
    if not isinstance(cached, dict) or item_key not in cached:
        return False, ""
    return True, _unwrap_ls_value(cached.get(item_key), item_key)


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
            return _unwrap_ls_value(inner, item_key)
        # Si solo hay un valor en el dict, devolverlo
        if len(raw) == 1:
            inner = next(iter(raw.values()))
            return _unwrap_ls_value(inner, item_key)
        return ""
    # Caso string normal
    text = str(raw).strip()
    if text == "[object Object]":
        return ""
    return text


def hydrate_local_storage_snapshot(values: Optional[dict]) -> None:
    """Guarda en session_state el snapshot fiable emitido por el navegador."""
    if not isinstance(values, dict):
        return
    normalized = {
        str(item_key): _unwrap_ls_value(value, str(item_key))
        for item_key, value in values.items()
    }
    previous = st.session_state.get(_SNAPSHOT_STATE_KEY)
    if isinstance(previous, dict):
        merged = dict(previous)
        for item_key, value in normalized.items():
            previous_value = _unwrap_ls_value(previous.get(item_key), item_key)
            # Un primer snapshot vacío puede llegar desde el componente antes
            # de que el navegador haya reconciliado localStorage. No dejes que
            # ese vacío borre una intención ya conocida en la sesión; las
            # acciones reales de "Olvidar" usan _FORGET_MARKER o "0".
            if value == "" and previous_value:
                continue
            merged[item_key] = value
        normalized = merged
    st.session_state[_SNAPSHOT_STATE_KEY] = normalized
    st.session_state[_SNAPSHOT_READY_KEY] = True
    _merge_session_storage_cache(normalized)


def local_storage_snapshot_ready() -> bool:
    """Indica si el bridge propio ya ha devuelto datos del navegador."""
    return bool(st.session_state.get(_SNAPSHOT_READY_KEY, False))


def _snapshot_item(item_key: str) -> tuple[bool, str]:
    snapshot = st.session_state.get(_SNAPSHOT_STATE_KEY)
    if not isinstance(snapshot, dict):
        return False, ""
    if item_key not in snapshot:
        return False, ""
    return True, _unwrap_ls_value(snapshot.get(item_key), item_key)


def queue_local_storage_writes(updates: dict) -> None:
    """Encola escrituras para enviarlas al navegador en una sola operación."""
    if not isinstance(updates, dict) or not updates:
        return
    try:
        pending = st.session_state.get(_PENDING_WRITES_KEY)
        if not isinstance(pending, dict):
            pending = {}
        snapshot = st.session_state.get(_SNAPSHOT_STATE_KEY)
        if not isinstance(snapshot, dict):
            snapshot = {}
        for item_key, value in updates.items():
            key = str(item_key)
            pending[key] = value
            snapshot[key] = _unwrap_ls_value(value, key)
        st.session_state[_PENDING_WRITES_KEY] = pending
        st.session_state[_SNAPSHOT_STATE_KEY] = snapshot
        st.session_state[_SNAPSHOT_READY_KEY] = True
        _merge_session_storage_cache(updates, authoritative=True)
    except Exception as exc:
        logger.warning("No se pudieron encolar escrituras de localStorage: %s", exc)


def consume_local_storage_writes() -> dict:
    """Extrae las escrituras pendientes para renderizarlas con el bridge."""
    try:
        pending = st.session_state.pop(_PENDING_WRITES_KEY, {})
    except Exception:
        return {}
    return pending if isinstance(pending, dict) else {}


def flush_local_storage_writes(component_key: str = "mlx_local_storage_flush") -> None:
    """Renderiza una única escritura batch pendiente hacia localStorage."""
    pending = consume_local_storage_writes()
    if not pending:
        return
    try:
        from local_storage_bridge import sync_local_storage

        seq_key = f"{component_key}_seq"
        seq = int(st.session_state.get(seq_key, 0) or 0) + 1
        st.session_state[seq_key] = seq
        sync_local_storage(
            keys=list(pending.keys()),
            writes=pending,
            emit=False,
            key=f"{component_key}_{seq}",
        )
    except Exception as exc:
        logger.warning("No se pudieron volcar escrituras de localStorage: %s", exc)
        queue_local_storage_writes(pending)


def forget_local_storage_keys() -> None:
    """
    Marca todas las keys de credenciales como olvidadas en el localStorage
    mediante la cola batch del bridge propio.

    También borra keys legacy que versiones anteriores pudieron haber escrito.
    """
    from config import LS_WU_FORGOTTEN

    # Keys actuales a marcar con el marcador de olvidado
    keys_to_forget = [LS_STATION, LS_APIKEY, LS_Z, LS_AUTOCONNECT_TARGET, LS_WU_CALIBRATIONS]
    # Keys legacy a marcar también (pueden tener datos de versiones anteriores)
    legacy_keys = [_LS_LEGACY_STATION, _LS_LEGACY_APIKEY, _LS_LEGACY_Z]

    # Actualizar el caché Python para que el rerun inmediato vea campos vacíos
    updates = {k: _FORGET_MARKER for k in keys_to_forget + legacy_keys}
    updates[LS_AUTOCONNECT] = "0"
    updates[LS_WU_FORGOTTEN] = "1"
    queue_local_storage_writes(updates)


def forget_weatherlink_local_storage_keys() -> None:
    """Marca las credenciales WeatherLink como olvidadas sin tocar WU."""
    updates = {
        LS_WEATHERLINK_APIKEY: _FORGET_MARKER,
        LS_WEATHERLINK_APISECRET: _FORGET_MARKER,
        LS_WEATHERLINK_Z: _FORGET_MARKER,
        LS_WEATHERLINK_STATION: _FORGET_MARKER,
    }
    queue_local_storage_writes(updates)


def set_local_storage(item_key: str, value, key_suffix: str) -> None:
    """Guarda un valor en LocalStorage (o lo marca como olvidado si value es None/'')"""
    try:
        if value is None or value == "":
            forget_value = "0" if item_key == LS_AUTOCONNECT else _FORGET_MARKER
            queue_local_storage_writes({item_key: forget_value})
            return

        queue_local_storage_writes({item_key: value})

    except Exception as exc:
        logger.warning("Error general escribiendo %s en localStorage: %s", item_key, exc)
        pass


def _read_cached_or_snapshot_item(item_key: str) -> tuple[bool, str]:
    has_cached, cached_value = _session_cached_item(item_key)
    if has_cached:
        return True, cached_value
    has_snapshot, snapshot_value = _snapshot_item(item_key)
    if has_snapshot:
        return True, snapshot_value
    return False, ""


def _read_ls_item(storage: LocalStorage, item_key: str, getter_key: str) -> str:
    """Lee una key del localStorage y desenvuelve el formato wrapper si es necesario."""
    has_value, value = _read_cached_or_snapshot_item(item_key)
    if has_value:
        return value
    try:
        raw = storage.getItem(item_key, key=getter_key)
    except TypeError:
        raw = storage.getItem(item_key)
    return _unwrap_ls_value(raw, item_key)


def _read_stored_text(item_key: str, getter_key: str) -> str:
    has_value, value = _read_cached_or_snapshot_item(item_key)
    if has_value:
        return value
    storage = _get_local_storage()
    if storage is None:
        return ""
    return _read_ls_item(storage, item_key, getter_key)


def get_stored_station():
    """Obtiene Station ID guardada"""
    try:
        txt = _read_stored_text(LS_STATION, "mlx_get_station")
    except Exception:
        return None
    if not txt or txt == _FORGET_MARKER:
        return None
    return txt


def get_stored_apikey():
    """Obtiene API Key guardada"""
    try:
        txt = _read_stored_text(LS_APIKEY, "mlx_get_apikey")
    except Exception:
        return None
    if not txt or txt == _FORGET_MARKER:
        return None
    return txt


def get_stored_z():
    """Obtiene altitud guardada"""
    try:
        txt = _read_stored_text(LS_Z, "mlx_get_z")
    except Exception:
        return None
    if not txt or txt == _FORGET_MARKER:
        return None
    return txt


def get_stored_autoconnect():
    """Obtiene la preferencia de autoconexion guardada (bool)."""
    try:
        txt = _read_stored_text(LS_AUTOCONNECT, "mlx_get_autoconnect")
    except Exception:
        return False

    txt = txt.lower()
    if txt in ("", _FORGET_MARKER.lower()):
        return False
    return txt in ("1", "true", "yes", "si", "on")


def set_stored_autoconnect_target(target: Optional[dict]):
    """Guarda el objetivo de autoconexión (WU o proveedor) en localStorage."""
    if not target:
        try:
            st.session_state[_SESSION_AUTOCONNECT_ENABLED_KEY] = False
            st.session_state.pop(_SESSION_AUTOCONNECT_TARGET_KEY, None)
        except Exception:
            pass
        set_local_storage(LS_AUTOCONNECT_TARGET, "", "forget")
        return

    try:
        payload = json.dumps(target, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        return

    try:
        st.session_state[_SESSION_AUTOCONNECT_TARGET_KEY] = dict(target)
        st.session_state[_SESSION_AUTOCONNECT_ENABLED_KEY] = True
    except Exception:
        pass
    set_local_storage(LS_AUTOCONNECT_TARGET, payload, "save")


def get_stored_autoconnect_target():
    """Obtiene el objetivo de autoconexión guardado."""
    try:
        txt = _read_stored_text(LS_AUTOCONNECT_TARGET, "mlx_get_autoconnect_target")
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
        txt = _read_stored_text(item_key, f"mlx_get_raw_{item_key}")
    except Exception:
        return None

    if not txt or txt == _FORGET_MARKER:
        return None
    return txt


def get_stored_wu_calibrations():
    """Devuelve el mapa completo de calibraciones WU guardadas."""
    txt = get_local_storage_value(LS_WU_CALIBRATIONS)
    if not txt:
        return {}
    try:
        payload = json.loads(txt)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def get_stored_wu_station_calibration(station_id: str):
    """Devuelve la calibración guardada para una estación WU concreta."""
    sid = str(station_id or "").strip().upper()
    if not sid:
        return {}
    payload = get_stored_wu_calibrations()
    station_payload = payload.get(sid, {})
    return station_payload if isinstance(station_payload, dict) else {}


def set_stored_wu_station_calibration(station_id: str, calibration: Optional[dict]):
    """Guarda la calibración WU de una estación dentro del mapa persistente."""
    sid = str(station_id or "").strip().upper()
    if not sid:
        return

    payload = get_stored_wu_calibrations()
    if not isinstance(payload, dict):
        payload = {}

    has_effective_values = False
    if isinstance(calibration, dict):
        for value in calibration.values():
            try:
                if abs(float(value)) > 1e-9:
                    has_effective_values = True
                    break
            except Exception:
                continue

    if has_effective_values:
        payload[sid] = calibration
    else:
        payload.pop(sid, None)

    try:
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        return
    set_local_storage(LS_WU_CALIBRATIONS, raw, "save")


def get_stored_unit_preferences():
    """Devuelve las preferencias de unidades guardadas."""
    txt = get_local_storage_value(LS_UNIT_PREFERENCES)
    if not txt:
        return normalize_unit_preferences(None)
    try:
        payload = json.loads(txt)
    except Exception:
        return normalize_unit_preferences(None)
    normalized = _migrate_legacy_unit_preferences(payload if isinstance(payload, dict) else None)
    if isinstance(payload, dict) and normalize_unit_preferences(payload) != normalized:
        set_stored_unit_preferences(normalized)
    return normalized


def set_stored_unit_preferences(preferences: Optional[dict]):
    """Guarda las preferencias de unidades en localStorage."""
    payload = normalize_unit_preferences(preferences if isinstance(preferences, dict) else None)
    try:
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        return
    set_local_storage(LS_UNIT_PREFERENCES, raw, "save")
