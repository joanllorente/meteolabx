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


def test_prefetch_prioritizes_the_first_pending_block():
    import scripts.forecast_worker as trabajador
    trabajos = [_trabajo(h) for h in (19,20)] + [_trabajo(2,dia=27)]
    objetivos = trabajador._blocks_ahead(trabajos, limit=2)
    assert [hora.hour for _,hora in objetivos] == [19,1]


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

    def a_veces_falla(paquete, run, valid_time, **kwargs):
        pedidos.append((paquete, valid_time.hour))
        if valid_time.hour == 19:
            raise trabajador.AromePackageError("todavía no publicado")
        return "ruta"

    monkeypatch.setattr(trabajador, "ensure_package", a_veces_falla)

    # 12:00 del 26 -> 00H06H; 19:00 del 26 -> 07H12H; 01:00 del 27 -> 13H18H.
    trabajos = [_trabajo(12), _trabajo(19), _trabajo(1, dia=27)]
    hilo = trabajador._start_package_prefetch(trabajos, threading.Event())
    assert hilo is not None
    hilo.join(timeout=5)

    horas = {hora for _, hora in pedidos}
    assert 1 in horas, "debe seguir con el bloque siguiente al que falla"


def test_a_drained_tier_does_not_hold_back_the_free_workers(monkeypatch):
    """Sin pendientes del nivel activo, quien esté libre empieza el siguiente.

    Antes había que esperar a que el último trabajo del nivel terminase. Con un
    worker daba igual; con cuatro son tres parados en cada cambio de nivel.
    """
    import scripts.forecast_worker as trabajador

    # Un trabajo de nivel 1 aún corriendo, y en la cola solo queda nivel 2.
    activo = _trabajo(12, tier=1)
    cola = [_trabajo(13, tier=2), _trabajo(14, tier=2)]

    grupo_activo = trabajador._job_group(activo)
    siguiente = trabajador._job_group(cola[0])

    assert grupo_activo != siguiente
    assert not any(trabajador._job_group(j) == grupo_activo for j in cola), (
        "el nivel activo ya está drenado: nada debería bloquear al siguiente"
    )
    # La capacidad mezclada es la más estrecha, para no admitir de más.
    assert min(
        trabajador.tier_capacity_for(1, workers=6, heavy_workers=4),
        trabajador.tier_capacity_for(2, workers=6, heavy_workers=4),
    ) == 4


def test_the_memory_guard_covers_dcape_not_just_the_other_profiles():
    """El freno protege todos los niveles pesados, no sólo el 2.

    DCAPE es nivel 3 y es el perfil más caro de todos: usa bandas de 192 filas
    en vez de 64, porque su selección de capa de origen depende de cómo se
    particione la rejilla. Dejarlo fuera del freno permitía arrancar cuatro a la
    vez sin mirar la memoria, que es justo el caso que más aprieta.
    """
    from pathlib import Path as RutaReal
    import inspect

    import scripts.forecast_worker as trabajador

    fuente = inspect.getsource(trabajador._run_parallel_work)
    assert "launch_tier >= 2" in fuente, "el freno debe cubrir del nivel 2 en adelante"
    assert "launch_tier == 2" not in fuente, "quedaría DCAPE sin protección"
    assert "job.tier >= 2" in fuente, (
        "sin esto no se anota el lanzamiento de un DCAPE y el escalonado de "
        "15 s no lo tiene en cuenta"
    )
    # El nivel 3 comparte límite con el 2: ambos son un perfil completo.
    assert trabajador.tier_capacity_for(3, workers=6, heavy_workers=4) == 4


def test_without_a_fixed_cap_the_profiles_use_every_worker():
    """A 0 no hay tope: los perfiles pueden usar los mismos workers que el resto.

    El tope fijo existía porque un porcentaje de memoria no dice lo mismo en
    cada máquina. Ahora frena el hueco real, así que no hace falta adivinar el
    número de antemano.
    """
    import scripts.forecast_worker as trabajador

    assert trabajador.tier_capacity_for(2, workers=6, heavy_workers=0) == 6
    assert trabajador.tier_capacity_for(3, workers=6, heavy_workers=0) == 6
    # Un tope explícito se sigue respetando.
    assert trabajador.tier_capacity_for(2, workers=6, heavy_workers=2) == 2


def test_free_memory_gates_another_profile(monkeypatch):
    """Se admite otro perfil sólo si cabe entero en lo que queda libre.

    Un porcentaje no vale igual en todas las máquinas: el 55 % de 8 GB deja
    3,6 GB y el de 24 deja casi 11. Lo que decide es si cabe uno más.
    """
    import scripts.forecast_worker as trabajador

    GB = 1024**3
    monkeypatch.setattr(trabajador, "HEAVY_PROFILE_BYTES", 3 * GB)

    monkeypatch.setattr(trabajador, "_cgroup_memory", lambda: (14 * GB, 24 * GB))
    assert trabajador._room_for_another_profile(), "quedan 10 GB, cabe otro"

    monkeypatch.setattr(trabajador, "_cgroup_memory", lambda: (22 * GB, 24 * GB))
    assert not trabajador._room_for_another_profile(), "quedan 2 GB, no cabe"

    # Una máquina pequeña con el mismo hueco relativo que la grande de arriba
    # (58 % ocupado): en 24 GB cabía otro perfil, en 8 GB no.
    monkeypatch.setattr(trabajador, "_cgroup_memory", lambda: (int(5.5 * GB), 8 * GB))
    assert not trabajador._room_for_another_profile()

    monkeypatch.setattr(trabajador, "_cgroup_memory", lambda: None)
    assert trabajador._room_for_another_profile(), "sin cgroup legible, no se frena"


