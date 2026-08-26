"""Reparto de trabajo y frenos de memoria del worker de predicción."""

from __future__ import annotations

import pytest




def test_memory_ratio_ignores_the_reclaimable_page_cache(monkeypatch, tmp_path):
    """El freno mide la memoria anónima, no el total del cgroup.

    memory.current incluye el page cache, y desde que los perfiles se sirven
    desde un fichero mapeado buena parte de ese cache es nuestro: memoria que
    el núcleo suelta en cuanto aprieta en vez de invocar al OOM. Contarla como
    ocupada frenaba un segundo perfil que sí cabía.
    """
    from pathlib import Path as RutaReal
    import scripts.forecast_worker as trabajador

    (tmp_path / "memory.current").write_text("6000000000")   # 6 GB con cache
    (tmp_path / "memory.max").write_text("8000000000")
    (tmp_path / "memory.stat").write_text("anon 3200000000\nfile 2800000000\n")

    def ruta_falsa(texto):
        nombre = str(texto).rsplit("/", 1)[-1]
        return tmp_path / nombre if "cgroup" in str(texto) else RutaReal(texto)

    monkeypatch.setattr(trabajador, "Path", ruta_falsa)

    # 3,2 de 8 GB, no 6 de 8.
    assert trabajador._container_memory_ratio() == pytest.approx(0.4)


def test_memory_ratio_falls_back_to_the_total_without_a_breakdown(monkeypatch, tmp_path):
    """Sin memory.stat se usa el total, que peca de conservador pero no miente."""
    from pathlib import Path as RutaReal
    import scripts.forecast_worker as trabajador

    (tmp_path / "memory.current").write_text("6000000000")
    (tmp_path / "memory.max").write_text("8000000000")

    def ruta_falsa(texto):
        nombre = str(texto).rsplit("/", 1)[-1]
        return tmp_path / nombre if "cgroup" in str(texto) else RutaReal(texto)

    monkeypatch.setattr(trabajador, "Path", ruta_falsa)

    assert trabajador._container_memory_ratio() == pytest.approx(0.75)


def test_isolated_jobs_configure_their_own_logging(monkeypatch):
    """Cada trabajo aislado reconfigura el log al arrancar.

    Se aíslan con «spawn», que arranca un intérprete limpio. Sin esto el hijo
    se queda en WARNING y todo lo que cuenta el trabajo de verdad —el reparto
    de tiempo por fases, las descargas de paquetes, las caídas al WCS— se
    pierde sin dejar rastro, que es como estuvo hasta ahora.
    """
    import logging

    import scripts.forecast_worker as trabajador

    configurado = []
    monkeypatch.setattr(trabajador, "_configure_logging",
                        lambda: configurado.append(True))
    monkeypatch.setattr(trabajador, "get_settings",
                        lambda: (_ for _ in ()).throw(RuntimeError("basta")))

    class ColaFalsa:
        def __init__(self): self.puesto = []
        def put(self, valor): self.puesto.append(valor)

    cola = ColaFalsa()
    trabajador._isolated_job_entry(cola, {})

    assert configurado, "el hijo debe configurar su propio log"
    assert cola.puesto and cola.puesto[0][0] == "error"


def test_logging_setup_overrides_an_inherited_configuration():
    """basicConfig con force: si no, una config previa lo deja mudo."""
    import logging

    import scripts.forecast_worker as trabajador

    logging.basicConfig(level=logging.CRITICAL, force=True)
    try:
        trabajador._configure_logging()
        assert logging.getLogger().level == logging.INFO
    finally:
        logging.basicConfig(level=logging.WARNING, force=True)


def _trabajo(hora, tier=2, run="2026-08-26T12:00:00Z", dia=26):
    import scripts.forecast_worker as trabajador

    return trabajador.ForecastJob(
        run=run,
        valid_time=f"2026-08-{dia:02d}T{hora:02d}:00:00Z",
        products=("mucape-muli",),
        scope="model",
        tier=tier,
    )


