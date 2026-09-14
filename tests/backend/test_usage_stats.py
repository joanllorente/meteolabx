"""Estadísticas internas de uso: servicio y endpoints."""
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from server.main import create_app
from server.services import usage_stats


def _settings(tmp_path, password="admin"):
    return SimpleNamespace(
        usage_stats_path=str(tmp_path / "usage_stats.sqlite"),
        stats_admin_password=password,
    )


def test_record_and_summary_windows(tmp_path):
    settings = _settings(tmp_path)
    usage_stats.record_visit("AEMET", "0076", "BARCELONA AEROPUERTO", settings=settings)
    usage_stats.record_visit(
        "AEMET", "0076", "BARCELONA AEROPUERTO", source="seo", settings=settings
    )
    usage_stats.record_seo_page_view(
        "AEMET", "0076", "BARCELONA AEROPUERTO", language="es", settings=settings
    )
    usage_stats.record_visit("WU", "IMADRID1", "", settings=settings)
    usage_stats.record_seo_panel_click(
        "AEMET", "0076", "BARCELONA AEROPUERTO", language="es", settings=settings
    )

    # Visita antigua (40 días): cuenta en total pero no en las ventanas.
    import sqlite3

    with sqlite3.connect(settings.usage_stats_path) as connection:
        connection.execute(
            "INSERT INTO station_visits(provider, station_id, name, epoch) VALUES (?, ?, ?, ?)",
            ("AEMET", "0076", "", int(time.time()) - 40 * 24 * 3600),
        )

    summary = usage_stats.visit_summary(settings=settings)
    assert summary["totals"]["total"] == 4
    assert summary["totals"]["d30"] == 3
    assert summary["totals"]["stations"] == 2
    assert summary["totals"]["sources"] == {
        "app": {"d30": 2, "total": 3},
        "seo": {"d30": 1, "total": 1},
        "legacy": {"d30": 0, "total": 0},
    }
    assert summary["totals"]["panel_clicks"]["d30"] == 1
    assert summary["totals"]["panel_clicks"]["total"] == 1

    top = summary["stations"][0]
    assert (top["provider"], top["station_id"]) == ("AEMET", "0076")
    assert top["total"] == 3
    assert top["d1"] == 2
    assert top["app_total"] == 2
    assert top["seo_total"] == 1
    assert top["panel_clicks"]["total"] == 1
    assert top["name"] == "BARCELONA AEROPUERTO"


def test_visit_normalizes_and_ignores_empty(tmp_path):
    settings = _settings(tmp_path)
    usage_stats.record_visit("wu", "IMADRID1", settings=settings)
    usage_stats.record_visit("", "X", settings=settings)   # ignorada
    usage_stats.record_visit("WU", "", settings=settings)  # ignorada
    summary = usage_stats.visit_summary(settings=settings)
    assert summary["totals"]["total"] == 1
    assert summary["stations"][0]["provider"] == "WU"


def test_seo_page_view_counts_without_station_connection(tmp_path):
    settings = _settings(tmp_path)
    usage_stats.record_seo_page_view(
        "meteocat", "D5", "Observatori Fabra", language="ca", settings=settings
    )

    summary = usage_stats.visit_summary(settings=settings)

    assert summary["totals"]["total"] == 0
    assert summary["totals"]["sources"]["seo"] == {"d30": 1, "total": 1}
    assert summary["stations"][0]["station_id"] == "D5"
    assert summary["stations"][0]["seo_total"] == 1