def test_zero_survives_every_clamp_down_to_the_capacity(monkeypatch):
    """El 0 debe llegar entero desde los argumentos hasta la capacidad.

    Significa «sin tope propio», pero varios max(1, ...) por el camino lo
    convertían en un único perfil a la vez: exactamente lo contrario. El valor
    por defecto es 0, así que el fallo dejaba la instalación entera en uno.
    """
    import inspect

    import scripts.forecast_worker as trabajador

    # El valor por defecto de la cadena entera es 0.
    firma = inspect.signature(trabajador.run_incremental_cycle)
    assert firma.parameters["heavy_workers"].default == 0

    # La normalización lo conserva en vez de subirlo a 1.
    assert trabajador._effective_heavy_workers(0, 6) == 0
    assert trabajador._effective_heavy_workers(-3, 6) == 0
    # Un tope explícito se respeta y se recorta al número de workers.
    assert trabajador._effective_heavy_workers(4, 6) == 4
    assert trabajador._effective_heavy_workers(9, 6) == 6

    # Y el resultado al final del camino: los perfiles usan los seis.
    assert trabajador.tier_capacity_for(2, 6, trabajador._effective_heavy_workers(0, 6)) == 6
    assert trabajador.tier_capacity_for(0, 6, trabajador._effective_heavy_workers(0, 6)) == 6


def test_prefetch_retries_blocks_that_are_not_published_yet(monkeypatch):
    """Un bloque que aún no existe se reintenta, no se abandona.

    Al principio de una pasada Météo-France publica los bloques poco a poco y
    la cizalladura, a menos de un minuto por hora, adelanta a la publicación.
    Si el adelanto se rinde a la primera, las horas siguientes acaban bajando
    el perfil campo a campo por el WCS.
    """
    import threading

    import scripts.forecast_worker as trabajador
    from server.services import arome_forecast

    monkeypatch.setattr(arome_forecast, "_packages_available", lambda: True)
    monkeypatch.setattr(trabajador, "PREFETCH_RETRY_S", 0)
    intentos = {"n": 0}

    def publicado_a_la_tercera(paquete, run, valid_time, **kwargs):
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise trabajador.AromePackageError("todavía no publicado")
        return "ruta"

    monkeypatch.setattr(trabajador, "ensure_package", publicado_a_la_tercera)

    hilo = trabajador._start_package_prefetch(
        [_trabajo(12), _trabajo(19)], threading.Event()
    )
    assert hilo is not None
    hilo.join(timeout=5)

    assert not hilo.is_alive(), "el hilo debe terminar cuando lo consigue"
    assert intentos["n"] >= 3, "debe insistir hasta que el bloque aparezca"


def test_prefetch_gives_up_after_the_deadline(monkeypatch):
    """No se persigue indefinidamente un bloque que nunca llega."""
    import threading

    import scripts.forecast_worker as trabajador
    from server.services import arome_forecast

    monkeypatch.setattr(arome_forecast, "_packages_available", lambda: True)
    monkeypatch.setattr(trabajador, "PREFETCH_RETRY_S", 0)
    monkeypatch.setattr(trabajador, "PREFETCH_DEADLINE_S", 0)

    def nunca(paquete, run, valid_time, **kwargs):
        raise trabajador.AromePackageError("nunca se publica")

    monkeypatch.setattr(trabajador, "ensure_package", nunca)

    hilo = trabajador._start_package_prefetch(
        [_trabajo(12), _trabajo(19)], threading.Event()
    )
    assert hilo is not None
    hilo.join(timeout=5)
    assert not hilo.is_alive(), "debe rendirse en vez de girar para siempre"


def test_prefetch_stops_when_the_cycle_ends(monkeypatch):
    """La señal de parada corta el adelanto aunque queden bloques."""
    import threading

    import scripts.forecast_worker as trabajador
    from server.services import arome_forecast

    monkeypatch.setattr(arome_forecast, "_packages_available", lambda: True)
    monkeypatch.setattr(trabajador, "PREFETCH_RETRY_S", 0)
    parar = threading.Event()

    def falla_y_para(paquete, run, valid_time, **kwargs):
        parar.set()
        raise trabajador.AromePackageError("todavía no publicado")

    monkeypatch.setattr(trabajador, "ensure_package", falla_y_para)

    hilo = trabajador._start_package_prefetch([_trabajo(12), _trabajo(19)], parar)
    assert hilo is not None
    hilo.join(timeout=5)
    assert not hilo.is_alive()


