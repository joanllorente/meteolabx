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
    usage_stats.record_visit("AEMET", "0076", "BARCELONA AEROPUERTO", settings=settings)
    usage_stats.record_visit("WU", "IMADRID1", "", settings=settings)

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

    top = summary["stations"][0]
    assert (top["provider"], top["station_id"]) == ("AEMET", "0076")
    assert top["total"] == 3
    assert top["d1"] == 2
    assert top["name"] == "BARCELONA AEROPUERTO"


def test_visit_normalizes_and_ignores_empty(tmp_path):
    settings = _settings(tmp_path)
    usage_stats.record_visit("wu", "IMADRID1", settings=settings)
    usage_stats.record_visit("", "X", settings=settings)   # ignorada
    usage_stats.record_visit("WU", "", settings=settings)  # ignorada
    summary = usage_stats.visit_summary(settings=settings)
    assert summary["totals"]["total"] == 1
    assert summary["stations"][0]["provider"] == "WU"


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
    assert summary["totals"]["errors"]["total"] == 1
    assert summary["stations"][0]["station_id"] == "0076"
    assert summary["stations"][0]["errors"]["last_kind"] == "network"


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
        json={"provider": "AEMET", "station_id": "0076", "name": "Barcelona Aeropuerto"},
    )
    assert ok.status_code == 204

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
    assert payload["totals"]["errors"]["total"] == 1
    assert payload["stations"][0]["station_id"] == "0076"
    assert payload["stations"][0]["errors"]["last_kind"] == "timeout"
    assert payload["error_kinds"][0]["kind"] == "timeout"


def test_stats_disabled_without_password(tmp_path, monkeypatch):
    from server import config as server_config

    settings = server_config.get_settings()
    monkeypatch.setattr(settings, "usage_stats_path", str(tmp_path / "stats.sqlite"), raising=False)
    monkeypatch.setattr(settings, "stats_admin_password", "", raising=False)
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/v1/stats/stations", headers={"X-Stats-Password": ""})
    assert response.status_code == 404
