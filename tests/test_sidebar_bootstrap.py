from types import SimpleNamespace

from components import sidebar
from config import LS_APIKEY, LS_AUTOCONNECT, LS_AUTOCONNECT_TARGET, LS_STATION, LS_Z
from utils import storage
from utils.state_keys import AUTOCONNECT_ATTEMPTED
from utils.units import DEFAULT_UNIT_PREFERENCES


class _ColumnStub:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _SidebarStub:
    def __init__(self, session_state=None):
        self.session_state = session_state if session_state is not None else {}
        self.calls = []
        self.info_messages = []
        self.success_messages = []
        self.warning_messages = []
        self.error_messages = []

    def title(self, *args, **kwargs):
        self.calls.append(("title", args, kwargs))

    def markdown(self, *args, **kwargs):
        self.calls.append(("markdown", args, kwargs))

    def caption(self, *args, **kwargs):
        self.calls.append(("caption", args, kwargs))

    def selectbox(self, label, options, **kwargs):
        self.calls.append(("selectbox", (label, options), kwargs))
        return options[0]

    def segmented_control(self, label, options, **kwargs):
        self.calls.append(("segmented_control", (label, options), kwargs))
        return kwargs.get("key") == "theme_selector" and "auto" or options[0]

    def text_input(self, *args, **kwargs):
        self.calls.append(("text_input", args, kwargs))
        return self.session_state.get(kwargs.get("key"), "")

    def toggle(self, *args, **kwargs):
        self.calls.append(("toggle", args, kwargs))
        return bool(self.session_state.get(kwargs.get("key"), False))

    def button(self, *args, **kwargs):
        self.calls.append(("button", args, kwargs))
        return False

    def columns(self, count):
        return tuple(_ColumnStub() for _ in range(count))

    def success(self, message):
        self.success_messages.append(message)

    def info(self, message):
        self.info_messages.append(message)

    def warning(self, message):
        self.warning_messages.append(message)

    def error(self, message):
        self.error_messages.append(message)


class _SidebarStaleWuToggleStub(_SidebarStub):
    def toggle(self, *args, **kwargs):
        key = kwargs.get("key")
        if key == "auto_connect_wu_device":
            self.session_state[key] = True
            callback = kwargs.get("on_change")
            if callable(callback):
                callback()
            return True
        return super().toggle(*args, **kwargs)


def test_sidebar_defers_wu_controls_until_local_storage_snapshot_ready(monkeypatch):
    fake_sidebar = _SidebarStub()
    fake_st = SimpleNamespace(
        session_state={},
        query_params={},
        sidebar=fake_sidebar,
    )
    monkeypatch.setattr(sidebar, "st", fake_st)
    monkeypatch.setattr(sidebar, "sync_local_storage", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "consume_local_storage_writes", lambda: {})
    monkeypatch.setattr(sidebar, "local_storage_snapshot_ready", lambda: False)
    monkeypatch.setattr(sidebar, "flush_local_storage_writes", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "get_stored_unit_preferences", lambda: dict(DEFAULT_UNIT_PREFERENCES))
    monkeypatch.setattr(sidebar, "init_language", lambda: "es")
    monkeypatch.setattr(sidebar, "get_supported_languages", lambda: ["es"])
    monkeypatch.setattr(sidebar, "get_language_label", lambda lang: lang)
    monkeypatch.setattr(sidebar, "set_language", lambda lang: lang)
    monkeypatch.setattr(sidebar, "t", lambda key, **kwargs: key)

    theme_mode, dark = sidebar.render_sidebar()

    assert theme_mode == "auto"
    assert dark in (True, False)
    call_names = [name for name, _args, _kwargs in fake_sidebar.calls]
    assert "sidebar.connection.loading_saved" in [
        args[0] for name, args, _kwargs in fake_sidebar.calls if name == "caption"
    ]
    assert "text_input" not in call_names
    assert "toggle" not in call_names
    assert "button" not in call_names