def test_prefetch_retry_fits_inside_a_cycle():
    """La espera entre reintentos tiene que caber varias veces en un ciclo.

    El hilo de adelanto muere cuando termina el ciclo que lo lanzó. Con una
    espera parecida a la duración del ciclo solo daba una vuelta antes de que
    lo cortaran, y el plazo largo no se alcanzaba nunca.
    """
    import os

    import scripts.forecast_worker as trabajador

    ciclo = int(os.getenv("METEOLABX_FORECAST_WORKER_CYCLE_BUDGET_S", "240"))
    assert trabajador.PREFETCH_RETRY_S * 3 <= ciclo, (
        f"con reintentos cada {trabajador.PREFETCH_RETRY_S} s apenas da vueltas "
        f"en un ciclo de {ciclo} s"
    )


def test_grouped_jobs_are_named_after_what_they_actually_compute():
    """Un grupo de cizalladuras no puede anunciarse como convectivo.

    Los de cizalladura también viajan agrupados, así que llamar convectivo a
    todo lo que tenga más de un producto hacía que el visor dijera
    «Diagnósticos convectivos» mientras calculaba cizalladuras.
    """
    import scripts.forecast_worker as trabajador

    assert trabajador._group_label(("shear-0-6",)) == "shear-0-6"
    assert trabajador._group_label(trabajador.SHEAR_PRODUCTS) == "shear-group"
    assert trabajador._group_label(trabajador.PROFILE_PRODUCTS) == "convective-group"
    assert trabajador._group_label(("shear-0-6", "mucape-muli")) == "mixed-group"


def test_prefetch_does_not_depend_on_what_the_catalog_has_announced():
    """Se persiguen los bloques del horizonte, no los de la cola.

    Al principio de una pasada el WCS solo anuncia las primeras horas, así que
    la cola pendiente llega hasta +6 y poco más. Derivar de ella los bloques a
    adelantar dejaba sin perseguir precisamente los que más tardan en
    publicarse: cuando los convectivos llegaran a +8, no habría paquete y esas
    treinta horas se resolverían campo a campo por el WCS.
    """
    from server.services.arome_packages import block_range

    import scripts.forecast_worker as trabajador

    # Cola como al arrancar la pasada: solo tres horas anunciadas.
    cola = [_trabajo(h, tier=1, run="2026-08-26T12:00:00Z") for h in (12, 13, 14)]

    objetivos = trabajador._blocks_ahead(cola, trabajador.PREFETCH_BLOCKS)
    bloques = [block_range(run, vt) for run, vt in objetivos]

    assert len(bloques) >= 5, f"con la cola corta solo persigue {bloques}"
    assert "31H36H" in bloques, "el final de la pasada también hace falta"
    # The immediate block goes first; prefetch never waits on another owner.
    assert bloques[0] == "00H06H"
    assert bloques[-1] == "31H36H"


def test_prefetch_horizon_covers_the_diagnostics_horizon():
    """El horizonte perseguido no puede quedarse corto respecto a los cálculos."""
    import os

    import scripts.forecast_worker as trabajador

    diagnosticos = int(os.getenv("METEOLABX_FORECAST_DIAGNOSTIC_MAX_HOURS", "36"))
    assert trabajador.PREFETCH_HORIZON_H >= diagnosticos


def _cola(horas, tier=2, run="2026-08-26T12:00:00Z"):
    return [({}, _trabajo(h, tier=tier, run=run)) for h in horas]


def test_an_hour_with_its_package_goes_first(monkeypatch):
    """Si la primera hora no tiene paquete y otra sí, esa se adelanta.

    Una hora de perfil sin paquete cuesta 102 peticiones al WCS, unos dos
    minutos de estrangulador, frente a dos con él. Adelantar la que ya lo tiene
    le da tiempo al GRIB de la otra a publicarse.
    """
    import scripts.forecast_worker as trabajador

    listos = {"2026-08-26T14:00:00Z"}
    monkeypatch.setattr(
        trabajador, "_profile_package_ready",
        lambda job: job.valid_time in listos,
    )

    pending = _cola([12, 13, 14, 15])
    grupo = trabajador._job_group(pending[0][1])
    trabajador._bring_forward_a_ready_hour(pending, grupo)

    assert pending[0][1].valid_time == "2026-08-26T14:00:00Z"
    # Las demás conservan su orden relativo.
    assert [j.valid_time[11:13] for _m, j in pending] == ["14", "12", "13", "15"]


def test_nothing_moves_when_the_first_hour_is_already_ready(monkeypatch):
    import scripts.forecast_worker as trabajador

    monkeypatch.setattr(trabajador, "_profile_package_ready", lambda job: True)
    pending = _cola([12, 13, 14])
    antes = [j.valid_time for _m, j in pending]

    trabajador._bring_forward_a_ready_hour(pending, trabajador._job_group(pending[0][1]))

    assert [j.valid_time for _m, j in pending] == antes


def test_the_run_does_not_stall_when_no_hour_has_its_package(monkeypatch):
    """Si ninguna hora tiene paquete se sigue igualmente, por el WCS.

    Quedarse esperando a Météo-France dejaría la pasada parada, que es peor que
    pagar las peticiones.
    """
    import scripts.forecast_worker as trabajador

    monkeypatch.setattr(trabajador, "_profile_package_ready", lambda job: False)
    pending = _cola([12, 13, 14])
    antes = [j.valid_time for _m, j in pending]

    trabajador._bring_forward_a_ready_hour(pending, trabajador._job_group(pending[0][1]))

    assert [j.valid_time for _m, j in pending] == antes


