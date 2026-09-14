import json

from server.services import inventory_total, stations


def _fake_counts(monkeypatch, counts):
    calls = []

    def fake():
        calls.append(1)
        return sum(counts.values())

    monkeypatch.setattr(stations, "inventory_station_count", fake)
    return calls


def test_se_cuenta_una_vez_y_queda_fijo(monkeypatch):
    inventory_total.reset_for_tests()
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    calls = _fake_counts(monkeypatch, {"ES": 10, "FR": 5})
    assert inventory_total.current()["total"] == 15
    _fake_counts(monkeypatch, {"ES": 99})
    assert inventory_total.current()["total"] == 15
    assert len(calls) == 1
    inventory_total.reset_for_tests()


def test_un_reinicio_del_mismo_deploy_reutiliza_el_recuento(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deploy-1")
    inventory_total.reset_for_tests()
    _fake_counts(monkeypatch, {"ES": 10})
    assert inventory_total.current()["total"] == 10
    saved = json.loads((tmp_path / "inventory_total.json").read_text())
    assert saved["deployment_id"] == "deploy-1"

    # Reinicio (proceso nuevo) dentro del mismo deploy: no se vuelve a contar.
    inventory_total.reset_for_tests()
    calls = _fake_counts(monkeypatch, {"ES": 12})
    assert inventory_total.current()["total"] == 10
    assert not calls

    # Deploy nuevo: se cuenta otra vez y se sobrescribe.
    inventory_total.reset_for_tests()
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deploy-2")
    assert inventory_total.current()["total"] == 12
    assert json.loads((tmp_path / "inventory_total.json").read_text())["deployment_id"] == "deploy-2"
    inventory_total.reset_for_tests()


def test_el_total_y_el_selector_no_cuentan_el_iem_que_esconde_el_mapa():
    stations._country_counts_cache.clear()
    visible = stations.country_counts()
    iem = stations.country_counts(providers=["IEM"])
    # En España y EE. UU. IEM solo trae duplicados de la red propia: fuera.
    assert iem["US"] > 100000
    assert visible["US"] < iem["US"]
    assert visible["ES"] == stations.country_counts(
        providers=[p for p in stations.CATALOG_PROVIDERS if p != "IEM"]
    )["ES"]
    # Donde no hay red propia, IEM cuenta.
    assert visible.get("IR", 0) >= iem.get("IR", 0) > 0
    assert stations.inventory_station_count() == sum(visible.values())
    assert len(stations.search_catalog(countries=["ES"], limit=250000)) == visible["ES"]