def test_prefetch_skips_the_block_already_in_use():
    """Se adelanta el bloque siguiente, no el que se está usando.

    El bloque en curso ya lo está bajando quien lo necesita; pedirlo otra vez
    solo serviría para quedarse esperando en su cerrojo sin adelantar nada.
    """
    import scripts.forecast_worker as trabajador

    # 12Z: las horas 12-18 son el bloque 00H06H y las 19+ el siguiente.
    trabajos = [_trabajo(h) for h in (12, 13, 14, 15, 19, 20, 26)]

    objetivos = trabajador._blocks_ahead(trabajos, limit=1)

    assert len(objetivos) == 1
    _, hora = objetivos[0]
    assert hora.hour == 19, "debe adelantar el segundo bloque, no el primero"


def test_prefetch_ignores_jobs_that_do_not_use_packages():
    """Los productos nativos (nivel 0) no leen paquetes; no cuentan."""
    import scripts.forecast_worker as trabajador

    trabajos = [_trabajo(h, tier=0) for h in (12, 19, 26)]

    assert trabajador._blocks_ahead(trabajos, limit=2) == []


def test_prefetch_does_not_start_without_packages(monkeypatch):
    """Sin credencial de paquetes no se lanza ningún hilo."""
    import threading

    import scripts.forecast_worker as trabajador
    from server.services import arome_forecast

    monkeypatch.setattr(arome_forecast, "_packages_available", lambda: False)

    hilo = trabajador._start_package_prefetch(
        [_trabajo(h) for h in (12, 19)], threading.Event()
    )

    assert hilo is None


def test_prefetch_covers_the_whole_run_not_just_the_next_block():
    """Con margen de disco se adelantan todos los bloques de la pasada.

    Los perfiles convectivos empiezan mucho después que los niveles 0 y 1, así
    que da tiempo a bajarlo todo mientras aquéllos calculan. El objetivo es que
    un perfil no espere nunca a una descarga.
    """
    import scripts.forecast_worker as trabajador

    # 52 horas de pasada: ocho bloques de siete.
    trabajos = [_trabajo(h % 24, run="2026-08-26T12:00:00Z") for h in range(12, 24)]
    trabajos += [
        trabajador.ForecastJob(
            run="2026-08-26T12:00:00Z",
            valid_time=f"2026-08-2{7 + h // 24}T{h % 24:02d}:00:00Z",
            products=("mucape-muli",), scope="model", tier=2,
        )
        for h in range(0, 40)
    ]

    assert trabajador.PREFETCH_BLOCKS >= 8, "debe cubrir una pasada entera"
    objetivos = trabajador._blocks_ahead(trabajos, limit=trabajador.PREFETCH_BLOCKS)
    assert len(objetivos) >= 5, f"solo adelanta {len(objetivos)} bloques"


def test_prefetch_keeps_going_when_one_block_is_not_published_yet(monkeypatch):
    """Un bloque que aún no existe no cancela el adelanto de los demás.

    Durante la publicación los últimos plazos tardan en aparecer; abandonar al
    primer fallo dejaba sin adelantar todo lo que sí estaba disponible.
    """
    import threading

    import scripts.forecast_worker as trabajador
    from server.services import arome_forecast

    monkeypatch.setattr(arome_forecast, "_packages_available", lambda: True)
    pedidos = []

    def a_veces_falla(paquete, run, valid_time):
        pedidos.append((paquete, valid_time.hour))
        if valid_time.hour == 19:
            raise trabajador.AromePackageError("todavía no publicado")
        return "ruta"

    monkeypatch.setattr(trabajador, "ensure_package", a_veces_falla)

    # 12:00 del 26 -> bloque 00H06H; 19:00 del 26 -> 07H13H; 02:00 del 27 -> 14H20H.
    trabajos = [_trabajo(12), _trabajo(19), _trabajo(2, dia=27)]
    hilo = trabajador._start_package_prefetch(trabajos, threading.Event())
    assert hilo is not None
    hilo.join(timeout=5)

    horas = {hora for _, hora in pedidos}
    assert 2 in horas, "debe seguir con el bloque siguiente al que falla"