def test_lighter_tiers_are_not_reordered(monkeypatch):
    """Cizalladura y nativos no se reordenan: sin paquete cuestan mucho menos."""
    import scripts.forecast_worker as trabajador

    monkeypatch.setattr(trabajador, "_profile_package_ready", lambda job: False)
    for tier in (0, 1):
        pending = _cola([12, 13], tier=tier)
        antes = [j.valid_time for _m, j in pending]
        trabajador._bring_forward_a_ready_hour(
            pending, trabajador._job_group(pending[0][1])
        )
        assert [j.valid_time for _m, j in pending] == antes, tier


def test_reordering_never_crosses_into_another_group(monkeypatch):
    """Sólo se busca dentro del mismo nivel y la misma pasada."""
    import scripts.forecast_worker as trabajador

    # La hora lista está en otro nivel: no debe adelantarse.
    monkeypatch.setattr(
        trabajador, "_profile_package_ready",
        lambda job: job.tier == 3,
    )
    pending = _cola([12, 13]) + _cola([14], tier=3)
    antes = [j.valid_time for _m, j in pending]

    trabajador._bring_forward_a_ready_hour(pending, trabajador._job_group(pending[0][1]))

    assert [j.valid_time for _m, j in pending] == antes


def test_accumulative_products_are_not_expected_at_the_run_hour():
    """Un acumulado de una hora no existe en el instante de la pasada.

    Lluvia, racha y radiación se publican con periodo PT1H: la primera hora
    disponible es la +1, no la +0. Contarles esa hora dejaba el progreso
    topando en el 99,5 % con absolutamente todo calculado, y no había forma de
    distinguir eso de un fallo real.
    """
    import scripts.forecast_worker as trabajador
    from server.services.forecast_store import PERSISTED_FORECAST_PRODUCTS

    manifiesto = {"expected_hours": {"native": 52, "diagnostic": 36}}

    assert trabajador._expected_hours(manifiesto, "precip-1h") == 51
    assert trabajador._expected_hours(manifiesto, "accumulated-precip") == 51
    assert trabajador._expected_hours(manifiesto, "wind-gust") == 51
    assert trabajador._expected_hours(manifiesto, "shortwave-down") == 51
    # La nubosidad tampoco existe en H+0, aunque su cobertura no declare
    # periodo: se marca a mano en el catálogo de productos.
    assert trabajador._expected_hours(manifiesto, "cloud-cover") == 51
    assert trabajador._first_available_hour("cloud-cover") == 1
    assert trabajador._first_available_hour("temperature-2m") == 0
    # Los instantáneos conservan las 52.
    assert trabajador._expected_hours(manifiesto, "temperature-2m") == 52
    # Y los diagnósticos, su propio límite.
    assert trabajador._expected_hours(manifiesto, "mucape-muli") == 36

    total = sum(
        trabajador._expected_hours(manifiesto, p) for p in PERSISTED_FORECAST_PRODUCTS
    )
    # Sube al añadir un mapa: theta-e + MSLP y la reflectividad aportaron sus
    # 52 plazos nativos cada uno.
    assert total == 1441, f"el denominador de una pasada completa es 1441, no {total}"


def test_prefetch_also_brings_the_dcape_package(monkeypatch):
    """IP3 se adelanta pronto: lo necesitan dos niveles, no sólo DCAPE.

    Cuando iba el último, los primeros perfiles del nivel 2 se quedaban
    esperando medio giga y agotaban su límite de cálculo.
    """
    import threading

    import scripts.forecast_worker as trabajador
    from server.services import arome_forecast

    monkeypatch.setattr(arome_forecast, "_packages_available", lambda: True)
    monkeypatch.setattr(trabajador, "PREFETCH_RETRY_S", 0)
    monkeypatch.setattr(trabajador, "PREFETCH_STREAMS", 1)
    pedidos = []
    monkeypatch.setattr(
        trabajador, "ensure_package",
        lambda paquete, run, vt, **kwargs: pedidos.append(paquete) or "ruta",
    )

    hilo = trabajador._start_package_prefetch([_trabajo(12), _trabajo(19)], threading.Event())
    assert hilo is not None
    hilo.join(timeout=5)

    assert "IP3" in pedidos
    # Detrás de IP1, que es el que necesita todo el mundo, y por delante de los
    # de superficie, que pesan cincuenta megas y llegan enseguida.
    primero = pedidos[:4]
    assert primero.index("IP1") == 0, f"IP1 primero: {primero}"
    assert primero.index("IP3") == 1, f"IP3 detrás de IP1: {primero}"