def test_record_section_visits_and_summary_windows(tmp_path):
    settings = _settings(tmp_path)
    usage_stats.record_section_visit("map.stations", settings=settings)
    usage_stats.record_section_visit("MAP.TEMPERATURE", settings=settings)
    usage_stats.record_section_visit("forecast.streamlit", settings=settings)
    usage_stats.record_section_visit("forecast.direct", settings=settings)
    usage_stats.record_section_visit("ranking", settings=settings)
    usage_stats.record_section_visit("unknown", settings=settings)  # ignorada

    import sqlite3

    with sqlite3.connect(settings.usage_stats_path) as connection:
        connection.execute(
            "INSERT INTO section_visits(section, epoch) VALUES (?, ?)",
            ("map.stations", int(time.time()) - 40 * 24 * 3600),
        )

    summary = usage_stats.visit_summary(settings=settings)
    by_section = {row["section"]: row for row in summary["sections"]}
    assert by_section["map.stations"]["total"] == 2
    assert by_section["map.stations"]["d30"] == 1
    assert by_section["map.temperature"]["total"] == 1
    assert by_section["ranking"]["total"] == 1
    assert by_section["forecast.streamlit"]["total"] == 1
    assert by_section["forecast.direct"]["total"] == 1
    assert by_section["observation"]["total"] == 0
    assert len(summary["sections"]) == len(usage_stats.TRACKED_SECTIONS)


def test_record_error_and_summary(tmp_path):
    settings = _settings(tmp_path)
    usage_stats.record_visit("AEMET", "0076", "BARCELONA AEROPUERTO", settings=settings)
    usage_stats.record_error("AEMET", "0076", error_kind="timeout", settings=settings)
    usage_stats.record_error("AEMET", "0076", error_kind="Timeout ", settings=settings)  # se normaliza
    usage_stats.record_error(
        "wu", "IMADRID1", "Madrid Centro", error_kind="unauthorized",
        status_code=401, settings=settings,
    )
    # Ignorados: sin provider/station/kind.
    usage_stats.record_error("", "X", error_kind="timeout", settings=settings)
    usage_stats.record_error("WU", "X", error_kind="", settings=settings)

    summary = usage_stats.visit_summary(settings=settings)
    assert summary["totals"]["errors"]["total"] == 3
    assert summary["totals"]["errors"]["d1"] == 3

    by_id = {s["station_id"]: s for s in summary["stations"]}
    assert by_id["0076"]["errors"]["total"] == 2
    assert by_id["0076"]["errors"]["last_kind"] == "timeout"
    # Estación con errores pero sin visitas: aparece igualmente en el panel.
    assert by_id["IMADRID1"]["total"] == 0
    assert by_id["IMADRID1"]["errors"]["total"] == 1
    assert by_id["IMADRID1"]["name"] == "Madrid Centro"

    kinds = {k["kind"]: k for k in summary["error_kinds"]}
    assert kinds["timeout"]["total"] == 2
    assert kinds["unauthorized"]["total"] == 1


