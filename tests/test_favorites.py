import json
from types import SimpleNamespace

from config import LS_FAVORITES
from utils import favorites
from utils import storage
from components import favorites as favorites_component


class _RenderCtx:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FavoritesStStub:
    def __init__(self):
        self.markdown_calls = []
        self.container_calls = []
        self.session_state = {}

    def markdown(self, *args, **kwargs):
        self.markdown_calls.append((args, kwargs))

    def columns(self, spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        return [_RenderCtx() for _ in range(count)]

    def container(self, **kwargs):
        self.container_calls.append(kwargs)
        return _RenderCtx()

    def expander(self, *args, **kwargs):
        self.expander_args = (args, kwargs)
        return _RenderCtx()

    def button(self, *args, **kwargs):
        return False

    def error(self, *args, **kwargs):
        pass


def test_wu_favorite_requires_api_key_and_normalizes_station_id():
    assert favorites.favorite_from_wu("ilhosP26", "", "39") is None

    fav = favorites.favorite_from_wu("ilhosP26", "secret", "39")

    assert fav["provider_id"] == "WU"
    assert fav["station_id"] == "ILHOSP26"
    assert fav["api_key"] == "secret"
    assert fav["z"] == "39"


def test_provider_favorite_ignores_weatherlink_and_keeps_metadata():
    assert favorites.favorite_from_provider_station({"provider_id": "WEATHERLINK", "station_id": "123"}) is None

    fav = favorites.favorite_from_provider_station(
        {
            "provider_id": "METEOHUB_IT",
            "provider": "MeteoHub IT",
            "station_id": "dpcn-lombardia|46.29690|10.50656|ponte-di-legno-case-pirli",
            "name": "Ponte di Legno Case Pirli",
            "lat": 46.2969,
            "lon": 10.50656,
            "elevation_m": 1640,
            "locality": "dpcn-lombardia",
            "station_tz": "Europe/Rome",
        }
    )

    assert fav["kind"] == "PROVIDER"
    assert fav["provider_id"] == "METEOHUB_IT"
    assert fav["station_name"] == "Ponte di Legno Case Pirli"
    assert fav["elevation_m"] == 1640.0
    assert fav["station_tz"] == "Europe/Rome"


def test_upsert_favorite_replaces_existing_station(monkeypatch):
    stored = [
        favorites.favorite_from_wu("ILHOSP26", "old", "10"),
        favorites.favorite_from_wu("IOTHER", "key", "20"),
    ]
    written = {}

    monkeypatch.setattr(favorites, "get_stored_favorites", lambda: list(stored))
    monkeypatch.setattr(favorites, "set_stored_favorites", lambda payload: written.setdefault("payload", payload))

    assert favorites.upsert_favorite(favorites.favorite_from_wu("ILHOSP26", "new", "39")) is True

    payload = written["payload"]
    assert len(payload) == 2
    assert payload[0]["station_id"] == "ILHOSP26"
    assert payload[0]["api_key"] == "new"
    assert payload[1]["station_id"] == "IOTHER"


def test_remove_favorites_by_provider_keeps_other_providers(monkeypatch):
    stored = [
        favorites.favorite_from_wu("ILHOSP26", "secret", "39"),
        favorites.favorite_from_provider_station(
            {
                "provider_id": "METEOCAT",
                "provider_name": "Meteocat",
                "station_id": "Z6",
                "name": "Sasseuva",
                "lat": 42.7,
                "lon": 0.7,
                "elevation_m": 2228,
            }
        ),
    ]
    written = {}

    monkeypatch.setattr(favorites, "get_stored_favorites", lambda: list(stored))
    monkeypatch.setattr(favorites, "set_stored_favorites", lambda payload: written.setdefault("payload", payload))

    assert favorites.remove_favorites_by_provider("WU") is True

    payload = written["payload"]
    assert len(payload) == 1
    assert payload[0]["provider_id"] == "METEOCAT"


def test_remove_favorite_only_removes_matching_station(monkeypatch):
    stored = [
        favorites.favorite_from_wu("ILHOSP26", "secret", "39"),
        favorites.favorite_from_wu("IOTHER", "key", "20"),
        favorites.favorite_from_provider_station(
            {
                "provider_id": "METEOCAT",
                "provider_name": "Meteocat",
                "station_id": "Z6",
                "name": "Sasseuva",
                "lat": 42.7,
                "lon": 0.7,
                "elevation_m": 2228,
            }
        ),
    ]
    written = {}

    monkeypatch.setattr(favorites, "get_stored_favorites", lambda: list(stored))
    monkeypatch.setattr(favorites, "set_stored_favorites", lambda payload: written.setdefault("payload", payload))

    assert favorites.remove_favorite(favorites.favorite_from_wu("ILHOSP26", "secret", "39")) is True

    payload = written["payload"]
    assert [item["station_id"] for item in payload] == ["IOTHER", "Z6"]


def test_favorite_meta_text_shows_only_provider():
    fav = favorites.favorite_from_wu(
        "ILHOSP26",
        "secret",
        "39",
        lat=41.371,
        lon=2.128,
        elevation_m=39,
    )

    assert favorites_component._favorite_meta_text(lambda key, **kwargs: key, fav) == "Weather Underground"


def test_render_favorites_bar_escapes_css_braces(monkeypatch):
    fake_st = _FavoritesStStub()
    monkeypatch.setattr(favorites_component, "st", fake_st)
    monkeypatch.setattr(
        favorites_component,
        "get_stored_favorites",
        lambda: [
            favorites.favorite_from_wu("ILHOSP26", "secret", "39"),
            favorites.favorite_from_wu("IROSES18", "secret", "25"),
        ],
    )

    favorites_component.render_favorites_bar(t=lambda key, **kwargs: key, dark=False)

    assert fake_st.markdown_calls
    assert fake_st.expander_args[1]["expanded"] is False
    assert any(
        'div[data-testid="stExpander"]:has(.mlbx-favorites-title)' in str(args[0])
        for args, _kwargs in fake_st.markdown_calls
        if args
    )


def test_render_favorites_bar_marks_current_station_by_container_key(monkeypatch):
    fake_st = _FavoritesStStub()
    fake_st.session_state = {
        "connected": True,
        "connection_type": "WU",
        "wu_connected_station": "ILHOSP26",
    }
    monkeypatch.setattr(favorites_component, "st", fake_st)
    monkeypatch.setattr(
        favorites_component,
        "get_stored_favorites",
        lambda: [
            favorites.favorite_from_wu("ILHOSP26", "secret", "39"),
            favorites.favorite_from_wu("IROSES18", "secret", "25"),
        ],
    )

    favorites_component.render_favorites_bar(t=lambda key, **kwargs: key, dark=False)

    container_keys = [call.get("key", "") for call in fake_st.container_calls]
    assert any(key.startswith("mlbx_favorite_card_active_") for key in container_keys)
    assert not any(
        "mlbx-favorite-active-anchor" in str(args[0])
        for args, _kwargs in fake_st.markdown_calls
        if args
    )


def test_current_favorite_key_matches_connected_wu_station():
    state = {
        "connected": True,
        "connection_type": "WU",
        "wu_connected_station": "ilhosP26",
    }

    assert favorites_component._current_favorite_key(state) == "WU:ILHOSP26"


def test_current_favorite_key_matches_connected_provider_station():
    state = {
        "connected": True,
        "connection_type": "METEOCAT",
        "meteocat_station_id": "x8",
    }

    assert favorites_component._current_favorite_key(state) == "METEOCAT:X8"


# =====================================================================
# WeatherLink favorites
# =====================================================================

def test_favorite_from_weatherlink_persists_credentials_and_station() -> None:
    favorite = favorites.favorite_from_weatherlink(
        "12345",
        "MY_API_KEY",
        "MY_API_SECRET",
        "120",
        station_name="My Station",
        station_id_uuid="abc-def",
        lat=41.4,
        lon=2.2,
        elevation_m=120.0,
    )

    assert favorite is not None
    assert favorite["provider_id"] == "WEATHERLINK"
    assert favorite["kind"] == "WEATHERLINK"
    assert favorite["station_id"] == "12345"
    assert favorite["station_name"] == "My Station"
    assert favorite["api_key"] == "MY_API_KEY"
    assert favorite["api_secret"] == "MY_API_SECRET"
    assert favorite["z"] == "120"
    assert favorite["station_id_uuid"] == "abc-def"
    assert favorite["lat"] == 41.4
    assert favorite["lon"] == 2.2
    assert favorite["elevation_m"] == 120.0


def test_favorite_from_weatherlink_rejected_without_credentials() -> None:
    """Sin api_key o sin api_secret no se puede reconectar → rechazar."""
    assert favorites.favorite_from_weatherlink("12345", "", "secret") is None
    assert favorites.favorite_from_weatherlink("12345", "key", "") is None
    assert favorites.favorite_from_weatherlink("", "key", "secret") is None


def test_weatherlink_favorite_key_uses_provider_and_station() -> None:
    favorite = favorites.favorite_from_weatherlink(
        "12345", "k", "s",
    )
    assert favorite is not None
    assert favorites.favorite_key(favorite) == "WEATHERLINK:12345"


def test_normalize_favorite_accepts_weatherlink_with_secret() -> None:
    """``normalize_favorite`` ya NO descarta WEATHERLINK (cambio F-WL)."""
    raw = {
        "provider_id": "WEATHERLINK",
        "station_id": "12345",
        "api_key": "k", "api_secret": "s", "z": "100",
    }
    favorite = favorites.normalize_favorite(raw)
    assert favorite is not None
    assert favorite["provider_id"] == "WEATHERLINK"


def test_normalize_favorite_rejects_weatherlink_without_secret() -> None:
    """Pero exige ambas credenciales (sin secret no se puede reconectar)."""
    raw = {"provider_id": "WEATHERLINK", "station_id": "12345", "api_key": "k"}
    assert favorites.normalize_favorite(raw) is None


def test_current_favorite_key_matches_connected_weatherlink_station() -> None:
    """
    Tras conectar a una estación WeatherLink, la card del favorito
    correspondiente debe marcarse como activa.
    """
    state = {
        "connected": True,
        "connection_type": "WEATHERLINK",
        "weatherlink_station_id": "12345",
    }

    assert favorites_component._current_favorite_key(state) == "WEATHERLINK:12345"


def test_normalize_favorite_rejects_dict_as_station_id() -> None:
    """
    Regresión: una versión previa del path WeatherLink pasaba un dict
    como ``station_id`` al guardar, lo que resultaba en un favorito
    con texto basura (el dict serializado) como nombre. Defendemos
    contra esa clase de regresión rechazando favoritos cuyo
    ``station_id`` no sea un primitivo o cuyo string empiece por '{'/'['.
    """
    # Caso 1: station_id es directamente un dict
    raw_dict_as_id = {
        "provider_id": "WEATHERLINK",
        "station_id": {"station_id": "123631", "elevation": "25"},
        "api_key": "k",
        "api_secret": "s",
    }
    assert favorites.normalize_favorite(raw_dict_as_id) is None

    # Caso 2: station_id ya pre-serializado a string '{...}'
    raw_serialized_dict = {
        "provider_id": "WEATHERLINK",
        "station_id": "{'station_id': '123631'}",
        "api_key": "k",
        "api_secret": "s",
    }
    assert favorites.normalize_favorite(raw_serialized_dict) is None

    # Caso 3: station_id es una lista
    raw_list_as_id = {
        "provider_id": "WEATHERLINK",
        "station_id": ["12345"],
        "api_key": "k",
        "api_secret": "s",
    }
    assert favorites.normalize_favorite(raw_list_as_id) is None


def test_normalize_favorite_accepts_numeric_station_id() -> None:
    """Estaciones WeatherLink usan IDs numéricos; aceptamos int/float."""
    raw_numeric = {
        "provider_id": "WEATHERLINK",
        "station_id": 12345,  # int, no string
        "api_key": "k",
        "api_secret": "s",
    }
    favorite = favorites.normalize_favorite(raw_numeric)
    assert favorite is not None
    assert favorite["station_id"] == "12345"


# =====================================================================
# Persistencia: el favorito debe quedar ENCOLADO para localStorage
# =====================================================================

def _patch_storage(monkeypatch, patch_streamlit):
    """Aísla utils.storage del componente real de localStorage."""
    patch_streamlit(storage)
    monkeypatch.setattr(
        storage,
        "_get_local_storage",
        lambda: SimpleNamespace(getItem=lambda *args, **kwargs: None),
    )


def test_upsert_favorite_queues_write_for_browser_persistence(
    patch_streamlit, fake_session_state, monkeypatch
):
    """
    Regresión del bug de producción: al guardar un favorito desde el mapa o
    el selector de estaciones, el favorito aparecía en la sesión pero
    desaparecía al recargar.

    Causa: esos flujos hacían un ``flush_local_storage_writes`` efímero
    seguido de ``st.rerun()`` inmediato, que desmontaba el iframe del bridge
    antes de escribir en el navegador y vaciaba la cola. La corrección deja
    la escritura ENCOLADA para que el bootstrap estable del sidebar la
    entregue en el rerun siguiente (igual que las credenciales WU).

    Este test fija ese contrato: tras ``upsert_favorite`` la escritura de
    ``LS_FAVORITES`` queda en la cola pendiente (no se pierde) y es legible
    dentro de la sesión.
    """
    _patch_storage(monkeypatch, patch_streamlit)

    favorite = favorites.favorite_from_provider_station(
        {
            "provider_id": "METEOGALICIA",
            "provider": "MeteoGalicia",
            "station_id": "10045",
            "name": "Mabegondo",
            "lat": 43.24,
            "lon": -8.26,
            "elevation_m": 94,
        }
    )
    assert favorite is not None
    assert favorites.upsert_favorite(favorite) is True

    # 1) La escritura de LS_FAVORITES quedó ENCOLADA para el bridge.
    pending = fake_session_state.get("_mlx_local_storage_pending_writes", {})
    assert LS_FAVORITES in pending, "el favorito debe encolarse para localStorage"
    queued = json.loads(pending[LS_FAVORITES])
    assert len(queued) == 1
    assert queued[0]["station_id"] == "10045"
    assert queued[0]["provider_id"] == "METEOGALICIA"

    # 2) Dentro de la sesión el favorito es legible (write-cache/snapshot),
    #    sin haber hablado todavía con el navegador.
    stored = favorites.get_stored_favorites()
    assert [f["station_id"] for f in stored] == ["10045"]

    # 3) El bootstrap consume la cola UNA vez para entregarla al navegador.
    consumed = storage.consume_local_storage_writes()
    assert LS_FAVORITES in consumed
    assert storage.consume_local_storage_writes() == {}