def test_a_declared_limit_is_used_when_the_cgroup_has_none(monkeypatch, tmp_path):
    """Con el cgroup en «max» manda el límite declarado a mano.

    Algunos alojamientos no publican límite. Sin uno declarado no hay contra
    qué comparar y el freno se desactivaba en silencio, que es justo donde más
    falta hace: nada impide que los perfiles pasen del techo real.
    """
    from pathlib import Path as RutaReal

    import scripts.forecast_worker as trabajador

    GB = 1024**3
    (tmp_path / "memory.current").write_text(str(10 * GB))
    (tmp_path / "memory.max").write_text("max")
    (tmp_path / "memory.stat").write_text(f"anon {9 * GB}\n")

    def ruta_falsa(texto):
        nombre = str(texto).rsplit("/", 1)[-1]
        return tmp_path / nombre if "cgroup" in str(texto) else RutaReal(texto)

    monkeypatch.setattr(trabajador, "Path", ruta_falsa)
    monkeypatch.setattr(trabajador, "DECLARED_MEMORY_LIMIT_B", 24 * GB)

    assert trabajador._cgroup_memory() == (9 * GB, 24 * GB)
    monkeypatch.setattr(trabajador, "HEAVY_PROFILE_BYTES", 3 * GB)
    assert trabajador._room_for_another_profile(), "quedan 15 GB"

    (tmp_path / "memory.stat").write_text(f"anon {22 * GB}\n")
    assert not trabajador._room_for_another_profile(), "quedan 2 GB, no cabe otro"


def test_without_any_limit_the_guard_says_so(monkeypatch, tmp_path):
    """Sin límite de ningún tipo se avisa en vez de callar."""
    from pathlib import Path as RutaReal
    import logging

    import scripts.forecast_worker as trabajador

    (tmp_path / "memory.current").write_text("1000")
    (tmp_path / "memory.max").write_text("max")

    def ruta_falsa(texto):
        nombre = str(texto).rsplit("/", 1)[-1]
        return tmp_path / nombre if "cgroup" in str(texto) else RutaReal(texto)

    monkeypatch.setattr(trabajador, "Path", ruta_falsa)
    monkeypatch.setattr(trabajador, "DECLARED_MEMORY_LIMIT_B", 0)
    trabajador._warn_memory_is_unbounded.cache_clear()

    with monkeypatch.context():
        import io
        registro = io.StringIO()
        manejador = logging.StreamHandler(registro)
        trabajador.logger.addHandler(manejador)
        try:
            assert trabajador._cgroup_memory() is None
        finally:
            trabajador.logger.removeHandler(manejador)

    assert "freno de perfiles queda desactivado" in registro.getvalue()


def test_prefetch_downloads_several_blocks_at_once(monkeypatch):
    """Los bloques se adelantan de varios en varios, no en fila india.

    El nivel 2 consume un bloque en menos de tres minutos y bajar uno lleva
    cuatro. Mientras los niveles previos duraban casi una hora la precarga
    terminaba con tiempo de sobra; al acelerarlos deja de llegar.
    """
    import threading

    import scripts.forecast_worker as trabajador
    from server.services import arome_forecast

    monkeypatch.setattr(arome_forecast, "_packages_available", lambda: True)
    monkeypatch.setattr(trabajador, "PREFETCH_RETRY_S", 0)
    monkeypatch.setattr(trabajador, "PREFETCH_STREAMS", 4)

    a_la_vez = []
    corriendo = []
    cerrojo = threading.Lock()

    def descarga_lenta(paquete, run, valid_time, **kwargs):
        assert kwargs["lock_timeout_s"] == 0
        with cerrojo:
            corriendo.append(1)
            a_la_vez.append(len(corriendo))
        __import__("time").sleep(0.05)
        with cerrojo:
            corriendo.pop()
        return "ruta"

    monkeypatch.setattr(trabajador, "ensure_package", descarga_lenta)

    hilo = trabajador._start_package_prefetch(
        [_trabajo(12), _trabajo(19), _trabajo(1, dia=27)], threading.Event()
    )
    assert hilo is not None
    hilo.join(timeout=10)

    assert max(a_la_vez) == 4, f"debe usar exactamente cuatro slots: {a_la_vez}"


def test_parallel_prefetch_still_retries_what_is_not_published(monkeypatch):
    """Bajar en paralelo no rompe el reintento de los bloques que faltan."""
    import threading

    import scripts.forecast_worker as trabajador
    from server.services import arome_forecast

    monkeypatch.setattr(arome_forecast, "_packages_available", lambda: True)
    monkeypatch.setattr(trabajador, "PREFETCH_RETRY_S", 0)
    intentos = {"n": 0}
    cerrojo = threading.Lock()

    def publicado_tarde(paquete, run, valid_time, **kwargs):
        with cerrojo:
            intentos["n"] += 1
            if intentos["n"] < 4:
                raise trabajador.AromePackageError("todavía no publicado")
        return "ruta"

    monkeypatch.setattr(trabajador, "ensure_package", publicado_tarde)

    hilo = trabajador._start_package_prefetch(
        [_trabajo(12), _trabajo(19)], threading.Event()
    )
    assert hilo is not None
    hilo.join(timeout=10)

    assert not hilo.is_alive()
    assert intentos["n"] >= 4, "debe insistir hasta que aparezcan"


def test_a_finished_run_summarises_itself(caplog):
    """Al completarse, la pasada deja su cronología en una línea.

    Sin esto, saber lo que costó obliga a juntar los logs de todos los
    despliegues que la atravesaron y reconstruir los tiempos a mano.
    """
    import logging

    import scripts.forecast_worker as trabajador

    manifiesto = {
        "run": "2026-08-27T06:00:00Z",
        "tier_timing": {
            "0": {"first_start": "2026-08-27T06:10:00Z",
                  "last_start": "2026-08-27T06:40:00Z", "jobs": 306},
            "2": {"first_start": "2026-08-27T06:44:00Z",
                  "last_start": "2026-08-27T07:30:00Z", "jobs": 38},
        },
    }

    with caplog.at_level(logging.INFO, logger="meteolabx.forecast_worker"):
        trabajador._log_run_summary(manifiesto)

    texto = caplog.text
    assert "Pasada 2026-08-27T06:00:00Z completada en 80 min" in texto
    assert "nativos 306 trabajos 0-30 min" in texto
    assert "convectivos 38 trabajos 34-80 min" in texto