def test_error_table_added_without_wiping_existing_db(tmp_path):
    """Simula un despliegue: base creada con el esquema antiguo (solo
    station_visits) que debe conservar sus datos al añadirse station_errors."""
    import sqlite3

    settings = _settings(tmp_path)
    old_schema = """
    CREATE TABLE IF NOT EXISTS station_visits (
        visit_pk INTEGER PRIMARY KEY,
        provider TEXT NOT NULL,
        station_id TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        epoch INTEGER NOT NULL
    );
    """
    with sqlite3.connect(settings.usage_stats_path) as connection:
        connection.executescript(old_schema)
        connection.execute(
            "INSERT INTO station_visits(provider, station_id, name, epoch) VALUES (?, ?, ?, ?)",
            ("AEMET", "0076", "BARCELONA AEROPUERTO", int(time.time())),
        )

    # Primer uso tras el despliegue: crea station_errors sin tocar lo previo.
    usage_stats.record_error("AEMET", "0076", error_kind="network", settings=settings)
    summary = usage_stats.visit_summary(settings=settings)
    assert summary["totals"]["total"] == 1  # la visita antigua sigue ahí
    assert summary["totals"]["sources"]["app"]["total"] == 0
    assert summary["totals"]["sources"]["seo"]["total"] == 0
    assert summary["totals"]["sources"]["legacy"]["total"] == 1
    assert summary["stations"][0]["legacy_total"] == 1
    assert summary["totals"]["errors"]["total"] == 1
    assert summary["stations"][0]["station_id"] == "0076"
    assert summary["stations"][0]["errors"]["last_kind"] == "network"

    # La misma migración crea la tabla de navegación sin borrar las visitas.
    usage_stats.record_section_visit("map.wind", settings=settings)
    summary = usage_stats.visit_summary(settings=settings)
    assert next(row for row in summary["sections"] if row["section"] == "map.wind")["total"] == 1

    with sqlite3.connect(settings.usage_stats_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(station_visits)")}
    assert "source" in columns


@pytest.fixture()
def stats_client(tmp_path, monkeypatch):
    from server import config as server_config

    settings = server_config.get_settings()
    monkeypatch.setattr(settings, "usage_stats_path", str(tmp_path / "stats.sqlite"), raising=False)
    monkeypatch.setattr(settings, "stats_admin_password", "s3creto", raising=False)
    app = create_app()
    with TestClient(app) as client:
        yield client


def test_stats_endpoints_roundtrip_and_auth(stats_client):
    ok = stats_client.post(
        "/v1/stats/visit",
        json={
            "provider": "AEMET",
            "station_id": "0076",
            "name": "Barcelona Aeropuerto",
            "source": "seo",
        },
    )
    assert ok.status_code == 204
    seo_view = stats_client.post(
        "/v1/stats/seo-view",
        json={
            "provider": "AEMET",
            "station_id": "0076",
            "name": "Barcelona Aeropuerto",
            "language": "es",
        },
    )
    assert seo_view.status_code == 204
    click = stats_client.post(
        "/v1/stats/panel-click",
        json={
            "provider": "AEMET",
            "station_id": "0076",
            "name": "Barcelona Aeropuerto",
            "language": "es",
        },
    )
    assert click.status_code == 204
    assert stats_client.post(
        "/v1/stats/visit",
        json={"provider": "AEMET", "station_id": "0076", "source": "otra"},
    ).status_code == 422

    # Sin contraseña o con contraseña mala → 401.
    assert stats_client.get("/v1/stats/stations").status_code == 401
    assert stats_client.get(
        "/v1/stats/stations", headers={"X-Stats-Password": "mala"}
    ).status_code == 401

    err = stats_client.post(
        "/v1/stats/error",
        json={
            "provider": "AEMET",
            "station_id": "0076",
            "error_kind": "timeout",
            "status_code": 504,
        },
    )
    assert err.status_code == 204
    section = stats_client.post(
        "/v1/stats/section", json={"section": "map.precipitation"},
    )
    assert section.status_code == 204
    assert stats_client.post(
        "/v1/stats/section", json={"section": "forecast.streamlit"},
    ).status_code == 204
    assert stats_client.post(
        "/v1/stats/section", json={"section": "forecast.direct"},
    ).status_code == 204
    assert stats_client.post(
        "/v1/stats/section", json={"section": "unknown"},
    ).status_code == 422
    # status_code fuera de rango o error_kind vacío → 422 de validación.
    assert stats_client.post(
        "/v1/stats/error",
        json={"provider": "AEMET", "station_id": "0076", "error_kind": ""},
    ).status_code == 422

    good = stats_client.get(
        "/v1/stats/stations", headers={"X-Stats-Password": "s3creto"}
    )
    assert good.status_code == 200
    payload = good.json()
    assert payload["totals"]["total"] == 1
    assert payload["totals"]["sources"]["seo"]["total"] == 1
    assert payload["totals"]["sources"]["app"]["total"] == 0
    assert payload["totals"]["sources"]["legacy"]["total"] == 0
    assert payload["totals"]["panel_clicks"]["total"] == 1
    assert payload["totals"]["errors"]["total"] == 1
    assert payload["stations"][0]["station_id"] == "0076"
    assert payload["stations"][0]["seo_total"] == 1
    assert payload["stations"][0]["panel_clicks"]["total"] == 1
    assert payload["stations"][0]["errors"]["last_kind"] == "timeout"
    assert payload["error_kinds"][0]["kind"] == "timeout"
    sections = {row["section"]: row for row in payload["sections"]}
    assert sections["map.precipitation"]["total"] == 1
    assert sections["forecast.streamlit"]["total"] == 1
    assert sections["forecast.direct"]["total"] == 1


def test_stats_disabled_without_password(tmp_path, monkeypatch):
    from server import config as server_config

    settings = server_config.get_settings()
    monkeypatch.setattr(settings, "usage_stats_path", str(tmp_path / "stats.sqlite"), raising=False)
    monkeypatch.setattr(settings, "stats_admin_password", "", raising=False)
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/v1/stats/stations", headers={"X-Stats-Password": ""})
    assert response.status_code == 404


def test_station_detail_endpoint(stats_client):
    """El detalle dice cuándo y de qué tipo fueron los errores."""
    assert stats_client.post(
        "/v1/stats/visit",
        json={
            "provider": "METEOCAT",
            "station_id": "X4",
            "name": "Tarragona",
            "source": "app",
            "language": "ca",
            "entry": "search",
            "referrer_domain": "google.es",
            "device": "mobile",
        },
    ).status_code == 204
    # Una entrada que no reconocemos no se guarda, y el dominio solo tiene
    # sentido cuando alguien enlazó de verdad.
    assert stats_client.post(
        "/v1/stats/visit",
        json={"provider": "METEOCAT", "station_id": "X4", "entry": "inventada"},
    ).status_code == 422
    assert stats_client.post(
        "/v1/stats/visit",
        json={"provider": "METEOCAT", "station_id": "X4", "device": "nevera"},
    ).status_code == 422
    assert stats_client.post(
        "/v1/stats/visit",
        json={
            "provider": "METEOCAT",
            "station_id": "X4",
            "language": "es",
            "entry": "direct",
            "referrer_domain": "google.es",
        },
    ).status_code == 204
    assert stats_client.post(
        "/v1/stats/error",
        json={
            "provider": "METEOCAT",
            "station_id": "X4",
            "name": "Tarragona",
            "error_kind": "timeout",
            "status_code": 504,
        },
    ).status_code == 204
    assert stats_client.post(
        "/v1/stats/error",
        json={"provider": "METEOCAT", "station_id": "X4", "error_kind": "network"},
    ).status_code == 204

    consulta = {"provider": "METEOCAT", "station_id": "X4"}
    # La contraseña también se exige aquí.
    assert stats_client.get("/v1/stats/station", params=consulta).status_code == 401
    assert stats_client.get(
        "/v1/stats/station", params=consulta, headers={"X-Stats-Password": "mala"}
    ).status_code == 401

    respuesta = stats_client.get(
        "/v1/stats/station", params=consulta, headers={"X-Stats-Password": "s3creto"}
    )
    assert respuesta.status_code == 200
    detalle = respuesta.json()
    assert detalle["name"] == "Tarragona"
    assert detalle["visits"]["total"] == 2
    assert detalle["visits"]["last_epoch"] > 0
    assert detalle["visits_by_source"]["app"]["total"] == 2
    assert detalle["errors"]["total"] == 2
    assert {tipo["kind"] for tipo in detalle["error_kinds"]} == {"timeout", "network"}
    assert len(detalle["recent_errors"]) == 2
    assert all(evento["epoch"] > 0 for evento in detalle["recent_errors"])
    codigos = {evento["kind"]: evento["status_code"] for evento in detalle["recent_errors"]}
    assert codigos == {"timeout": 504, "network": None}
    assert len(detalle["recent_visits"]) == 2
    # El idioma y la entrada viajan con la visita. Las dos caen en el mismo
    # segundo, así que se busca por su contenido y no por su posición.
    desde_google = next(v for v in detalle["recent_visits"] if v["entry"] == "search")
    assert desde_google["language"] == "ca"
    assert desde_google["referrer_domain"] == "google.es"
    assert desde_google["device"] == "mobile"
    directa = next(v for v in detalle["recent_visits"] if v["entry"] == "direct")
    assert directa["referrer_domain"] == ""
    assert directa["device"] == ""
    assert sorted(detalle["visits_by_device"], key=lambda f: f["device"]) == [
        {"device": "", "d30": 1, "total": 1},
        {"device": "mobile", "d30": 1, "total": 1},
    ]
    assert sorted(detalle["visits_by_language"], key=lambda f: f["language"]) == [
        {"language": "ca", "d30": 1, "total": 1},
        {"language": "es", "d30": 1, "total": 1},
    ]
    assert sorted(detalle["visits_by_entry"], key=lambda f: f["entry"]) == [
        {"entry": "direct", "d30": 1, "total": 1},
        {"entry": "search", "d30": 1, "total": 1},
    ]
    # La visita directa no arrastra el dominio que venía en el cuerpo.
    assert detalle["referrers"] == [
        {"domain": "google.es", "d30": 1, "total": 1, "last_epoch": desde_google["epoch"]}
    ]

    # El identificador se normaliza igual que al registrar (red en mayúsculas).
    assert stats_client.get(
        "/v1/stats/station",
        params={"provider": "meteocat", "station_id": "X4"},
        headers={"X-Stats-Password": "s3creto"},
    ).status_code == 200
    # Estación sin ningún evento → 404, no una ficha vacía.
    assert stats_client.get(
        "/v1/stats/station",
        params={"provider": "AEMET", "station_id": "no-existe"},
        headers={"X-Stats-Password": "s3creto"},
    ).status_code == 404


def test_visit_language_diagnostics(stats_client):
    response = stats_client.post('/v1/stats/visit',
        headers={'Accept-Language': 'it;q=0.5,es-ES;q=0.9', 'Cookie': 'meteolabx_language=fr'},
        json={'provider': 'AEMET', 'station_id': '3386A', 'language': 'fr',
              'browser_languages': 'es-ES,es', 'url_language': 'fr'})
    assert response.status_code == 204
    detail = stats_client.get('/v1/stats/station',
        params={'provider': 'AEMET', 'station_id': '3386A'},
        headers={'X-Stats-Password': 's3creto'}).json()
    visit = detail['recent_visits'][0]
    assert visit['language'] == 'fr'
    assert visit['url_language'] == 'fr'
    assert visit['browser_languages'] == 'es-es,es'
    assert visit['request_languages'] == 'es-es,it'
    assert visit['saved_language'] == 'fr'


def test_language_diagnostics_discard_free_text():
    assert usage_stats.language_tags('es,secret@example.com,/private,pt-BR;q=0.9,en;q=0') == 'es,pt-br'
    assert usage_stats.language_tags('es;q=NaN,fr;q=inf,it;q=oops') == ''


def test_language_diagnostics_migrate_without_rewriting_history(tmp_path):
    import sqlite3
    from types import SimpleNamespace
    path = tmp_path / 'old-stats.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE station_visits (visit_pk INTEGER PRIMARY KEY, provider TEXT, station_id TEXT, name TEXT, source TEXT, language TEXT, entry TEXT, referrer_domain TEXT, device TEXT, epoch INTEGER)")
        connection.execute("INSERT INTO station_visits VALUES (1, 'AEMET', '3386A', 'Navalvillar', 'app', 'fr', 'direct', '', 'mobile', 1788710000)")
    settings = SimpleNamespace(usage_stats_path=str(path))
    detail = usage_stats.station_detail('AEMET', '3386A', settings=settings)
    assert detail['visits']['total'] == 1
    old = detail['recent_visits'][0]
    assert old['language'] == 'fr'
    assert old['browser_languages'] == old['request_languages'] == old['saved_language'] == old['url_language'] == ''
    usage_stats.record_visit('AEMET', '3386A', language='es', browser_languages='es-ES', url_language='es', settings=settings)
    assert usage_stats.station_detail('AEMET', '3386A', settings=settings)['visits']['total'] == 2


def test_page_request_and_renderer_request_are_distinct(stats_client):
    assert stats_client.post('/v1/stats/visit', headers={
        'Accept-Language': 'en-US',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15'},
        json={'provider': 'AEMET', 'station_id': '3386A', 'language': 'es',
              'url_language': 'es', 'browser_languages': 'en-US',
              'page_request_languages': '', 'language_reason': 'url'}).status_code == 204
    detail = stats_client.get('/v1/stats/station', params={'provider': 'AEMET', 'station_id': '3386A'},
                              headers={'X-Stats-Password': 's3creto'}).json()
    visit = detail['recent_visits'][0]
    assert visit['page_request_languages'] == ''
    assert visit['language_reason'] == 'url'
    assert visit['request_languages'] == visit['browser_languages'] == 'en-us'
    assert visit['language'] == visit['url_language'] == 'es'
    assert visit['request_client'] == 'unidentified'


def test_client_classification_never_claims_human_identity():
    assert usage_stats.request_client('Mozilla/5.0 Safari/605.1.15') == 'unidentified'
    assert usage_stats.request_client('') == 'unidentified'
    assert usage_stats.request_client('bingbot/2.0') == 'bingbot'
    assert usage_stats.request_client('HeadlessChrome/140') == 'other_bot'


def test_only_self_declared_crawlers_are_discarded():
    assert usage_stats.is_crawler('Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)')
    assert usage_stats.is_crawler('bingbot/2.0')
    assert usage_stats.is_crawler('HeadlessChrome/140')
    # Lo que no se declara sigue contando: no hay forma de confirmar que sea
    # una persona, y descartar por sospecha perdería visitas reales.
    assert not usage_stats.is_crawler('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1')
    assert not usage_stats.is_crawler('')


GOOGLEBOT = {'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'}


def test_crawler_visits_never_reach_any_table(stats_client):
    """Googlebot renderiza la ficha y llegaba a los mismos avisos que una persona."""
    station = {'provider': 'AEMET', 'station_id': '3386A', 'name': 'REUS'}
    assert stats_client.post('/v1/stats/visit', headers=GOOGLEBOT,
                             json={**station, 'language': 'es', 'language_reason': 'url'}).status_code == 204
    assert stats_client.post('/v1/stats/error', headers=GOOGLEBOT,
                             json={**station, 'error_kind': 'timeout'}).status_code == 204
    assert stats_client.post('/v1/stats/seo-view', headers=GOOGLEBOT,
                             json={**station, 'language': 'es'}).status_code == 204
    assert stats_client.post('/v1/stats/panel-click', headers=GOOGLEBOT,
                             json={**station, 'language': 'es'}).status_code == 204
    assert stats_client.post('/v1/stats/section', headers=GOOGLEBOT,
                             json={'section': 'observation'}).status_code == 204

    # Sin una sola fila registrada, la estación ni siquiera existe para el panel.
    assert stats_client.get('/v1/stats/station', params={'provider': 'AEMET', 'station_id': '3386A'},
                            headers={'X-Stats-Password': 's3creto'}).status_code == 404


def test_purge_removes_crawler_visits_already_stored(tmp_path):
    settings = _settings(tmp_path)
    usage_stats.record_visit('AEMET', '3386A', 'REUS', request_client='googlebot', settings=settings)
    usage_stats.record_visit('AEMET', '3386A', 'REUS', request_client='bingbot', settings=settings)
    usage_stats.record_visit('AEMET', '3386A', 'REUS', request_client='other_bot', settings=settings)
    usage_stats.record_visit('AEMET', '3386A', 'REUS', request_client='unidentified', settings=settings)
    # Las visitas anteriores a que se registrara el cliente lo llevan vacío:
    # no se sabe qué eran, así que se conservan.
    usage_stats.record_visit('AEMET', '3386A', 'REUS', settings=settings)

    assert usage_stats.purge_crawler_visits(settings=settings) == 3
    detail = usage_stats.station_detail('AEMET', '3386A', settings=settings)
    assert detail['visits']['total'] == 2
    assert usage_stats.purge_crawler_visits(settings=settings) == 0


def test_discarding_crawlers_does_not_touch_real_visits(stats_client):
    station = {'provider': 'AEMET', 'station_id': '3386A', 'name': 'REUS'}
    persona = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1'}
    assert stats_client.post('/v1/stats/visit', headers=persona,
                             json={**station, 'language': 'es'}).status_code == 204
    assert stats_client.post('/v1/stats/visit', headers=GOOGLEBOT,
                             json={**station, 'language': 'es'}).status_code == 204
    detail = stats_client.get('/v1/stats/station', params={'provider': 'AEMET', 'station_id': '3386A'},
                              headers={'X-Stats-Password': 's3creto'}).json()
    assert detail['visits']['total'] == 1


# =====================================================================
# Mapas de predicción
# =====================================================================

def test_forecast_map_views_rank_by_last_30_days(tmp_path):
    settings = _settings(tmp_path)
    for _ in range(3):
        usage_stats.record_forecast_map_view(
            "arome", "cape", label="CAPE", category="convection", settings=settings,
        )
    usage_stats.record_forecast_map_view(
        "arome", "temperature-2m", label="Temperatura a 2 m", category="temperature",
        settings=settings,
    )
    # Un mapa visto hace dos meses cuenta en el total, no en los 30 días.
    antiguo = int(time.time()) - 60 * 24 * 3600
    with usage_stats._connect(settings) as connection:
        for _ in range(5):
            connection.execute(
                "INSERT INTO forecast_map_views(model, product, label, category, epoch)"
                " VALUES ('arome', 'temperature-2m', 'Temperatura a 2 m', 'temperature', ?)",
                (antiguo,),
            )

    summary = usage_stats.forecast_map_summary(settings=settings)

    assert [row["product"] for row in summary["maps"]] == ["cape", "temperature-2m"]
    cape, temperatura = summary["maps"]
    assert (cape["d30"], cape["total"], cape["label"]) == (3, 3, "CAPE")
    assert (temperatura["d30"], temperatura["total"]) == (1, 6)
    assert summary["totals"] == {"d1": 4, "d7": 4, "d30": 4, "total": 9, "maps": 2}
    assert summary["categories"][0] == {"category": "convection", "d30": 3, "total": 3}


def test_forecast_map_view_rejects_unknown_models_and_odd_ids(tmp_path):
    settings = _settings(tmp_path)
    usage_stats.record_forecast_map_view("gfs", "cape", settings=settings)
    usage_stats.record_forecast_map_view("arome", "'; DROP TABLE x;--", settings=settings)
    usage_stats.record_forecast_map_view("AROME", "Wind-Level", category="Dynamics!", settings=settings)

    maps = usage_stats.forecast_map_summary(settings=settings)["maps"]
    assert [(row["model"], row["product"], row["category"]) for row in maps] == [
        ("arome", "wind-level", ""),
    ]


def test_forecast_map_endpoints(stats_client):
    enviado = stats_client.post(
        "/v1/stats/forecast-map",
        json={"model": "arome", "product": "cape", "label": "CAPE", "category": "convection"},
    )
    assert enviado.status_code == 204
    rastreador = stats_client.post(
        "/v1/stats/forecast-map",
        json={"model": "arome", "product": "cape"},
        headers={"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"},
    )
    assert rastreador.status_code == 204

    assert stats_client.get(
        "/v1/stats/forecast-maps", headers={"X-Stats-Password": "mala"}
    ).status_code == 401
    respuesta = stats_client.get(
        "/v1/stats/forecast-maps", headers={"X-Stats-Password": "s3creto"}
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["maps"][0]["total"] == 1


# =====================================================================
# Instalación de la PWA
# =====================================================================

def test_pwa_events_funnel_and_devices(tmp_path):
    settings = _settings(tmp_path)
    iphone = dict(os="ios", device="mobile", browser="safari", method="ios-safari")
    android = dict(os="android", device="mobile", browser="chrome", method="prompt")
    pc = dict(os="windows", device="desktop", browser="edge", method="desktop-chromium")
    for event, context in [
        ("offered", iphone), ("instructions", iphone), ("launched", {**iphone, "method": "installed"}),
        ("offered", android), ("prompt_accepted", android), ("installed", android),
        ("launched", {**android, "method": "installed"}),
        ("offered", pc), ("dismissed", pc),
        ("offered", iphone),
    ]:
        usage_stats.record_pwa_event(event, settings=settings, **context)

    summary = usage_stats.pwa_summary(settings=settings)
    totals = {row["event"]: row["total"] for row in summary["events"]}
    assert totals == {
        "offered": 4, "instructions": 1, "prompt_accepted": 1, "prompt_dismissed": 0,
        "installed": 1, "launched": 2, "dismissed": 1,
    }
    assert [row["event"] for row in summary["events"]] == list(usage_stats.PWA_EVENTS)
    por_so = {row["value"]: row for row in summary["by_os"]}
    assert (por_so["ios"]["launched"], por_so["ios"]["offered"]) == (1, 2)
    assert (por_so["android"]["installed"], por_so["android"]["launched"]) == (1, 1)
    assert por_so["windows"]["launched"] == 0
    assert summary["by_device"][0] == {
        "value": "mobile", "launched": 2, "installed": 1, "offered": 3, "launched_d30": 2,
    }
    metodos = {row["value"]: row for row in summary["by_method"]}
    assert metodos["prompt"]["prompt_accepted"] == 1
    assert metodos["desktop-chromium"]["dismissed"] == 1


def test_pwa_event_drops_unknown_values(tmp_path):
    settings = _settings(tmp_path)
    usage_stats.record_pwa_event("hacked", os="ios", settings=settings)
    usage_stats.record_pwa_event("LAUNCHED", os="ios", device="fridge", browser="netscape", settings=settings)
    summary = usage_stats.pwa_summary(settings=settings)
    assert sum(row["total"] for row in summary["events"]) == 1
    assert summary["by_device"] == [{"value": "", "launched": 1, "installed": 0, "offered": 0, "launched_d30": 1}]
    assert summary["by_browser"][0]["value"] == ""


def test_pwa_endpoints(stats_client):
    assert stats_client.post(
        "/v1/stats/pwa",
        json={"event": "launched", "os": "ipados", "device": "tablet", "browser": "safari", "method": "installed"},
    ).status_code == 204
    assert stats_client.post(
        "/v1/stats/pwa", json={"event": "launched", "os": "ios"},
        headers={"User-Agent": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"},
    ).status_code == 204
    assert stats_client.get("/v1/stats/pwa", headers={"X-Stats-Password": "mala"}).status_code == 401
    respuesta = stats_client.get("/v1/stats/pwa", headers={"X-Stats-Password": "s3creto"})
    assert respuesta.status_code == 200
    body = respuesta.json()
    assert {row["event"]: row["total"] for row in body["events"]}["launched"] == 1
    assert body["by_os"][0]["value"] == "ipados"


def test_pwa_values_match_the_frontend():
    """Si el navegador envía un valor que aquí no está, se guarda vacío sin avisar."""
    import re
    from pathlib import Path

    web = Path(__file__).resolve().parents[2] / "web" / "src"
    platform = (web / "lib" / "pwa" / "platform.js").read_text(encoding="utf-8")
    bloque = platform[platform.index("export const INSTALL_METHODS"):]
    bloque = bloque[:bloque.index("];")]
    assert set(re.findall(r"'([a-z-]+)'", bloque)) == usage_stats.PWA_METHODS

    # Cada línea que llama a `recordPwaEvent` o a `recordOnce` lleva el evento
    # entre comillas (o los dos de un ternario, como el del diálogo).
    enviados = set()
    for path in (web / "lib").rglob("*"):
        if path.suffix not in {".js", ".svelte"}:
            continue
        for linea in path.read_text(encoding="utf-8").splitlines():
            llamada = "recordPwaEvent(" in linea or "recordOnce(" in linea
            if llamada and "function " not in linea:
                enviados |= set(re.findall(r"'([a-z_]+)'", linea))
    assert enviados <= set(usage_stats.PWA_EVENTS)
    assert {"offered", "instructions", "installed", "launched", "dismissed", "prompt_accepted", "prompt_dismissed"} <= enviados