def test_sidebar_ignores_stale_wu_toggle_callback_during_bootstrap(monkeypatch):
    target = {
        "kind": "WU",
        "station": "ILHOSP26",
        "api_key": "secret-key",
        "z": "39",
    }
    session_state = {
        "_sidebar_inputs_initialized": True,
        "_mlx_local_storage_snapshot_ready": True,
        "_mlx_local_storage_snapshot": {
            LS_STATION: "ILHOSP26",
            LS_APIKEY: "secret-key",
            LS_Z: "39",
            LS_AUTOCONNECT: "1",
            LS_AUTOCONNECT_TARGET: (
                '{"kind":"WU","station":"ILHOSP26","api_key":"secret-key","z":"39"}'
            ),
        },
        "active_station": "",
        "active_key": "",
        "active_z": "",
        "connected": True,
        "connection_type": "WU",
        "wu_connected_station": "ILHOSP26",
        "wu_connected_api_key": "secret-key",
        "wu_connected_z": "39",
        AUTOCONNECT_ATTEMPTED: True,
        "auto_connect_wu_device": False,
        "_wu_autoconnect_toggle_changed": True,
        "_wu_autoconnect_ui_last_value": True,
        "_wu_autoconnect_ui_target_kind": "WU",
    }
    fake_sidebar = _SidebarStub(session_state)
    fake_st = SimpleNamespace(
        session_state=session_state,
        query_params={},
        sidebar=fake_sidebar,
        button=lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(sidebar, "st", fake_st)
    monkeypatch.setattr(sidebar, "sync_local_storage", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "consume_local_storage_writes", lambda: {})
    monkeypatch.setattr(sidebar, "local_storage_snapshot_ready", lambda: True)
    monkeypatch.setattr(sidebar, "flush_local_storage_writes", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "st", fake_st)
    monkeypatch.setattr(storage, "_get_local_storage", lambda: object())
    monkeypatch.setattr(sidebar, "get_stored_unit_preferences", lambda: dict(DEFAULT_UNIT_PREFERENCES))
    monkeypatch.setattr(storage, "get_stored_wu_station_calibration", lambda station_id: {})
    monkeypatch.setattr(sidebar, "init_language", lambda: "es")
    monkeypatch.setattr(sidebar, "get_supported_languages", lambda: ["es"])
    monkeypatch.setattr(sidebar, "get_language_label", lambda lang: lang)
    monkeypatch.setattr(sidebar, "set_language", lambda lang: lang)
    monkeypatch.setattr(sidebar, "t", lambda key, **kwargs: key)

    sidebar.render_sidebar()

    assert session_state["active_station"] == "ILHOSP26"
    assert session_state["active_key"] == "secret-key"
    assert session_state["active_z"] == "39"
    assert session_state[sidebar.WU_STATION_INPUT_KEY] == "ILHOSP26"
    assert session_state[sidebar.WU_API_KEY_INPUT_KEY] == "secret-key"
    assert session_state[sidebar.WU_ALTITUDE_INPUT_KEY] == "39"
    assert session_state["auto_connect_wu_device"] is True
    assert session_state["_wu_autoconnect_ui_last_value"] is True
    assert "sidebar.autoconnect.disabled" not in fake_sidebar.info_messages


def test_sidebar_hydrates_empty_wu_widgets_from_runtime_connection(monkeypatch):
    session_state = {
        "_sidebar_inputs_initialized": True,
        "_mlx_local_storage_snapshot_ready": True,
        "_mlx_local_storage_snapshot": {},
        "active_station": "",
        "active_key": "",
        "active_z": "",
        sidebar.WU_STATION_INPUT_KEY: "",
        sidebar.WU_API_KEY_INPUT_KEY: "",
        sidebar.WU_ALTITUDE_INPUT_KEY: "",
        "connected": True,
        "connection_type": "WU",
        "wu_connected_station": "ILHOSP26",
        "wu_connected_api_key": "secret-key",
        "wu_connected_z": "39",
    }
    fake_sidebar = _SidebarStub(session_state)
    fake_st = SimpleNamespace(
        session_state=session_state,
        query_params={},
        sidebar=fake_sidebar,
        button=lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(sidebar, "st", fake_st)
    monkeypatch.setattr(sidebar, "sync_local_storage", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "consume_local_storage_writes", lambda: {})
    monkeypatch.setattr(sidebar, "local_storage_snapshot_ready", lambda: True)
    monkeypatch.setattr(sidebar, "flush_local_storage_writes", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "st", fake_st)
    monkeypatch.setattr(storage, "_get_local_storage", lambda: object())
    monkeypatch.setattr(storage, "get_stored_wu_station_calibration", lambda station_id: {})
    monkeypatch.setattr(sidebar, "get_stored_unit_preferences", lambda: dict(DEFAULT_UNIT_PREFERENCES))
    monkeypatch.setattr(sidebar, "init_language", lambda: "es")
    monkeypatch.setattr(sidebar, "get_supported_languages", lambda: ["es"])
    monkeypatch.setattr(sidebar, "get_language_label", lambda lang: lang)
    monkeypatch.setattr(sidebar, "set_language", lambda lang: lang)
    monkeypatch.setattr(sidebar, "t", lambda key, **kwargs: key)

    sidebar.render_sidebar()

    assert session_state[sidebar.WU_STATION_INPUT_KEY] == "ILHOSP26"
    assert session_state[sidebar.WU_API_KEY_INPUT_KEY] == "secret-key"
    assert session_state[sidebar.WU_ALTITUDE_INPUT_KEY] == "39"
    assert session_state["active_station"] == "ILHOSP26"
    assert session_state["active_key"] == "secret-key"
    assert session_state["active_z"] == "39"


def test_sidebar_keeps_user_edited_wu_widgets_while_connected(monkeypatch):
    session_state = {
        "_sidebar_inputs_initialized": True,
        "_mlx_local_storage_snapshot_ready": True,
        "_mlx_local_storage_snapshot": {},
        "_wu_inputs_user_edited": True,
        "active_station": "ILHOSP26",
        "active_key": "secret-key",
        "active_z": "39",
        sidebar.WU_STATION_INPUT_KEY: "NEWSTATION",
        sidebar.WU_API_KEY_INPUT_KEY: "new-key",
        sidebar.WU_ALTITUDE_INPUT_KEY: "12",
        "connected": True,
        "connection_type": "WU",
        "wu_connected_station": "ILHOSP26",
        "wu_connected_api_key": "secret-key",
        "wu_connected_z": "39",
    }
    fake_sidebar = _SidebarStub(session_state)
    fake_st = SimpleNamespace(
        session_state=session_state,
        query_params={},
        sidebar=fake_sidebar,
        button=lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(sidebar, "st", fake_st)
    monkeypatch.setattr(sidebar, "sync_local_storage", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "consume_local_storage_writes", lambda: {})
    monkeypatch.setattr(sidebar, "local_storage_snapshot_ready", lambda: True)
    monkeypatch.setattr(sidebar, "flush_local_storage_writes", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "st", fake_st)
    monkeypatch.setattr(storage, "_get_local_storage", lambda: object())
    monkeypatch.setattr(storage, "get_stored_wu_station_calibration", lambda station_id: {})
    monkeypatch.setattr(sidebar, "get_stored_unit_preferences", lambda: dict(DEFAULT_UNIT_PREFERENCES))
    monkeypatch.setattr(sidebar, "init_language", lambda: "es")
    monkeypatch.setattr(sidebar, "get_supported_languages", lambda: ["es"])
    monkeypatch.setattr(sidebar, "get_language_label", lambda lang: lang)
    monkeypatch.setattr(sidebar, "set_language", lambda lang: lang)
    monkeypatch.setattr(sidebar, "t", lambda key, **kwargs: key)

    sidebar.render_sidebar()

    assert session_state[sidebar.WU_STATION_INPUT_KEY] == "NEWSTATION"
    assert session_state[sidebar.WU_API_KEY_INPUT_KEY] == "new-key"
    assert session_state[sidebar.WU_ALTITUDE_INPUT_KEY] == "12"
    assert session_state["active_station"] == "NEWSTATION"
    assert session_state["active_key"] == "new-key"
    assert session_state["active_z"] == "12"


def test_wu_autoconnect_is_not_disabled_by_implicit_widget_value_diff():
    source = sidebar.__file__
    with open(source, "r", encoding="utf-8") as handle:
        text = handle.read()

    assert "if wu_toggle_changed:" in text
    assert "wu_toggle_changed or auto_connect_wu_device != last_wu_toggle_value" not in text
    assert 'key="active_station"' not in text
    assert 'key="active_key"' not in text
    assert 'key="active_z"' not in text


def test_sidebar_ignores_late_phantom_toggle_callback_in_grace_window(monkeypatch):
    """
    Regresión: tras un autoconnect exitoso, Streamlit puede disparar un
    on_change "fantasma" del toggle varios reruns después (al rehidratar el
    frontend con un valor stale). Antes ese callback pasaba las defensas
    existentes y borraba LS_AUTOCONNECT_TARGET, dejando la próxima sesión
    sin posibilidad de autoconectar. La ventana de gracia
    `_wu_autoconnect_post_grace` debe descartar esos callbacks.
    """
    session_state = {
        "_sidebar_inputs_initialized": True,
        "_mlx_local_storage_snapshot_ready": True,
        "_mlx_local_storage_snapshot": {
            LS_STATION: "ILHOSP26",
            LS_APIKEY: "secret-key",
            LS_Z: "39",
            LS_AUTOCONNECT: "1",
            LS_AUTOCONNECT_TARGET: (
                '{"kind":"WU","station":"ILHOSP26","api_key":"secret-key","z":"39"}'
            ),
        },
        "active_station": "ILHOSP26",
        "active_key": "secret-key",
        "active_z": "39",
        sidebar.WU_STATION_INPUT_KEY: "ILHOSP26",
        sidebar.WU_API_KEY_INPUT_KEY: "secret-key",
        sidebar.WU_ALTITUDE_INPUT_KEY: "39",
        "connected": True,
        "connection_type": "WU",
        "wu_connected_station": "ILHOSP26",
        "wu_connected_api_key": "secret-key",
        "wu_connected_z": "39",
        AUTOCONNECT_ATTEMPTED: True,
        # Simulamos el 3.er rerun después del autoconnect: los dos flags de
        # armed YA están a True (se habían seteado en reruns anteriores),
        # pero el callback "desactivador" del toggle llega ahora.
        "_wu_autoconnect_event_armed": True,
        "_wu_autoconnect_disable_armed": True,
        "auto_connect_wu_device": False,  # Streamlit lo rehidrató stale
        "_wu_autoconnect_toggle_changed": True,
        "_wu_autoconnect_ui_last_value": True,
        "_wu_autoconnect_ui_target_kind": "WU",
        # La ventana de gracia aún tiene reruns disponibles.
        "_wu_autoconnect_post_grace": 3,
    }
    fake_sidebar = _SidebarStub(session_state)
    fake_st = SimpleNamespace(
        session_state=session_state,
        query_params={},
        sidebar=fake_sidebar,
        button=lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(sidebar, "st", fake_st)
    monkeypatch.setattr(sidebar, "sync_local_storage", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "consume_local_storage_writes", lambda: {})
    monkeypatch.setattr(sidebar, "local_storage_snapshot_ready", lambda: True)
    monkeypatch.setattr(sidebar, "flush_local_storage_writes", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "st", fake_st)
    monkeypatch.setattr(storage, "_get_local_storage", lambda: object())
    monkeypatch.setattr(sidebar, "get_stored_unit_preferences", lambda: dict(DEFAULT_UNIT_PREFERENCES))
    monkeypatch.setattr(storage, "get_stored_wu_station_calibration", lambda station_id: {})
    monkeypatch.setattr(sidebar, "init_language", lambda: "es")
    monkeypatch.setattr(sidebar, "get_supported_languages", lambda: ["es"])
    monkeypatch.setattr(sidebar, "get_language_label", lambda lang: lang)
    monkeypatch.setattr(sidebar, "set_language", lambda lang: lang)
    monkeypatch.setattr(sidebar, "t", lambda key, **kwargs: key)

    sidebar.render_sidebar()

    # El toggle debe quedar en True (la ventana de gracia anuló el fantasma).
    assert session_state["auto_connect_wu_device"] is True
    # localStorage NO debe haber sido borrado.
    assert session_state["_mlx_local_storage_snapshot"][LS_AUTOCONNECT] == "1"
    assert "kind" in session_state["_mlx_local_storage_snapshot"][LS_AUTOCONNECT_TARGET]
    # El contador debe haber decrementado.
    assert session_state["_wu_autoconnect_post_grace"] == 2
    # No debe haberse mostrado el mensaje de "desactivado".
    assert "sidebar.autoconnect.disabled" not in fake_sidebar.info_messages


def test_sidebar_does_not_overwrite_provider_target_with_wu_phantom_callback(monkeypatch):
    """
    Regresión: al activar el toggle de autoconexión de un proveedor desde el
    mapa/station_selector, se persiste un target ``{"kind": "PROVIDER", ...}``
    y se dispara un rerun. En ese rerun el toggle WU puede ser rehidratado
    por Streamlit con un valor stale ``True`` y disparar on_change como si
    el usuario lo hubiera activado. El bloque que procesa "WU toggle changed
    to True" entonces sobrescribía ``LS_AUTOCONNECT_TARGET`` con kind=WU,
    perdiendo la decisión del usuario. La guardia ``current_kind == "PROVIDER"``
    en el bloque del toggle WU debe descartar ese callback.
    """
    provider_target_json = (
        '{"kind":"PROVIDER","provider_id":"METEOCAT","station_id":"Z6",'
        '"station_name":"Sasseuva","lat":42.7,"lon":1.4,"elevation_m":2228}'
    )
    session_state = {
        "_sidebar_inputs_initialized": True,
        "_mlx_local_storage_snapshot_ready": True,
        "_mlx_local_storage_snapshot": {
            LS_AUTOCONNECT: "1",
            LS_AUTOCONNECT_TARGET: provider_target_json,
        },
        "active_station": "ILHOSP26",
        "active_key": "secret-key",
        "active_z": "39",
        sidebar.WU_STATION_INPUT_KEY: "ILHOSP26",
        sidebar.WU_API_KEY_INPUT_KEY: "secret-key",
        sidebar.WU_ALTITUDE_INPUT_KEY: "39",
        "connected": True,
        "connection_type": "WU",
        "wu_connected_station": "ILHOSP26",
        "wu_connected_api_key": "secret-key",
        "wu_connected_z": "39",
        # Streamlit rehidrata el toggle WU stale a True y dispara on_change.
        "auto_connect_wu_device": True,
        "_wu_autoconnect_toggle_changed": True,
        "_wu_autoconnect_ui_last_value": True,
        "_wu_autoconnect_ui_target_kind": "WU",  # estaba en WU antes
        "_wu_autoconnect_event_armed": True,
        "_wu_autoconnect_disable_armed": True,
    }
    fake_sidebar = _SidebarStub(session_state)
    rerun_called = {"count": 0}

    def _capture_rerun():
        rerun_called["count"] += 1
        raise RuntimeError("rerun_called")

    fake_st = SimpleNamespace(
        session_state=session_state,
        query_params={},
        sidebar=fake_sidebar,
        button=lambda *args, **kwargs: False,
        rerun=_capture_rerun,
    )
    monkeypatch.setattr(sidebar, "st", fake_st)
    monkeypatch.setattr(sidebar, "sync_local_storage", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "consume_local_storage_writes", lambda: {})
    monkeypatch.setattr(sidebar, "local_storage_snapshot_ready", lambda: True)
    monkeypatch.setattr(sidebar, "flush_local_storage_writes", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "st", fake_st)
    monkeypatch.setattr(storage, "_get_local_storage", lambda: object())
    monkeypatch.setattr(sidebar, "get_stored_unit_preferences", lambda: dict(DEFAULT_UNIT_PREFERENCES))
    monkeypatch.setattr(storage, "get_stored_wu_station_calibration", lambda station_id: {})
    monkeypatch.setattr(sidebar, "init_language", lambda: "es")
    monkeypatch.setattr(sidebar, "get_supported_languages", lambda: ["es"])
    monkeypatch.setattr(sidebar, "get_language_label", lambda lang: lang)
    monkeypatch.setattr(sidebar, "set_language", lambda lang: lang)
    monkeypatch.setattr(sidebar, "t", lambda key, **kwargs: key)

    try:
        sidebar.render_sidebar()
    except RuntimeError as exc:
        if "rerun_called" not in str(exc):
            raise

    # Tras el callback fantasma, el toggle WU debe haber quedado en False.
    assert session_state["auto_connect_wu_device"] is False
    # localStorage NO debe haber sido sobrescrito (sigue siendo el target PROVIDER).
    snap = session_state["_mlx_local_storage_snapshot"]
    assert '"kind":"PROVIDER"' in snap[LS_AUTOCONNECT_TARGET]
    assert snap[LS_AUTOCONNECT] == "1"
    # El target_kind del sidebar debe reflejar PROVIDER, no WU.
    assert session_state["_wu_autoconnect_ui_target_kind"] == "PROVIDER"
    assert session_state["_wu_autoconnect_ui_last_value"] is False


def test_sidebar_provider_takeover_ignores_stale_wu_true_callback(monkeypatch):
    provider_target_json = (
        '{"kind":"PROVIDER","provider_id":"AEMET","station_id":"9935X",'
        '"station_name":"VALDERROBRES","lat":40.8733,"lon":0.1464,"elevation_m":483}'
    )
    session_state = {
        "_sidebar_inputs_initialized": True,
        "_mlx_local_storage_snapshot_ready": True,
        "_mlx_local_storage_snapshot": {
            LS_AUTOCONNECT: "1",
            LS_AUTOCONNECT_TARGET: provider_target_json,
        },
        "active_station": "ILHOSP26",
        "active_key": "secret-key",
        "active_z": "39",
        sidebar.WU_STATION_INPUT_KEY: "ILHOSP26",
        sidebar.WU_API_KEY_INPUT_KEY: "secret-key",
        sidebar.WU_ALTITUDE_INPUT_KEY: "39",
        "connected": True,
        "connection_type": "WU",
        "wu_connected_station": "ILHOSP26",
        "wu_connected_api_key": "secret-key",
        "wu_connected_z": "39",
        # Callback stale: el usuario acaba de guardar PROVIDER, pero el
        # frontend aún entrega el toggle WU anterior en True.
        "auto_connect_wu_device": True,
        "_wu_autoconnect_toggle_changed": True,
        "_wu_autoconnect_ui_last_value": False,
        "_wu_autoconnect_ui_target_kind": "PROVIDER",
        "_wu_autoconnect_event_armed": True,
        "_wu_autoconnect_disable_armed": False,
        "_provider_autoconnect_takeover_pending": True,
        "_provider_autoconnect_takeover_grace": 1,
    }
    fake_sidebar = _SidebarStub(session_state)
    fake_st = SimpleNamespace(
        session_state=session_state,
        query_params={},
        sidebar=fake_sidebar,
        button=lambda *args, **kwargs: False,
        rerun=lambda: (_ for _ in ()).throw(RuntimeError("unexpected_rerun")),
    )
    monkeypatch.setattr(sidebar, "st", fake_st)
    monkeypatch.setattr(sidebar, "sync_local_storage", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "consume_local_storage_writes", lambda: {})
    monkeypatch.setattr(sidebar, "local_storage_snapshot_ready", lambda: True)
    monkeypatch.setattr(sidebar, "flush_local_storage_writes", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "st", fake_st)
    monkeypatch.setattr(storage, "_get_local_storage", lambda: object())
    monkeypatch.setattr(sidebar, "get_stored_unit_preferences", lambda: dict(DEFAULT_UNIT_PREFERENCES))
    monkeypatch.setattr(storage, "get_stored_wu_station_calibration", lambda station_id: {})
    monkeypatch.setattr(sidebar, "init_language", lambda: "es")
    monkeypatch.setattr(sidebar, "get_supported_languages", lambda: ["es"])
    monkeypatch.setattr(sidebar, "get_language_label", lambda lang: lang)
    monkeypatch.setattr(sidebar, "set_language", lambda lang: lang)
    monkeypatch.setattr(sidebar, "t", lambda key, **kwargs: key)

    sidebar.render_sidebar()

    snap = session_state["_mlx_local_storage_snapshot"]
    assert '"kind":"PROVIDER"' in snap[LS_AUTOCONNECT_TARGET]
    assert '"kind":"WU"' not in snap[LS_AUTOCONNECT_TARGET]
    assert session_state["auto_connect_wu_device"] is False
    assert "_provider_autoconnect_takeover_pending" not in session_state
    assert "sidebar.autoconnect.enabled" not in fake_sidebar.success_messages


def test_sidebar_provider_takeover_ignores_wu_callback_created_by_widget(monkeypatch):
    provider_target_json = (
        '{"kind":"PROVIDER","provider_id":"METEOCAT","station_id":"WW",'
        '"station_name":"Artés","lat":41.7942,"lon":1.9368,"elevation_m":278}'
    )
    session_state = {
        "_sidebar_inputs_initialized": True,
        "_mlx_local_storage_snapshot_ready": True,
        "_mlx_local_storage_snapshot": {
            LS_AUTOCONNECT: "1",
            LS_AUTOCONNECT_TARGET: provider_target_json,
        },
        "active_station": "ILHOSP26",
        "active_key": "secret-key",
        "active_z": "39",
        sidebar.WU_STATION_INPUT_KEY: "ILHOSP26",
        sidebar.WU_API_KEY_INPUT_KEY: "secret-key",
        sidebar.WU_ALTITUDE_INPUT_KEY: "39",
        "connected": True,
        "connection_type": "WU",
        "auto_connect_wu_device": False,
        "_wu_autoconnect_ui_last_value": False,
        "_wu_autoconnect_ui_target_kind": "PROVIDER",
        "_wu_autoconnect_event_armed": True,
        "_wu_autoconnect_disable_armed": False,
        "_provider_autoconnect_takeover_pending": True,
        "_provider_autoconnect_takeover_grace": 1,
    }
    fake_sidebar = _SidebarStaleWuToggleStub(session_state)
    fake_st = SimpleNamespace(
        session_state=session_state,
        query_params={},
        sidebar=fake_sidebar,
        button=lambda *args, **kwargs: False,
        rerun=lambda: (_ for _ in ()).throw(RuntimeError("unexpected_rerun")),
    )
    monkeypatch.setattr(sidebar, "st", fake_st)
    monkeypatch.setattr(sidebar, "sync_local_storage", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "consume_local_storage_writes", lambda: {})
    monkeypatch.setattr(sidebar, "local_storage_snapshot_ready", lambda: True)
    monkeypatch.setattr(sidebar, "flush_local_storage_writes", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "st", fake_st)
    monkeypatch.setattr(storage, "_get_local_storage", lambda: object())
    monkeypatch.setattr(sidebar, "get_stored_unit_preferences", lambda: dict(DEFAULT_UNIT_PREFERENCES))
    monkeypatch.setattr(storage, "get_stored_wu_station_calibration", lambda station_id: {})
    monkeypatch.setattr(sidebar, "init_language", lambda: "es")
    monkeypatch.setattr(sidebar, "get_supported_languages", lambda: ["es"])
    monkeypatch.setattr(sidebar, "get_language_label", lambda lang: lang)
    monkeypatch.setattr(sidebar, "set_language", lambda lang: lang)
    monkeypatch.setattr(sidebar, "t", lambda key, **kwargs: key)

    sidebar.render_sidebar()

    snap = session_state["_mlx_local_storage_snapshot"]
    assert '"kind":"PROVIDER"' in snap[LS_AUTOCONNECT_TARGET]
    assert '"kind":"WU"' not in snap[LS_AUTOCONNECT_TARGET]
    assert session_state["auto_connect_wu_device"] is False
    assert "sidebar.autoconnect.enabled" not in fake_sidebar.success_messages


def test_sidebar_provider_takeover_grace_ignores_late_wu_true_callback(monkeypatch):
    provider_target_json = (
        '{"kind":"PROVIDER","provider_id":"AEMET","station_id":"8210Y",'
        '"station_name":"SALVACAÑETE","lat":40.1031,"lon":-1.5036,"elevation_m":1160}'
    )
    session_state = {
        "_sidebar_inputs_initialized": True,
        "_mlx_local_storage_snapshot_ready": True,
        "_mlx_local_storage_snapshot": {
            LS_AUTOCONNECT: "1",
            LS_AUTOCONNECT_TARGET: provider_target_json,
        },
        "active_station": "ILHOSP26",
        "active_key": "secret-key",
        "active_z": "39",
        sidebar.WU_STATION_INPUT_KEY: "ILHOSP26",
        sidebar.WU_API_KEY_INPUT_KEY: "secret-key",
        sidebar.WU_ALTITUDE_INPUT_KEY: "39",
        "connected": True,
        "connection_type": "WU",
        "auto_connect_wu_device": True,
        "_wu_autoconnect_toggle_changed": True,
        "_wu_autoconnect_ui_last_value": False,
        "_wu_autoconnect_ui_target_kind": "PROVIDER",
        "_wu_autoconnect_event_armed": True,
        "_wu_autoconnect_disable_armed": False,
        # El callback stale llega un rerun después: pending ya se consumió,
        # pero la ventana de gracia sigue activa.
        "_provider_autoconnect_takeover_grace": 1,
    }
    fake_sidebar = _SidebarStub(session_state)
    fake_st = SimpleNamespace(
        session_state=session_state,
        query_params={},
        sidebar=fake_sidebar,
        button=lambda *args, **kwargs: False,
        rerun=lambda: (_ for _ in ()).throw(RuntimeError("unexpected_rerun")),
    )
    monkeypatch.setattr(sidebar, "st", fake_st)
    monkeypatch.setattr(sidebar, "sync_local_storage", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "consume_local_storage_writes", lambda: {})
    monkeypatch.setattr(sidebar, "local_storage_snapshot_ready", lambda: True)
    monkeypatch.setattr(sidebar, "flush_local_storage_writes", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "st", fake_st)
    monkeypatch.setattr(storage, "_get_local_storage", lambda: object())
    monkeypatch.setattr(sidebar, "get_stored_unit_preferences", lambda: dict(DEFAULT_UNIT_PREFERENCES))
    monkeypatch.setattr(storage, "get_stored_wu_station_calibration", lambda station_id: {})
    monkeypatch.setattr(sidebar, "init_language", lambda: "es")
    monkeypatch.setattr(sidebar, "get_supported_languages", lambda: ["es"])
    monkeypatch.setattr(sidebar, "get_language_label", lambda lang: lang)
    monkeypatch.setattr(sidebar, "set_language", lambda lang: lang)
    monkeypatch.setattr(sidebar, "t", lambda key, **kwargs: key)

    sidebar.render_sidebar()

    snap = session_state["_mlx_local_storage_snapshot"]
    assert '"kind":"PROVIDER"' in snap[LS_AUTOCONNECT_TARGET]
    assert '"kind":"WU"' not in snap[LS_AUTOCONNECT_TARGET]
    assert session_state["auto_connect_wu_device"] is False
    assert session_state["_provider_autoconnect_takeover_grace"] == 0
    assert "sidebar.autoconnect.enabled" not in fake_sidebar.success_messages


def test_sidebar_allows_real_wu_autoconnect_click_after_provider_target(monkeypatch):
    provider_target_json = (
        '{"kind":"PROVIDER","provider_id":"METEOCAT","station_id":"Z6",'
        '"station_name":"Sasseuva","lat":42.7,"lon":1.4,"elevation_m":2228}'
    )
    session_state = {
        "_sidebar_inputs_initialized": True,
        "_mlx_local_storage_snapshot_ready": True,
        "_mlx_local_storage_snapshot": {
            LS_AUTOCONNECT: "1",
            LS_AUTOCONNECT_TARGET: provider_target_json,
        },
        "active_station": "ILHOSP26",
        "active_key": "secret-key",
        "active_z": "39",
        sidebar.WU_STATION_INPUT_KEY: "ILHOSP26",
        sidebar.WU_API_KEY_INPUT_KEY: "secret-key",
        sidebar.WU_ALTITUDE_INPUT_KEY: "39",
        "connected": True,
        "connection_type": "METEOCAT",
        # En este caso la UI ya estaba sincronizada con proveedor apagado:
        # el siguiente True sí representa un click real del usuario para
        # volver a WU, no una rehidratación stale.
        "auto_connect_wu_device": True,
        "_wu_autoconnect_toggle_changed": True,
        "_wu_autoconnect_ui_last_value": False,
        "_wu_autoconnect_ui_target_kind": "PROVIDER",
        "_wu_autoconnect_event_armed": True,
        "_wu_autoconnect_disable_armed": False,
    }
    fake_sidebar = _SidebarStub(session_state)
    fake_st = SimpleNamespace(
        session_state=session_state,
        query_params={},
        sidebar=fake_sidebar,
        button=lambda *args, **kwargs: False,
        rerun=lambda: (_ for _ in ()).throw(RuntimeError("unexpected_rerun")),
    )
    monkeypatch.setattr(sidebar, "st", fake_st)
    monkeypatch.setattr(sidebar, "sync_local_storage", lambda *args, **kwargs: None)
    monkeypatch.setattr(sidebar, "consume_local_storage_writes", lambda: {})
    monkeypatch.setattr(sidebar, "local_storage_snapshot_ready", lambda: True)
    monkeypatch.setattr(sidebar, "flush_local_storage_writes", lambda *args, **kwargs: None)
    monkeypatch.setattr(storage, "st", fake_st)
    monkeypatch.setattr(storage, "_get_local_storage", lambda: object())
    monkeypatch.setattr(sidebar, "get_stored_unit_preferences", lambda: dict(DEFAULT_UNIT_PREFERENCES))
    monkeypatch.setattr(storage, "get_stored_wu_station_calibration", lambda station_id: {})
    monkeypatch.setattr(sidebar, "init_language", lambda: "es")
    monkeypatch.setattr(sidebar, "get_supported_languages", lambda: ["es"])
    monkeypatch.setattr(sidebar, "get_language_label", lambda lang: lang)
    monkeypatch.setattr(sidebar, "set_language", lambda lang: lang)
    monkeypatch.setattr(sidebar, "t", lambda key, **kwargs: key)

    sidebar.render_sidebar()

    snap = session_state["_mlx_local_storage_snapshot"]
    assert snap[LS_AUTOCONNECT] == "1"
    assert '"kind":"WU"' in snap[LS_AUTOCONNECT_TARGET]
    assert '"station":"ILHOSP26"' in snap[LS_AUTOCONNECT_TARGET]
    assert session_state["_wu_autoconnect_ui_target_kind"] == "WU"
    assert "sidebar.autoconnect.enabled" in fake_sidebar.success_messages