def test_finished_and_failed_jobs_add_their_busy_time():
    """Cada trabajo suma a su nivel lo que ocupó, termine bien o mal."""
    from datetime import datetime, timedelta, timezone

    import scripts.forecast_worker as trabajador

    manifiesto = {"run": "2026-08-27T06:00:00Z", "products": {}}
    trabajos = [
        trabajador.ForecastJob("2026-08-27T06:00:00Z", f"2026-08-27T{hora:02d}:00:00Z",
                               ("mucape",), "model", 2)
        for hora in (7, 8)
    ]
    for trabajo in trabajos:
        trabajador._mark_job_started(manifiesto, trabajo, 600, slots=3)
    # Como si hubieran arrancado hace dos minutos.
    hace = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    for entrada in manifiesto["progress"]["active_jobs"]:
        entrada["started_at"] = hace.replace("+00:00", "Z")

    trabajador._mark_job_finished(manifiesto, trabajos[0])
    trabajador._mark_job_failed(manifiesto, trabajos[1], "WCS 500")

    tramo = manifiesto["tier_timing"]["2"]
    assert tramo["slots"] == 3
    assert tramo["busy_seconds"] == pytest.approx(240.0, abs=5.0)
    assert tramo["last_end"]
    assert manifiesto["progress"]["active_jobs"] == []


def test_an_incomplete_run_says_what_it_is_waiting_for(caplog):
    """Una pasada al 100 % que no se completa bloqueaba su informe en silencio."""
    import logging

    import scripts.forecast_worker as trabajador

    horas = [f"2026-09-2{2 + ((18 + h) // 24)}T{(18 + h) % 24:02d}:00:00Z" for h in range(52)]
    manifiesto = {
        "run": "2026-09-22T18:00:00Z",
        # El catálogo llegó a +51 h, así que el horizonte no es el problema.
        "expected_times": horas,
        # Una hora que el catálogo anunció y el cálculo nunca llegó a tener:
        # así se queda una pasada que el panel marca al 100 %.
        "catalog_products": {"temperature-850": {"valid_times": horas}},
        "products": {"temperature-850": {"available_times": horas[:-1]}},
        "progress": {},
    }
    with caplog.at_level(logging.INFO, logger=trabajador.logger.name):
        trabajador._finish_status(manifiesto)
        trabajador._finish_status(manifiesto)

    assert manifiesto["status"] == "publishing"
    avisos = [r.getMessage() for r in caplog.records if "sigue en publicando" in r.getMessage()]
    assert len(avisos) == 1, "el motivo se registra una vez, no en cada ciclo"
    assert "faltan" in avisos[0]


def _complete_report_manifest():
    from datetime import datetime, timedelta, timezone
    import scripts.forecast_worker as worker

    run = datetime(2026, 8, 27, 6, tzinfo=timezone.utc)
    hours = [(run + timedelta(hours=h)).isoformat().replace("+00:00", "Z")
             for h in range(52)]
    manifest = {"run": hours[0], "expected_times": hours,
                "expected_hours": {"native": 52, "diagnostic": 36},
                "status": "publishing", "catalog_products": {}, "products": {}}
    for product in worker.PERSISTED_FORECAST_PRODUCTS:
        start = worker._first_available_hour(product)
        expected = hours[start:start + worker._expected_hours(manifest, product)]
        manifest["catalog_products"][product] = {"valid_times": list(expected)}
        manifest["products"][product] = {"available_times": list(expected)}
    return manifest


def test_the_run_report_sees_the_last_frame(monkeypatch):
    """El informe se emite con el progreso al día, no con el del ciclo anterior.

    `_finish_status` corre antes de `_persist_manifest`, que es quien refrescaba
    el progreso: el correo de cierre siempre contaba un frame de menos.
    """
    import copy

    import scripts.forecast_worker as trabajador

    vistos = []
    monkeypatch.setattr(trabajador, "_log_run_summary", lambda m: True)
    monkeypatch.setattr(
        trabajador, "_emit_run_report",
        lambda store, m: bool(vistos.append(copy.deepcopy(m["progress"]))) or True,
    )
    manifiesto = _complete_report_manifest()
    # A stale counter must be refreshed before building the report.
    manifiesto["progress"] = {"frames_available": -1, "frames_total": -1}
    trabajador._finish_status(manifiesto, store=object())

    assert manifiesto["status"] == "complete"
    fresco = trabajador._refresh_progress(copy.deepcopy(manifiesto))
    assert vistos and vistos[0]["frames_available"] == fresco["frames_available"]
    assert vistos[0]["frames_total"] == fresco["frames_total"]


