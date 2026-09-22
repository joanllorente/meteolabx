"""Informes de pasada y avisos por correo.

Lo que se comprueba aquí es sobre todo cuándo NO se avisa: un vigilante que
escribe de más se ignora, y entonces da igual lo bien que detecte los fallos.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.services import alerts, health_alerts, run_report


def _manifiesto(**cambios):
    base = {
        "run": "2026-09-19T12:00:00Z",
        "forecast_model": "arome",
        "model": "AROME France 0,025°",
        "status": "complete",
        "created_at": "2026-09-19T14:00:00Z",
        "updated_at": "2026-09-19T15:20:00Z",
        "worker_heartbeat_at": "2026-09-19T15:20:00Z",
        "tier_timing": {
            "0": {"first_start": "2026-09-19T14:00:00Z",
                  "last_start": "2026-09-19T14:30:00Z", "jobs": 306},
            "3": {"first_start": "2026-09-19T14:35:00Z",
                  "last_start": "2026-09-19T15:20:00Z", "jobs": 38},
        },
        "products": {"t2m": {"available_times": ["2026-09-19T13:00:00Z"], "errors": {}}},
        "progress": {"frames_available": 344, "frames_total": 344, "percent": 100.0},
    }
    base.update(cambios)
    return base


def test_una_pasada_limpia_no_genera_correo():
    informe = run_report.build_report(_manifiesto())
    assert informe["severity"] == "ok"
    assert informe["issues"] == []
    assert run_report.alert_for_report(informe) is None


def test_con_nivel_all_tambien_escribe_la_pasada_limpia(monkeypatch):
    """Un correo por pasada, si eso es lo que se pide expresamente."""
    monkeypatch.setenv("METEOLABX_ALERT_EMAIL_LEVEL", "all")
    aviso = run_report.alert_for_report(run_report.build_report(_manifiesto()))
    assert aviso is not None
    assert aviso.severity == "info"
    assert aviso.subject == "✅ MeteoLabX · pasada 2026-09-19T12Z completa en 80 min"


def test_el_nivel_all_no_tapa_las_pasadas_con_problemas(monkeypatch):
    monkeypatch.setenv("METEOLABX_ALERT_EMAIL_LEVEL", "all")
    informe = run_report.build_report(_manifiesto(status="publishing"))
    aviso = run_report.alert_for_report(informe)
    assert aviso.severity == "fail"
    assert aviso.subject.startswith("🔴")


def test_la_duracion_se_mide_de_punta_a_punta():
    informe = run_report.build_report(_manifiesto())
    assert informe["duration_min"] == pytest.approx(80.0)
    assert [tramo["name"] for tramo in informe["tiers"]] == ["nativos", "DCAPE"]
    assert "nativos" in run_report.render_text(informe)


def test_una_pasada_sin_terminar_avisa_como_fallo():
    informe = run_report.build_report(
        _manifiesto(status="publishing",
                    progress={"frames_available": 100, "frames_total": 344, "percent": 29.1})
    )
    assert informe["severity"] == "fail"
    aviso = run_report.alert_for_report(informe)
    assert aviso is not None
    assert aviso.key == "forecast/run-report/2026-09-19T12:00:00Z"
    assert "no llegó a completarse" in aviso.body


def test_los_errores_se_agrupan_por_producto():
    manifiesto = _manifiesto(products={
        "dcape": {
            "available_times": [],
            "errors": {f"2026-09-19T{hora:02d}:00:00Z": "WCS 500: dominio ocupado"
                       for hora in range(10)},
        },
        "t2m": {"available_times": ["2026-09-19T13:00:00Z"], "errors": {}},
    })
    informe = run_report.build_report(manifiesto)
    assert informe["error_count"] == 10
    assert informe["errors"][0]["product"] == "dcape"
    assert informe["errors"][0]["message"] == "WCS 500: dominio ocupado"
    # Un producto sin un solo frame es lo que se nota en el visor.
    assert "dcape" in informe["empty_products"]
    assert informe["severity"] == "fail"


def test_una_pasada_mucho_mas_lenta_que_las_suyas_avisa():
    previas = [
        _manifiesto(run="2026-09-19T06:00:00Z", tier_timing={
            "0": {"first_start": "2026-09-19T08:00:00Z",
                  "last_start": "2026-09-19T08:40:00Z", "jobs": 306},
        }),
        _manifiesto(run="2026-09-19T00:00:00Z", tier_timing={
            "0": {"first_start": "2026-09-19T02:00:00Z",
                  "last_start": "2026-09-19T02:40:00Z", "jobs": 306},
        }),
    ]
    informe = run_report.build_report(_manifiesto(), previous=previas)
    assert informe["previous_median_min"] == pytest.approx(40.0)
    assert informe["severity"] == "warn"
    assert any("2.0×" in problema for problema in informe["issues"])


# --- Pasada atascada: el worker muerto por memoria ------------------------

def test_una_pasada_parada_se_detecta_aunque_el_worker_no_pueda_avisar():
    ahora = datetime(2026, 9, 19, 16, 30, tzinfo=timezone.utc)
    manifiesto = _manifiesto(status="publishing",
                             worker_heartbeat_at="2026-09-19T15:20:00Z")
    aviso = health_alerts.stalled_alert(manifiesto, now=ahora)
    assert aviso is not None
    assert aviso.severity == "fail"
    assert "70 min" in aviso.subject
    assert "memoria" in aviso.body


def test_una_pasada_que_acaba_de_latir_no_se_da_por_atascada():
    ahora = datetime(2026, 9, 19, 15, 30, tzinfo=timezone.utc)
    manifiesto = _manifiesto(status="publishing")
    assert health_alerts.stalled_alert(manifiesto, now=ahora) is None


def test_una_pasada_completa_nunca_esta_atascada():
    ahora = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    assert health_alerts.stalled_alert(_manifiesto(), now=ahora) is None


# --- Proveedores de estaciones -------------------------------------------

def test_varios_proveedores_caidos_a_la_vez_se_leen_como_fallo_propio():
    """El cuadro del 16/09/2026: la red del backend, no la de los proveedores."""
    aviso = health_alerts.provider_alert(
        {"aemet": 3, "iem": 3, "frost": 2, "meteocat": 4},
        attempted=["aemet", "iem", "frost", "meteocat", "smhi", "dmi"],
    )
    assert aviso is not None
    assert aviso.severity == "fail"
    assert aviso.key == "stations/providers-down"
    assert "4 de 6" in aviso.body


def test_un_proveedor_suelto_que_falla_un_rato_no_avisa():
    assert health_alerts.provider_alert({"aemet": 3}, attempted=["aemet", "iem"]) is None


def test_un_proveedor_caido_horas_avisa_pero_sin_alarma():
    aviso = health_alerts.provider_alert({"aemet": 7}, attempted=["aemet", "iem"])
    assert aviso is not None
    assert aviso.severity == "warn"
    assert "aemet" in aviso.subject


# --- Envío -----------------------------------------------------------------

def test_sin_credenciales_el_aviso_queda_en_el_log_y_no_revienta(monkeypatch, caplog):
    monkeypatch.delenv("METEOLABX_RESEND_API_KEY", raising=False)
    aviso = alerts.Alert(key="prueba", subject="algo", body="cuerpo", severity="fail")
    assert alerts.send(aviso) is False
    assert "ALERTA prueba" in caplog.text


def test_el_mismo_fallo_no_escribe_dos_veces_seguidas(monkeypatch, tmp_path):
    from server.services.forecast_store import LocalObjectStore

    enviados = []
    monkeypatch.setenv("METEOLABX_RESEND_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(alerts, "_post", lambda alerta: bool(enviados.append(alerta) or True))
    store = LocalObjectStore(root=tmp_path)
    aviso = alerts.Alert(key="stations/providers-down", subject="caídos", body="…")

    assert alerts.send(aviso, store=store) is True
    assert alerts.send(aviso, store=store) is False
    assert len(enviados) == 1


def test_un_correo_que_no_sale_se_reintenta_la_proxima_vez(monkeypatch, tmp_path):
    """Si Resend falla, el fallo no puede quedar dado por avisado."""
    from server.services.forecast_store import LocalObjectStore

    monkeypatch.setenv("METEOLABX_RESEND_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(alerts, "_post", lambda alerta: False)
    store = LocalObjectStore(root=tmp_path)
    aviso = alerts.Alert(key="forecast/run-stalled/x", subject="parada", body="…")

    assert alerts.send(aviso, store=store) is False
    intentos = []
    monkeypatch.setattr(alerts, "_post", lambda alerta: bool(intentos.append(alerta) or True))
    assert alerts.send(aviso, store=store) is True


def test_el_resumen_diario_cuenta_las_pasadas_limpias():
    resumen = health_alerts.digest_alert(
        [_manifiesto(), _manifiesto(run="2026-09-19T06:00:00Z", status="publishing")],
        now=datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc),
    )
    assert resumen.subject.startswith("🔴")
    assert "1/2 pasadas limpias" in resumen.subject
    assert resumen.severity == "info"
    assert resumen.key == "digest/2026-09-20"


# --- Endpoint ---------------------------------------------------------------

def test_el_informe_de_la_pasada_en_curso_se_calcula_al_vuelo():
    """Mientras publica todavía no hay informe guardado, y es cuando se mira."""
    from fastapi.testclient import TestClient

    from server.config import Settings, get_settings
    from server.main import create_app
    from server.services.forecast_store import (
        LATEST_MANIFEST_KEY,
        get_forecast_store,
        write_json,
    )

    write_json(get_forecast_store(), LATEST_MANIFEST_KEY, _manifiesto(status="publishing"))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        arome_api_key="test-token", ranking_refresh_enabled=False
    )
    with TestClient(app) as client:
        respuesta = client.get("/v1/forecast/arome/report")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["run"] == "2026-09-19T12:00:00Z"
    assert cuerpo["severity"] == "fail"


def test_sin_ninguna_pasada_el_informe_responde_404():
    from fastapi.testclient import TestClient

    from server.config import Settings, get_settings
    from server.main import create_app

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        arome_api_key="test-token", ranking_refresh_enabled=False
    )
    with TestClient(app) as client:
        assert client.get("/v1/forecast/arome/report").status_code == 404


def test_el_worker_guarda_el_informe_al_completar_la_pasada(monkeypatch):
    """El informe queda en el volumen aunque el correo no llegue a salir."""
    import scripts.forecast_worker as trabajador
    from server.services.forecast_store import get_forecast_store, read_json

    store = get_forecast_store()
    enviados = []
    monkeypatch.setattr(alerts, "send", lambda aviso, **kwargs: bool(enviados.append(aviso)))

    manifiesto = _manifiesto(status="publishing", products={
        "t2m": {"available_times": [], "errors": {"2026-09-19T13:00:00Z": "WCS 500"}},
    })
    assert trabajador._emit_run_report(store, manifiesto) is True

    guardado = read_json(store, run_report.report_key(manifiesto["run"]))
    assert guardado is not None
    assert guardado["severity"] == "fail"
    assert len(enviados) == 1


# --- Avisos que no pudieron salir ------------------------------------------

def test_el_aviso_de_que_no_hay_internet_se_guarda_y_sale_al_volver(monkeypatch, tmp_path):
    """El correo que cuenta que no hay red tampoco puede salir por la red.

    Se detecta el atasco, el correo falla, el proceso se reinicia. Sin cola,
    ese aviso —justo el del fallo que nos dejó horas sin estaciones— se
    perdería para siempre.
    """
    from server.services.forecast_store import LocalObjectStore

    monkeypatch.setenv("METEOLABX_RESEND_API_KEY", "clave-de-prueba")
    store = LocalObjectStore(root=tmp_path)
    monkeypatch.setattr(alerts, "_post", lambda alerta: False)
    assert alerts.send(health_alerts.egress_alert(restarting=True, pool={}), store=store) is False

    salidos = []
    monkeypatch.setattr(alerts, "_post", lambda alerta: bool(salidos.append(alerta) or True))
    assert alerts.flush_pending(store) == 1
    assert "no salía a internet" in salidos[0].subject
    assert "no pudo enviarse entonces" in salidos[0].body
    # Ya no queda nada pendiente ni se repite.
    assert alerts.flush_pending(store) == 0


def test_un_aviso_pendiente_caduca_en_vez_de_llegar_tarde(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone

    from server.services.forecast_store import LocalObjectStore, write_json

    monkeypatch.setenv("METEOLABX_RESEND_API_KEY", "clave-de-prueba")
    store = LocalObjectStore(root=tmp_path)
    viejo = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat().replace("+00:00", "Z")
    write_json(store, alerts.PENDING_KEY, {"version": 1, "alerts": [
        {"key": "backend/egress-stuck", "subject": "viejo", "body": "…",
         "severity": "fail", "queued_at": viejo},
    ]})
    monkeypatch.setattr(alerts, "_post", lambda alerta: pytest.fail("no debe enviarse"))
    assert alerts.flush_pending(store) == 0


def test_el_color_del_asunto_dice_la_gravedad_sin_abrir_el_correo():
    """Los tres estados usan el mismo símbolo en todos los vigilantes."""
    from datetime import datetime, timezone

    limpia = health_alerts.digest_alert(
        [_manifiesto()], now=datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
    )
    assert limpia.subject.startswith("✅")

    proveedor = health_alerts.provider_alert({"aemet": 7}, attempted=["aemet", "iem"])
    assert proveedor.subject.startswith("⚠️")

    parada = health_alerts.stalled_alert(
        _manifiesto(status="publishing", worker_heartbeat_at="2026-09-19T15:20:00Z"),
        now=datetime(2026, 9, 19, 17, 0, tzinfo=timezone.utc),
    )
    assert parada.subject.startswith("🔴")

    red = health_alerts.egress_alert(restarting=True, pool={})
    assert red.subject.startswith("🔴")


# --- Recursos, fallos y coste ----------------------------------------------

def _manifiesto_con_recursos(**cambios):
    gb = 1024 ** 3
    base = _manifiesto(
        resource_usage={
            "memory_peak_bytes": int(12.4 * gb),
            "memory_sum_bytes": int(10.5 * gb) * 40,
            "memory_samples": 40,
            "memory_limit_bytes": 32 * gb,
            "cpu_seconds": 18_600.0,
            # 18 min sumando cada descarga, pero solapadas en 10 de reloj.
            "downloads": {"packages": 18, "bytes": int(8.2 * gb), "seconds": 1080.0,
                          "wall_seconds": 600.0},
        },
    )
    base.update(cambios)
    return base


def test_el_informe_dice_cuanta_memoria_y_cpu_costo_la_pasada():
    informe = run_report.build_report(_manifiesto_con_recursos())
    recursos = informe["resources"]
    assert recursos["memory_peak_gb"] == pytest.approx(12.4, abs=0.05)
    assert recursos["memory_mean_gb"] == pytest.approx(10.5, abs=0.05)
    assert recursos["memory_limit_gb"] == pytest.approx(32.0)
    assert recursos["cpu_minutes"] == pytest.approx(310.0)


def test_el_informe_cuenta_las_descargas_de_grib():
    informe = run_report.build_report(_manifiesto_con_recursos())
    descargas = informe["resources"]["downloads"]
    assert descargas["packages"] == 18
    assert descargas["gb"] == pytest.approx(8.2, abs=0.05)
    assert descargas["minutes"] == pytest.approx(18.0)
    # 8,2 GiB en 18 min: los GB del informe son binarios, los MB/s decimales.
    # Es la velocidad de cada descarga, no lo que entró al contenedor.
    assert descargas["mb_s"] == pytest.approx(8.2 * 1024 ** 3 / 1e6 / 1080, abs=0.1)
    # El caudal real divide entre el tiempo de reloj, que es menor al solaparse.
    assert descargas["wall_minutes"] == pytest.approx(10.0)
    assert descargas["throughput_mb_s"] == pytest.approx(8.2 * 1024 ** 3 / 1e6 / 600, abs=0.1)
    # La pasada dura 80 min y en 10 hubo alguna descarga en marcha.
    assert descargas["share_of_run"] == pytest.approx(10 / 80, abs=0.01)


def test_sin_tiempo_de_reloj_no_se_inventa_caudal_ni_reparto():
    """Los manifiestos anteriores no traen wall_seconds: mejor nada que la suma."""
    gb = 1024 ** 3
    manifiesto = _manifiesto_con_recursos()
    manifiesto["resource_usage"]["downloads"] = {
        "packages": 18, "bytes": int(8.2 * gb), "seconds": 1080.0,
    }
    descargas = run_report.build_report(manifiesto)["resources"]["downloads"]
    assert descargas["mb_s"] is not None
    assert descargas["throughput_mb_s"] is None
    assert descargas["share_of_run"] is None
    assert "por descarga" in run_report.render_text(run_report.build_report(manifiesto))


def test_el_informe_mide_la_ocupacion_de_los_huecos():
    """Segundos ocupados entre huecos × duración: lo que falta es tiempo parado."""
    manifiesto = _manifiesto_con_recursos()
    manifiesto["tier_timing"] = {
        # 4 huecos durante 60 min = 240 min disponibles; 180 ocupados.
        "2": {"first_start": "2026-09-19T13:00:00Z", "last_start": "2026-09-19T13:55:00Z",
              "last_end": "2026-09-19T14:00:00Z", "jobs": 36,
              "slots": 4, "busy_seconds": 180 * 60.0},
    }
    informe = run_report.build_report(manifiesto)
    tramo = informe["tiers"][0]
    assert tramo["slots"] == 4
    assert tramo["occupancy"] == pytest.approx(0.75)
    # El tramo termina con el último trabajo, no con su arranque.
    assert tramo["end_min"] == pytest.approx(60.0)
    assert informe["occupancy"] == pytest.approx(0.75)
    texto = run_report.render_text(informe)
    assert "ocupación 75 % de 4 huecos" in texto
    assert "ocupación total 75 %" in texto


def test_el_coste_sale_de_las_tarifas_de_railway():
    informe = run_report.build_report(_manifiesto_con_recursos())
    coste = informe["cost"]
    # 10,5 GB de media durante 80 min = 840 GB-min a $0,000231.
    assert coste["memory_gb_min"] == pytest.approx(840.0, abs=5)
    assert coste["memory_usd"] == pytest.approx(0.194, abs=0.005)
    # 310 vCPU-min a $0,000463.
    assert coste["cpu_usd"] == pytest.approx(0.1435, abs=0.005)
    assert coste["total_usd"] == pytest.approx(0.3375, abs=0.01)
    # Las descargas son ingress: no se facturan y debe verse que es a propósito.
    assert coste["egress_usd"] == 0.0
    assert f"${coste['total_usd']:.2f}" in run_report.render_text(informe)


def test_las_tarifas_se_pueden_cambiar_sin_tocar_codigo(monkeypatch):
    monkeypatch.setenv("METEOLABX_PRICE_MEMORY_GB_MIN", "0.000462")
    informe = run_report.build_report(_manifiesto_con_recursos())
    assert informe["cost"]["memory_usd"] == pytest.approx(0.388, abs=0.01)


def test_los_trabajos_matados_por_memoria_se_cuentan_aparte():
    """Un trabajo que el contenedor mata no es un error de cálculo."""
    informe = run_report.build_report(
        _manifiesto_con_recursos(failure_kinds={"killed": 7, "provider": 2})
    )
    assert informe["failures"] == {"killed": 7, "provider": 2}
    assert informe["severity"] == "fail"
    assert any("por memoria" in problema for problema in informe["issues"])
    assert "matados por memoria 7" in run_report.render_text(informe)
    assert "caídos" in run_report.render_text(informe)


def test_rozar_el_techo_de_memoria_avisa_antes_de_que_maten_a_nadie():
    gb = 1024 ** 3
    informe = run_report.build_report(_manifiesto_con_recursos(resource_usage={
        "memory_peak_bytes": int(30 * gb), "memory_sum_bytes": int(20 * gb) * 10,
        "memory_samples": 10, "memory_limit_bytes": 32 * gb, "cpu_seconds": 600.0,
    }))
    assert informe["resources"]["memory_headroom"] == pytest.approx(0.94, abs=0.01)
    assert informe["severity"] == "warn"
    assert any("del techo" in problema for problema in informe["issues"])


def test_el_worker_clasifica_los_fallos_por_su_causa():
    import scripts.forecast_worker as trabajador

    assert trabajador._failure_kind("El subproceso terminó con código -9 sin resultado.") == "killed"
    assert trabajador._failure_kind("dcape 2026-09-20T12:00:00Z superó 900 s") == "timeout"
    assert trabajador._failure_kind("AromePackageError: HTTP 503") == "provider"
    assert trabajador._failure_kind("ValueError: rejilla vacía") == "other"


def test_las_descargas_se_apuntan_aunque_las_haga_otro_proceso(tmp_path, monkeypatch):
    """El registro vive en disco: los trabajos aislados son procesos aparte."""
    from datetime import datetime, timezone

    from server.services import arome_packages

    monkeypatch.setenv("METEOLABX_AROME_PACKAGE_CACHE_DIR", str(tmp_path))
    run = datetime(2026, 9, 20, 6, tzinfo=timezone.utc)
    arome_packages._record_download("IP1", run, "00H06H", 512_000_000, 62.0)
    arome_packages._record_download("SP1", run, "00H06H", 128_000_000, 11.0)

    resumen = arome_packages.download_stats(run)
    reloj = resumen.pop("wall_seconds")
    assert resumen == {"packages": 2, "bytes": 640_000_000, "seconds": 73.0}
    # Se apuntaron casi a la vez: la de 11 s cabe dentro de la de 62.
    assert reloj == pytest.approx(62.0, abs=1.0)
    # Otra pasada no hereda las descargas de la anterior.
    otra = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    assert arome_packages.download_stats(otra)["packages"] == 0


def test_el_tiempo_de_reloj_no_cuenta_dos_veces_lo_que_se_solapa():
    from server.services.arome_packages import _wall_seconds

    # Dos solapadas (0-60 y 30-90) y una aparte (200-210): 90 + 10.
    assert _wall_seconds([(30, 90), (0, 60), (200, 210)]) == pytest.approx(100.0)
    # Una dentro de otra no suma nada.
    assert _wall_seconds([(0, 100), (10, 20)]) == pytest.approx(100.0)
    assert _wall_seconds([]) == 0.0
