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

    good = stats_client.get(
        "/v1/stats/stations", headers={"X-Stats-Password": "s3creto"}
    )
    assert good.status_code == 200
    payload = good.json()
    assert payload["totals"]["total"] == 1
    assert payload["stations"][0]["station_id"] == "0076"


def test_stats_disabled_without_password(tmp_path, monkeypatch):
    from server import config as server_config

    settings = server_config.get_settings()
    monkeypatch.setattr(settings, "usage_stats_path", str(tmp_path / "stats.sqlite"), raising=False)
    monkeypatch.setattr(settings, "stats_admin_password", "", raising=False)
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/v1/stats/stations", headers={"X-Stats-Password": ""})
    assert response.status_code == 404