def test_the_summary_only_appears_once(caplog):
    """Se registra al pasar a completa, no en cada ciclo posterior."""
    import logging

    import scripts.forecast_worker as trabajador

    veces = []
    original = trabajador._log_run_summary
    # Devuelve True como el real cuando llega a escribirlo: quien llama usa
    # ese valor para no repetirlo.
    trabajador._log_run_summary = lambda m: bool(veces.append(m) or True)
    try:
        manifiesto = _complete_report_manifest()
        trabajador._finish_status(manifiesto)
        primera = len(veces)
        trabajador._finish_status(manifiesto)
        assert len(veces) == primera == 1, "no debe repetirse en ciclos posteriores"
    finally:
        trabajador._log_run_summary = original


def test_a_run_without_timings_is_not_marked_as_summarised():
    """Sin marcas de tiempo no hay resumen, y tampoco se da por hecho.

    Una pasada empezada antes de que existieran esas marcas no tiene nada que
    resumir. Apuntarla igualmente la daba por resumida para siempre: la 12Z
    llegó al 100 % y su cronología no llegó a escribirse nunca.
    """
    import scripts.forecast_worker as trabajador

    sin_marcas = {"run": "2026-08-28T12:00:00Z"}
    assert trabajador._log_run_summary(sin_marcas) is False

    con_marcas = {
        "run": "2026-08-28T12:00:00Z",
        "tier_timing": {"0": {"first_start": "2026-08-28T14:00:00Z",
                              "last_start": "2026-08-28T14:30:00Z", "jobs": 12}},
    }
    assert trabajador._log_run_summary(con_marcas) is True


def test_the_worker_trims_its_heap_and_reports_what_it_freed(monkeypatch, caplog):
    """El padre del worker recorta su montón: la API lo hacía, él no."""
    import logging

    import scripts.forecast_worker as trabajador
    from server.services import memory_maintenance

    lecturas = iter([900 * 1024**2, 600 * 1024**2])
    monkeypatch.setattr(memory_maintenance, "anonymous_bytes", lambda: next(lecturas))
    llamadas = []
    monkeypatch.setattr(memory_maintenance, "collect_and_trim", lambda: llamadas.append(1) or (0, 1))
    with caplog.at_level(logging.INFO, logger=trabajador.logger.name):
        liberado = trabajador._trim_worker_memory()
    assert llamadas == [1]
    assert liberado == 300 * 1024**2
    assert "300 MB devueltos" in caplog.text


def test_grib_release_is_logged_only_when_it_changes(monkeypatch, caplog):
    """Cada minuto se consulta; solo se cuenta cuando cambia, y el atasco se ve."""
    import logging

    import scripts.forecast_worker as trabajador

    monkeypatch.setattr(trabajador, "_ULTIMA_LIBERACION_GRIB", None)
    with caplog.at_level(logging.INFO, logger=trabajador.logger.name):
        trabajador._report_grib_release({"skipped": "unfinished_runs"})
        trabajador._report_grib_release({"skipped": "unfinished_runs"})
        trabajador._report_grib_release({"files_advised": 3, "file_bytes": 1})
    mensajes = [r.getMessage() for r in caplog.records if "Caché de GRIB" in r.getMessage()]
    assert mensajes == [
        "Caché de GRIB: no se libera, alguna pasada conservada no está completa.",
        "Caché de GRIB: se libera tras cada ciclo.",
    ]


@pytest.mark.parametrize("product,missing", [("wind-level", 1), ("wind-level", 52),
                                            ("ship", 1), ("precip-1h", 1)])
def test_partial_catalog_does_not_close_or_send_report(monkeypatch, product, missing):
    import copy
    import scripts.forecast_worker as worker

    manifest = _complete_report_manifest()
    full_times = list(manifest["catalog_products"][product]["valid_times"])
    manifest["catalog_products"][product]["valid_times"] = full_times[:-missing]
    manifest["products"][product]["available_times"] = full_times[:-missing]
    reports, summaries = [], []
    monkeypatch.setattr(worker, "_record_downloads", lambda m: None)
    monkeypatch.setattr(worker, "_log_run_summary", lambda m: summaries.append(m["run"]) or True)
    monkeypatch.setattr(worker, "_emit_run_report",
                        lambda store, m: reports.append(copy.deepcopy(m)) or True)
    # Another product already reaches +51; the global horizon alone is insufficient.
    worker._finish_status(manifest, store=object())
    assert manifest["status"] == "publishing"
    assert "catálogo aún no anuncia" in manifest["incomplete_reason"]
    assert not reports and not summaries
    # Publication alone is not enough: the newly announced frames need calculation.
    manifest["catalog_products"][product]["valid_times"] = full_times
    worker._finish_status(manifest, store=object())
    assert manifest["status"] == "publishing"
    assert not reports
    manifest["products"][product]["available_times"] = full_times
    worker._finish_status(manifest, store=object())
    worker._finish_status(manifest, store=object())
    assert manifest["status"] == "complete"
    assert len(reports) == len(summaries) == 1
    assert reports[0]["progress"]["frames_available"] == reports[0]["progress"]["frames_total"] == 1441


def test_no_report_until_last_active_job_finishes(monkeypatch):
    import scripts.forecast_worker as worker
    manifest = _complete_report_manifest()
    manifest["progress"] = {"active_jobs": [{"id": "still-finishing"}]}
    reports = []
    monkeypatch.setattr(worker, "_log_run_summary", lambda m: True)
    monkeypatch.setattr(worker, "_emit_run_report", lambda *a: reports.append(True) or True)
    worker._finish_status(manifest, store=object())
    assert manifest["status"] == "publishing" and not reports
    manifest["progress"]["active_jobs"] = []
    worker._finish_status(manifest, store=object())
    assert manifest["status"] == "complete" and reports == [True]


def test_a_stalled_download_does_not_hold_back_the_other_prefetch_slots(monkeypatch):
    """Una descarga parada ocupa su hueco, no los otros tres.

    Con rondas, la ronda esperaba a sus cuatro descargas: el 24/09 una se quedó
    33 min y los IP3 ya publicados de los bloques siguientes no se bajaron.
    """
    import threading

    import scripts.forecast_worker as trabajador
    from server.services import arome_forecast

    monkeypatch.setattr(arome_forecast, "_packages_available", lambda: True)
    monkeypatch.setattr(trabajador, "PREFETCH_RETRY_S", 0)
    soltar = threading.Event()
    # El primer bloque de la pasada de las 12 y el siguiente.
    PRIMERO, SIGUIENTE = "2026-08-26T12:00:00+00:00", "2026-08-26T19:00:00+00:00"
    pedidos = []
    cerrojo = threading.Lock()

    def ip1_parado(paquete, run, valid_time, **kwargs):
        with cerrojo:
            pedidos.append((paquete, valid_time.isoformat()))
            intentos_ip3 = pedidos.count(("IP3", SIGUIENTE))
        if (paquete, valid_time.isoformat()) == ("IP1", PRIMERO):
            soltar.wait(5)
        elif (paquete, valid_time.isoformat()) == ("IP3", SIGUIENTE) and intentos_ip3 == 1:
            raise trabajador.AromePackageError("todavía no publicado")
        return "ruta"

    monkeypatch.setattr(trabajador, "ensure_package", ip1_parado)
    trabajos = [_trabajo(12), _trabajo(19), _trabajo(1, dia=27)]
    hilo = trabajador._start_package_prefetch(trabajos, threading.Event())
    assert hilo is not None
    for _ in range(200):
        with cerrojo:
            if pedidos.count(("IP3", SIGUIENTE)) >= 2:
                break
        __import__("time").sleep(0.01)
    # El IP3 que no estaba se reintenta mientras el IP1 sigue parado.
    assert pedidos.count(("IP3", SIGUIENTE)) == 2
    assert hilo.is_alive()
    soltar.set()
    hilo.join(timeout=5)
    assert not hilo.is_alive()


def test_growing_profiles_keep_their_reserve_until_they_show(monkeypatch):
    """Los pesados que aún crecen descuentan su reserva; no hace falta esperar 15 s."""
    import scripts.forecast_worker as trabajador

    GB = 1024**3
    monkeypatch.setattr(trabajador, "HEAVY_PROFILE_BYTES", 4 * GB)
    monkeypatch.setattr(trabajador, "_cgroup_memory", lambda: (6 * GB, 30 * GB))
    # 24 GB libres: cabe otro además de cinco que crecen (6 × 4 = 24).
    assert trabajador._room_for_profiles(5)
    assert not trabajador._room_for_profiles(6)
    assert trabajador._growing_profiles([0.0, 10.0, 14.0], now=20.0) == 2
    # Sin cgroup legible se conserva la espera: nada mientras otro crece.
    monkeypatch.setattr(trabajador, "_cgroup_memory", lambda: None)
    assert trabajador._room_for_profiles(0)
    assert not trabajador._room_for_profiles(1)


def test_the_worker_says_what_it_has_to_work_with(monkeypatch, caplog, tmp_path):
    """Subir `--workers` a ciegas reparte la misma CPU en vez de acelerar.

    Un perfil convectivo ocupa tres hilos mientras diagnostica, así que lo que
    decide si otro worker sirve de algo es la cuota del contenedor, no las CPU
    del host que cuenta `os.cpu_count()`.
    """
    import argparse
    import logging
    import scripts.forecast_worker as worker

    monkeypatch.setattr(worker, '_cpu_quota', lambda: 4.0)  # cuota de cuatro CPU
    monkeypatch.setattr(worker, '_cgroup_memory', lambda: (0, 32 * 1024**3))
    args = argparse.Namespace(workers=7)

    with caplog.at_level(logging.INFO):
        linea = worker.log_worker_resources(args)

    assert '7 workers × 3 hilos por perfil' in linea
    assert 'CPU del contenedor: 4.0' in linea
    assert 'hilos si todos calculan un perfil: 21' in linea
    assert '32.0 GB de límite' in linea
    assert 'Recursos del worker' in caplog.text


def test_the_cpu_quota_comes_from_the_cgroup_not_the_host(tmp_path, monkeypatch):
    import scripts.forecast_worker as worker

    archivo = tmp_path / 'cpu.max'
    monkeypatch.setattr(worker, 'Path', lambda _: archivo)
    archivo.write_text('250000 100000\n')
    assert worker._cpu_quota() == 2.5
    archivo.write_text('max 100000\n')
    assert worker._cpu_quota() is None
    archivo.unlink()
    assert worker._cpu_quota() is None
