import numpy as np
import pytest

from server.services.convective_diagnostics import (
    KAPPA,
    _level_of_free_convection,
    _mixed_layer_parcel_properties,
    _saturated_temperature_from_theta_e,
    _saturated_theta_e_k,
    dewpoint_from_mixing_ratio_k,
    downdraft_cape,
    effective_bulk_wind_difference,
    mixing_ratio_kgkg,
    pressure_weighted_layer_mean,
    significant_hail_parameter,
    significant_hail_parameter_sharppy,
)


def test_saturated_temperature_inverts_theta_e_on_every_point():
    """Newton itera solo los puntos pendientes; debe invertir todos igual.

    El bucle abandona cada punto en cuanto converge, así que conviene fijar que
    ninguno se quede a medio resolver por haberlo apartado antes de tiempo.
    """
    rng = np.random.default_rng(11)
    pressure = rng.uniform(150.0, 1_000.0, (12, 40, 40))
    temperature = rng.uniform(215.0, 305.0, (12, 40, 40))
    target = _saturated_theta_e_k(pressure, temperature)

    recovered = _saturated_temperature_from_theta_e(
        pressure, target, temperature + rng.uniform(-8.0, 8.0, temperature.shape)
    )

    assert np.isfinite(recovered).all()
    # Se comprueba sobre theta-e, que es la magnitud que el método invierte.
    assert np.allclose(_saturated_theta_e_k(pressure, recovered), target, rtol=1e-6)


def test_effective_bulk_wind_difference_uses_half_storm_depth():
    heights = np.asarray([0.0, 2_000.0, 4_000.0, 6_000.0, 8_000.0])[:, None, None]
    u = (heights / 1_000.0) * 2.0
    v = np.zeros_like(u)

    magnitude, delta_u, delta_v = effective_bulk_wind_difference(
        heights,
        u,
        v,
        np.asarray([[2_000.0]]),
        np.asarray([[10_000.0]]),
    )

    # Base 2 km; 50 % de la profundidad hasta EL 10 km -> techo 6 km.
    assert magnitude.item() == 8.0
    assert delta_u.item() == 8.0
    assert delta_v.item() == 0.0


def test_pressure_weighted_layer_mean_honors_lcl_and_el_boundaries():
    pressure = np.asarray([1000.0, 800.0, 600.0, 400.0])[:, None, None]
    # Campo lineal con la presión: la media exacta entre 900 y 500 hPa es 7.
    values = (pressure / 100.0)

    result = pressure_weighted_layer_mean(
        pressure,
        values,
        np.asarray([[900.0]]),
        np.asarray([[500.0]]),
    )

    assert result.item() == 7.0


def test_mixed_layer_average_weights_uneven_pressure_levels():
    """ML100 es una integral en presión, no una media de niveles discretos."""
    pressure = np.asarray([1000.0, 990.0, 930.0, 900.0])[:, None, None]
    theta = np.asarray([300.0, 310.0, 330.0, 340.0])[:, None, None]
    temperature = theta * np.power(pressure / 1000.0, KAPPA)
    ratio = np.full_like(pressure, 0.01)
    dewpoint = dewpoint_from_mixing_ratio_k(pressure, ratio)

    mixed_temperature, mixed_dewpoint = _mixed_layer_parcel_properties(
        pressure, temperature, dewpoint
    )

    # Integral trapezoidal exacta: (305*10 + 320*60 + 335*30) / 100.
    assert mixed_temperature.item() == pytest.approx(323.0)
    assert mixed_temperature.item() != pytest.approx(theta.mean())
    assert mixed_dewpoint.item() == pytest.approx(
        dewpoint_from_mixing_ratio_k(np.asarray([[1000.0]]), np.asarray([[0.01]])).item()
    )


def test_lfc_is_interpolated_at_the_buoyancy_zero_crossing():
    pressure = np.asarray([1000.0, 900.0, 800.0, 700.0])[:, None, None]
    height = np.asarray([0.0, 1000.0, 2000.0, 3000.0])[:, None, None]
    buoyancy = np.asarray([-1.0, -0.5, 0.5, 1.0])[:, None, None]

    lfc_height, lfc_pressure = _level_of_free_convection(
        pressure, height, buoyancy, np.asarray([[950.0]])
    )

    assert lfc_height.item() == pytest.approx(1500.0)
    # La presión se interpola logarítmicamente entre 900 y 800 hPa.
    assert lfc_pressure.item() == pytest.approx(np.sqrt(900.0 * 800.0))


def test_ship_matches_sharppy_spc_base_equation():
    result = significant_hail_parameter(
        np.asarray([[2_000.0]]),
        np.asarray([[12.0]]),
        np.asarray([[7.0]]),
        np.asarray([[-15.0]]),
        np.asarray([[20.0]]),
        np.asarray([[3_000.0]]),
    )

    assert result.item() == 1.2


def test_ship_sharppy_adapter_matches_vectorized_formula():
    arguments = (
        np.asarray([[2_000.0]]),
        np.asarray([[12.0]]),
        np.asarray([[7.0]]),
        np.asarray([[-15.0]]),
        np.asarray([[20.0]]),
        np.asarray([[3_000.0]]),
    )

    assert significant_hail_parameter_sharppy(*arguments).item() == pytest.approx(
        significant_hail_parameter(*arguments).item(),
        # thermo.temp_at_mixrat/mixratio de SHARPpy no son inversas exactas;
        # la discrepancia esperada ronda el 0,7 % para 12 g/kg.
        rel=0.01,
    )


def test_ship_applies_low_cape_lapse_rate_and_freezing_reductions():
    result = significant_hail_parameter(
        np.asarray([[650.0]]),
        np.asarray([[12.0]]),
        np.asarray([[2.9]]),
        np.asarray([[-15.0]]),
        np.asarray([[20.0]]),
        np.asarray([[1_200.0]]),
    )
    base = 650.0 * 12.0 * 2.9 * 15.0 * 20.0 / 42_000_000.0

    assert result.item() == base * (650.0 / 1_300.0) * (2.9 / 5.8) * (1_200.0 / 2_400.0)


def test_dewpoint_and_mixing_ratio_are_inverse_operations():
    pressure = np.asarray([[950.0]])
    dewpoint = np.asarray([[288.15]])

    recovered = dewpoint_from_mixing_ratio_k(
        pressure,
        mixing_ratio_kgkg(pressure, dewpoint),
    )

    assert np.allclose(recovered, dewpoint, atol=0.02)


def test_dcape_is_nonnegative_for_a_dry_midlevel_profile():
    pressure = np.asarray([1000.0, 850.0, 700.0, 500.0])[:, None, None]
    temperature = np.asarray([303.0, 292.0, 280.0, 258.0])[:, None, None]
    dewpoint = np.asarray([294.0, 280.0, 258.0, 238.0])[:, None, None]
    height = np.asarray([0.0, 1_500.0, 3_100.0, 5_700.0])[:, None, None]

    result = downdraft_cape(pressure, temperature, dewpoint, height)

    assert np.isfinite(result.item())
    assert result.item() >= 0.0


def test_vectorized_dcape_tracks_sharppy_params_reference():
    sharppy_profile = pytest.importorskip("sharppy.sharptab.profile")
    sharppy_params = pytest.importorskip("sharppy.sharptab.params")
    pressure_1d = np.asarray([1000.0, 850.0, 700.0, 500.0])
    temperature_k_1d = np.asarray([303.0, 292.0, 280.0, 258.0])
    dewpoint_k_1d = np.asarray([294.0, 280.0, 258.0, 238.0])
    height_1d = np.asarray([0.0, 1_500.0, 3_100.0, 5_700.0])
    profile = sharppy_profile.create_profile(
        profile="default",
        pres=pressure_1d,
        hght=height_1d,
        tmpc=temperature_k_1d - 273.15,
        dwpc=dewpoint_k_1d - 273.15,
        wspd=np.zeros(pressure_1d.size),
        wdir=np.zeros(pressure_1d.size),
        missing=-9999,
        strictQC=False,
    )
    expected = float(sharppy_params.dcape(profile)[0])

    actual = downdraft_cape(
        pressure_1d[:, None, None],
        temperature_k_1d[:, None, None],
        dewpoint_k_1d[:, None, None],
        height_1d[:, None, None],
    ).item()

    assert actual == pytest.approx(expected, rel=0.002)


def _synthetic_profile(rows, cols, seed=13):
    levels = [1000., 950., 925., 900., 850., 800., 750., 700., 650., 600.,
              550., 500., 450., 400., 350., 300., 275., 250., 225., 200.,
              175., 150., 125., 100.]
    rng = np.random.default_rng(seed)
    surface = np.full((rows, cols), 1008.0) + rng.normal(0, 5, (rows, cols))
    pressure = np.empty((len(levels) + 1, rows, cols))
    pressure[0] = surface
    for index, level in enumerate(levels):
        pressure[index + 1] = np.minimum(level, surface)
    temperature = np.empty_like(pressure)
    temperature[0] = 299 + rng.normal(0, 3, (rows, cols))
    for index, level in enumerate(levels):
        temperature[index + 1] = temperature[0] - 6.5 * (
            np.log(surface / np.maximum(level, 1)) * 7.29
        )
    dewpoint = temperature - np.clip(rng.normal(5, 3, pressure.shape), 0.5, 32)
    u = rng.normal(0, 12, pressure.shape)
    v = rng.normal(0, 12, pressure.shape)
    terrain = np.abs(rng.normal(200, 150, (rows, cols)))
    return (pressure, temperature, dewpoint, u, v, terrain,
            u[0].copy(), v[0].copy(), levels)


def test_striped_convective_outputs_match_the_whole_grid():
    """Trocear por bandas de filas no cambia el diagnóstico.

    El pico de memoria del perfil obliga a procesar la rejilla por bandas; cada
    celda es independiente de sus vecinas, así que el resultado debe coincidir.
    DCAPE queda fuera: su selección de capa de origen a través de SHARPpy es
    sensible al conjunto de celdas y se comprueba aparte.
    """
    from server.services.arome_forecast import (
        _convective_outputs,
        _convective_outputs_in_stripes,
    )

    arguments = _synthetic_profile(48, 24)
    whole = _convective_outputs(*arguments)
    striped = _convective_outputs_in_stripes(*arguments, stripe_rows=16)

    assert set(whole) == set(striped)
    for name, expected in whole.items():
        if name == "dcape":
            continue
        actual = striped[name]
        assert actual.shape == expected.shape, name
        assert (np.isfinite(actual) == np.isfinite(expected)).all(), name
        finite = np.isfinite(expected)
        assert np.array_equal(actual[finite], expected[finite]), name


def test_striping_is_disabled_when_the_band_covers_the_grid():
    """Una banda igual o mayor que la rejilla no debe trocear nada."""
    from server.services.arome_forecast import (
        _convective_outputs,
        _convective_outputs_in_stripes,
    )

    arguments = _synthetic_profile(24, 16)
    whole = _convective_outputs(*arguments)
    for stripe_rows in (0, 24, 500):
        same = _convective_outputs_in_stripes(*arguments, stripe_rows=stripe_rows)
        for name, expected in whole.items():
            finite = np.isfinite(expected)
            assert np.array_equal(same[name][finite], expected[finite]), (name, stripe_rows)


def test_dcape_can_be_skipped_without_touching_the_other_diagnostics():
    """DCAPE se puede omitir para no retrasar a los otros trece.

    Es el diagnóstico más caro y el único que exige el punto de rocío exacto
    del modelo, así que se calcula aparte. Omitirlo no debe alterar nada más.
    """
    from server.services.arome_forecast import _convective_outputs_in_stripes

    arguments = _synthetic_profile(32, 20)
    con = _convective_outputs_in_stripes(*arguments, stripe_rows=16, include_dcape=True)
    sin = _convective_outputs_in_stripes(*arguments, stripe_rows=16, include_dcape=False)

    assert np.isfinite(con["dcape"]).any(), "con el interruptor activo debe calcularse"
    assert not np.isfinite(sin["dcape"]).any(), "omitido debe quedar sin valores"
    for nombre in con:
        if nombre == "dcape":
            continue
        finitos = np.isfinite(con[nombre])
        assert (np.isfinite(sin[nombre]) == finitos).all(), nombre
        assert np.array_equal(sin[nombre][finitos], con[nombre][finitos]), nombre


def test_derived_dewpoint_matches_the_published_one():
    """El rocío derivado de T y humedad reproduce el que publica el modelo.

    Medido contra AROME: fuera del aire ultraseco el error es de milésimas de
    grado, muy por debajo de lo que distingue cualquier diagnóstico.
    """
    from server.services.arome_forecast import _dewpoint_from_relative_humidity_c

    temperatura = np.array([[25.0, 10.0, -5.0, 30.0]])
    # Rocío de referencia y la humedad relativa que le corresponde.
    rocio = np.array([[18.0, 4.0, -12.0, 12.0]])
    es_t = 6.112 * np.exp(17.67 * temperatura / (temperatura + 243.5))
    es_td = 6.112 * np.exp(17.67 * rocio / (rocio + 243.5))
    humedad = es_td / es_t * 100.0

    recuperado = _dewpoint_from_relative_humidity_c(temperatura, humedad)

    assert np.allclose(recuperado, rocio, atol=1e-6)


def test_narrow_bands_are_reserved_for_the_run_without_dcape():
    """Sin DCAPE la banda por defecto se estrecha, y el resultado no cambia.

    DCAPE es el único diagnóstico sensible a cómo se particione la rejilla, así
    que cuando se calcula aparte los otros trece pueden ir en bandas mucho más
    estrechas y recortar el pico de memoria. Lo que no puede cambiar es el
    resultado: se comprueba contra la rejilla entera, sin trocear.
    """
    from server.services import arome_forecast
    from server.services.arome_forecast import (
        _convective_outputs,
        _convective_outputs_in_stripes,
    )

    assert (
        arome_forecast.CONVECTIVE_STRIPE_ROWS_WITHOUT_DCAPE
        < arome_forecast.CONVECTIVE_STRIPE_ROWS
    ), "la banda sin DCAPE existe para ser más estrecha"

    argumentos = _synthetic_profile(96, 40)
    entera = _convective_outputs(*argumentos, include_dcape=False)
    # Sin stripe_rows explícito: usa la banda estrecha por omisión.
    troceada = _convective_outputs_in_stripes(*argumentos, include_dcape=False)

    for nombre, esperado in entera.items():
        if nombre == "dcape":
            continue
        obtenido = troceada[nombre]
        finitos = np.isfinite(esperado)
        assert (np.isfinite(obtenido) == finitos).all(), nombre
        assert np.array_equal(obtenido[finitos], esperado[finitos]), nombre


def test_spilling_the_profiles_to_disk_does_not_change_the_result(tmp_path, monkeypatch):
    """Leer los perfiles desde un fichero mapeado da lo mismo que desde memoria.

    Apartarlos al disco existe para que ese giga sea caché recuperable en vez de
    memoria anónima, no para cambiar nada del diagnóstico.
    """
    from server.services import arome_forecast
    from server.services.arome_forecast import (
        _convective_outputs_in_stripes,
        _profiles_spilled_to_disk,
    )

    monkeypatch.setattr(arome_forecast, "PROFILE_SPILL_ENABLED", True)
    monkeypatch.setattr(arome_forecast, "_is_memory_backed", lambda path: False)
    monkeypatch.setattr(arome_forecast.tempfile, "gettempdir", lambda: str(tmp_path))

    pressure, temperature, dewpoint, u, v, terrain, su, sv, levels = _synthetic_profile(
        64, 32
    )
    en_memoria = _convective_outputs_in_stripes(
        pressure, temperature, dewpoint, u, v, terrain, su, sv, levels,
        stripe_rows=16, include_dcape=False,
    )

    apilados = [pressure, temperature, dewpoint, u, v]
    with _profiles_spilled_to_disk(apilados) as perfiles:
        assert not apilados, "la lista original debe quedar vacía"
        assert all(isinstance(p, np.memmap) for p in perfiles)
        en_disco = _convective_outputs_in_stripes(
            *perfiles, terrain, su, sv, levels, stripe_rows=16, include_dcape=False,
        )

    assert not list(tmp_path.glob("meteolabx-perfil-*")), "los ficheros deben borrarse"
    for nombre, esperado in en_memoria.items():
        if nombre == "dcape":
            continue
        finitos = np.isfinite(esperado)
        assert (np.isfinite(en_disco[nombre]) == finitos).all(), nombre
        assert np.array_equal(en_disco[nombre][finitos], esperado[finitos]), nombre


def test_profiles_stay_in_memory_when_the_temporary_lives_in_ram(tmp_path, monkeypatch):
    """Sobre un tmpfs no se vuelca: los bytes seguirían en memoria igualmente."""
    from server.services import arome_forecast
    from server.services.arome_forecast import _profiles_spilled_to_disk

    monkeypatch.setattr(arome_forecast, "PROFILE_SPILL_ENABLED", True)
    monkeypatch.setattr(arome_forecast, "_is_memory_backed", lambda path: True)
    monkeypatch.setattr(arome_forecast.tempfile, "gettempdir", lambda: str(tmp_path))

    original = [np.zeros((2, 4, 4)), np.ones((2, 4, 4))]
    with _profiles_spilled_to_disk(original) as perfiles:
        assert perfiles is original
        assert not any(isinstance(p, np.memmap) for p in perfiles)
    assert not list(tmp_path.iterdir())


def test_railway_mount_layout_is_recognised_as_real_disk(monkeypatch):
    """En Railway /tmp cuelga de un overlay y hereda el montaje de la raíz.

    La distinción decide si los perfiles se apartan al disco o no, y errar hacia
    "está en RAM" desactivaría la optimización en silencio. /dev/shm sí es RAM y
    debe seguir detectándose como tal.
    """
    from pathlib import Path
    from server.services.arome_forecast import _is_memory_backed

    montajes = (
        "overlay / overlay rw,relatime,lowerdir=/var/lib/docker/overlay2/l/ABC 0 0\n"
        "proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0\n"
        "tmpfs /dev tmpfs rw,nosuid,size=65536k,mode=755 0 0\n"
        "shm /dev/shm tmpfs rw,nosuid,nodev,noexec,relatime,size=65536k 0 0\n"
        "/dev/sda1 /app/data ext4 rw,relatime 0 0\n"
    )
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/proc/mounts")
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: montajes)
    monkeypatch.setattr(Path, "resolve", lambda self: self)

    assert _is_memory_backed(Path("/tmp")) is False
    assert _is_memory_backed(Path("/app/data")) is False
    assert _is_memory_backed(Path("/dev/shm")) is True


def test_dcape_can_be_computed_without_asking_the_wcs_for_dewpoint():
    """Calcular DCAPE y pedir el rocío exacto son decisiones separadas.

    DCAPE era el único que pedía el rocío isobárico al WCS: 24 peticiones por
    hora, 864 por pasada, más que todo el resto junto. Medido contra el modelo
    sobre la misma pasada y hora, el rocío derivado de la humedad del paquete
    se desvía 0,006 K y mueve el DCAPE un 0,18 %, así que tiene que poder
    calcularse sin esas peticiones.
    """
    import inspect

    from server.services import arome_forecast

    firma = inspect.signature(arome_forecast._convective_frames.__wrapped__)
    assert "include_dcape" in firma.parameters, (
        "sin separarlo, quitar el rocío del WCS apagaba también el cálculo"
    )
    # Por omisión siguen acoplados, que es el comportamiento de siempre.
    assert firma.parameters["include_dcape"].default is None


def test_parcel_reports_the_level_of_free_convection():
    """El LFC es la base de la capa flotante, y el EL su techo.

    Hace falta expuesto para poder mirar qué está haciendo el aire justo ahí:
    un ascenso que llega al LFC dispara la convección, y uno que se queda por
    debajo se embotella bajo la inversión.
    """
    from server.services.convective_diagnostics import parcel_diagnostics

    # Perfil con inversión hasta 850 y flotabilidad entre 800 y 500.
    pressure = np.asarray([1000., 900., 850., 800., 700., 600., 500., 400.])[:, None, None]
    height = np.asarray([100., 1000., 1500., 2000., 3000., 4200., 5600., 7200.])[:, None, None]
    # Entorno cálido abajo (frena) y frío arriba (deja subir).
    temperature = np.asarray([300., 294., 292., 285., 276., 266., 253., 236.])[:, None, None]
    dewpoint = temperature - np.asarray([2., 3., 4., 6., 10., 14., 18., 24.])[:, None, None]

    # La parcela sale de superficie, con su propio rocío.
    resultado = parcel_diagnostics(
        pressure, temperature, dewpoint, height,
        pressure[0], temperature[0], dewpoint[0],
    )

    assert np.isfinite(resultado.lfc_pressure_hpa).all()
    assert np.isfinite(resultado.lfc_height_m).all()
    # El LFC va por debajo del EL: más presión y menos altura.
    assert resultado.lfc_pressure_hpa.item() > resultado.equilibrium_pressure_hpa.item()
    assert resultado.lfc_height_m.item() < resultado.equilibrium_height_m.item()


def test_a_profile_without_buoyancy_has_no_lfc():
    """Sin capa flotante no hay nivel de convección libre que informar."""
    from server.services.convective_diagnostics import parcel_diagnostics

    pressure = np.asarray([1000., 900., 800., 700.])[:, None, None]
    height = np.asarray([0., 1000., 2000., 3100.])[:, None, None]
    # Isotermo muy seco: la parcela nunca gana flotabilidad.
    temperature = np.full((4, 1, 1), 300.0)
    dewpoint = temperature - 40.0

    resultado = parcel_diagnostics(
        pressure, temperature, dewpoint, height,
        pressure[0], temperature[0], dewpoint[0],
    )

    assert np.isnan(resultado.lfc_pressure_hpa).all()
    assert np.isnan(resultado.lfc_height_m).all()


def test_dcape_survives_the_band_it_runs_with():
    """La banda de DCAPE da el mismo resultado que una más ancha.

    Su selección de capa de origen a través de SHARPpy depende de cómo se
    particione la rejilla, así que estrecharla no es gratis por definición:
    hay que comprobarlo. A 128 filas coincide con 192 y ahorra 435 MB por
    perfil, que es lo que aprieta cuando corren varios a la vez.
    """
    from server.services import arome_forecast
    from server.services.arome_forecast import _convective_outputs_in_stripes

    assert arome_forecast.CONVECTIVE_STRIPE_ROWS >= 120, (
        "por debajo de ~120 filas el DCAPE deja de ser reproducible"
    )

    argumentos = _synthetic_profile(256, 60)
    estrecha = _convective_outputs_in_stripes(
        *argumentos, stripe_rows=arome_forecast.CONVECTIVE_STRIPE_ROWS,
        include_dcape=True,
    )
    ancha = _convective_outputs_in_stripes(*argumentos, stripe_rows=192, include_dcape=True)

    finitos = np.isfinite(ancha["dcape"])
    assert (np.isfinite(estrecha["dcape"]) == finitos).all()
    assert np.array_equal(estrecha["dcape"][finitos], ancha["dcape"][finitos])


def test_only_dcape_gives_the_same_dcape_without_the_parcels():
    """Pedir sólo DCAPE devuelve el mismo campo, sin rehacer las parcelas.

    DCAPE va en su propio nivel, detrás de los otros trece, y por el camino se
    recalculaban MU, ML y SB —que ese nivel anterior ya había hecho—. Medido
    sobre 192x1121: 14,8 s de parcelas para llegar a un DCAPE que cuesta 12,3.
    """
    from server.services.arome_forecast import _convective_outputs

    argumentos = _synthetic_profile(48, 30)
    completo = _convective_outputs(*argumentos, include_dcape=True)
    suelto = _convective_outputs(*argumentos, include_dcape=True, only_dcape=True)

    finitos = np.isfinite(completo["dcape"])
    assert (np.isfinite(suelto["dcape"]) == finitos).all()
    assert np.array_equal(suelto["dcape"][finitos], completo["dcape"][finitos])

    # Lo demás llega vacío: quien pide sólo DCAPE no lo mira.
    for nombre in ("mucape", "mlcape", "sbcape", "ship", "ebwd", "cell_speed"):
        assert not np.isfinite(suelto[nombre]).any(), nombre


def test_only_dcape_survives_the_striping():
    """El troceado por bandas no altera el DCAPE calculado a solas."""
    from server.services.arome_forecast import _convective_outputs_in_stripes

    argumentos = _synthetic_profile(256, 30)
    entero = _convective_outputs_in_stripes(
        *argumentos, stripe_rows=0, include_dcape=True, only_dcape=True
    )
    troceado = _convective_outputs_in_stripes(
        *argumentos, stripe_rows=128, include_dcape=True, only_dcape=True
    )

    finitos = np.isfinite(entero["dcape"])
    assert np.array_equal(troceado["dcape"][finitos], entero["dcape"][finitos])


def test_storm_relative_helicity_matches_metpy():
    """La helicidad coincide con MetPy sobre el mismo movimiento de tormenta.

    Es la referencia del cálculo, así que conviene comprobarlo y no solo el
    signo: una fórmula con los índices cruzados da valores plausibles y de
    signo correcto, pero equivocados.
    """
    metpy_calc = pytest.importorskip("metpy.calc")
    from metpy.units import units

    from server.services.convective_diagnostics import storm_relative_helicity

    z = np.array([0., 250., 500., 1000., 2000., 3000., 4000., 5000., 5750., 6000.])
    angulos = np.radians([180., 200., 220., 250., 270., 280., 285., 290., 295., 300.])
    modulo = np.array([5., 8., 10., 14., 18., 21., 24., 27., 29., 30.])
    u, v = -modulo * np.sin(angulos), -modulo * np.cos(angulos)
    cu, cv = 12.95, -7.50

    for techo in (1_000.0, 3_000.0):
        _, _, referencia = metpy_calc.storm_relative_helicity(
            z * units.m, u * units("m/s"), v * units("m/s"),
            depth=techo * units.m,
            storm_u=cu * units("m/s"), storm_v=cv * units("m/s"),
        )
        mio = storm_relative_helicity(
            z[:, None, None], u[:, None, None], v[:, None, None],
            np.array([[cu]]), np.array([[cv]]), techo,
        ).item()
        assert mio == pytest.approx(referencia.m, abs=0.05), techo


def test_veering_gives_positive_helicity_and_backing_negative():
    """El signo: giro a derechas positivo, a izquierdas negativo.

    Es el error clásico de esta fórmula y no se ve en los valores, que salen
    plausibles con el signo cambiado.
    """
    from server.services.convective_diagnostics import storm_relative_helicity

    z = np.array([0., 250., 500., 1000., 2000., 3000.])[:, None, None]
    angulos = np.radians([180., 200., 220., 250., 280., 300.])
    modulo = np.array([5., 8., 10., 14., 18., 22.])
    u = (-modulo * np.sin(angulos))[:, None, None]
    v = (-modulo * np.cos(angulos))[:, None, None]
    cero = np.zeros((1, 1))

    derechas = storm_relative_helicity(z, u, v, cero, cero, 3_000.0).item()
    # Espejar el hodógrafo en el eje este-oeste invierte el sentido del giro.
    izquierdas = storm_relative_helicity(z, -u, v, cero, cero, 3_000.0).item()

    assert derechas > 0, "el giro a derechas da helicidad positiva"
    assert izquierdas < 0, "el giro a izquierdas, negativa"


def test_bunkers_needs_the_whole_six_kilometres():
    """Sin la capa 0-6 km no hay movimiento que calcular."""
    from server.services.convective_diagnostics import bunkers_right_motion

    z = np.array([0., 500., 1000., 2000.])[:, None, None]
    u = np.array([5., 8., 12., 16.])[:, None, None]
    v = np.zeros_like(u)

    cu, cv = bunkers_right_motion(z, u, v)

    assert np.isnan(cu).all() and np.isnan(cv).all()


def test_bunkers_deviates_to_the_right_of_the_shear():
    """La desviación son 7,5 m/s perpendiculares a la cizalladura, a derechas.

    Con cizalladura puramente del oeste, el desvío tiene que ir hacia el sur:
    es lo que separa al right mover del viento medio.
    """
    from server.services.convective_diagnostics import (
        DEVIATION_MS, bunkers_right_motion,
    )

    z = np.array([0., 250., 500., 3000., 5500., 5750., 6000.])[:, None, None]
    # Sólo componente u, creciente con la altura: cizalladura del oeste.
    u = np.array([2., 4., 6., 16., 26., 27., 28.])[:, None, None]
    v = np.zeros_like(u)

    cu, cv = bunkers_right_motion(z, u, v)

    assert cv.item() == pytest.approx(-DEVIATION_MS, abs=0.01), (
        "con cizalladura del oeste el desvío va al sur"
    )
    assert cu.item() > 0


def test_vertical_velocity_at_the_lfc_needs_the_profile():
    """Sin perfil de velocidad vertical el campo llega vacío, no a cero.

    Un cero diría «no hay ascenso», que es una afirmación meteorológica; la
    ausencia del dato no lo es.
    """
    from server.services.arome_forecast import _convective_outputs

    argumentos = _synthetic_profile(48, 30)
    sin_dato = _convective_outputs(*argumentos, include_dcape=False)

    assert not np.isfinite(sin_dato["vv_lfc"]).any()


def test_vertical_velocity_is_read_at_the_free_convection_level():
    """Se interpola al NCL de la parcela de capa mezclada, no a un nivel fijo.

    Ahí está el sentido del mapa: un ascenso que alcanza ese nivel dispara la
    convección, y uno que se queda por debajo se embotella bajo la inversión.
    """
    from server.services.arome_forecast import _convective_outputs

    argumentos = _synthetic_profile(48, 30)
    presion = argumentos[0]
    # Velocidad vertical que crece con la altura: el valor en el NCL depende
    # de a qué altura esté, así que no puede salir constante.
    perfil_vv = np.linspace(0.0, 2.0, presion.shape[0])[:, None, None] * np.ones_like(presion)

    salida = _convective_outputs(
        *argumentos, include_dcape=False, vertical_velocity=perfil_vv
    )

    con_valor = np.isfinite(salida["vv_lfc"])
    assert con_valor.any()
    # Sólo donde hay capa flotante que alcanzar.
    assert np.array_equal(con_valor, np.isfinite(salida["ml_lfc_height"]))
    assert salida["vv_lfc"][con_valor].std() > 0, "no puede ser un nivel fijo"


def test_lfc_and_vertical_velocity_use_height_above_ground():
    """El relieve no puede sumarse dos veces al buscar el NCL en el perfil."""
    from server.services.arome_forecast import _convective_outputs

    argumentos = list(_synthetic_profile(24, 18))
    presion = argumentos[0]
    terreno = argumentos[5]
    perfil_vv = (
        np.linspace(0.0, 4.0, presion.shape[0])[:, None, None]
        * np.ones_like(presion)
    )

    con_relieve = _convective_outputs(
        *argumentos, include_dcape=False, vertical_velocity=perfil_vv
    )
    argumentos[5] = np.zeros_like(terreno)
    a_nivel_del_mar = _convective_outputs(
        *argumentos, include_dcape=False, vertical_velocity=perfil_vv
    )

    assert np.allclose(
        con_relieve["ml_lfc_height"],
        a_nivel_del_mar["ml_lfc_height"],
        equal_nan=True,
    )
    assert np.allclose(
        con_relieve["vv_lfc"],
        a_nivel_del_mar["vv_lfc"],
        equal_nan=True,
    )


def test_adding_vertical_velocity_changes_nothing_else():
    """El campo nuevo no altera ninguno de los diagnósticos que ya había."""
    from server.services.arome_forecast import _convective_outputs

    argumentos = _synthetic_profile(48, 30)
    presion = argumentos[0]
    perfil_vv = np.linspace(0.0, 2.0, presion.shape[0])[:, None, None] * np.ones_like(presion)

    sin_dato = _convective_outputs(*argumentos, include_dcape=False)
    con_dato = _convective_outputs(
        *argumentos, include_dcape=False, vertical_velocity=perfil_vv
    )

    for nombre in sin_dato:
        if nombre == "vv_lfc":
            continue
        assert np.array_equal(
            np.nan_to_num(sin_dato[nombre]), np.nan_to_num(con_dato[nombre])
        ), nombre


def _perfil_con_vorticidad(filas=96, columnas=60):
    """Perfil sintético con un vórtice y ascenso, para la helicidad."""
    p, t, td, u, v, terr, su, sv, niveles = _synthetic_profile(filas, columnas)
    Y, X = np.meshgrid(
        np.arange(filas) - filas / 2, np.arange(columnas) - columnas / 2,
        indexing="ij",
    )
    radio = np.hypot(X, Y) + 1e-9
    giro = np.exp(-radio / 12) * 20.0
    u[:] = (-Y / radio * giro)[None, ...]
    v[:] = (X / radio * giro)[None, ...]
    w = np.full(p.shape, 4.0)
    latitudes = np.linspace(43.0, 42.0, filas)
    # Como la rejilla de verdad: las filas bajan de norte a sur, así que el
    # paso latitudinal es negativo.
    return (p, t, td, u, v, terr, w, (latitudes, 0.025, -0.025))


def test_striped_updraft_helicity_matches_the_whole_grid():
    """Trocear no puede cambiar la helicidad ni dejar costuras.

    La vorticidad se deriva en el plano, así que cada banda lee una fila de más
    por arriba y otra por abajo. Sin ese halo, las filas de unión se derivarían
    contra el vacío y el mapa saldría rayado.

    Las únicas diferencias admisibles están en el borde exterior del dominio,
    donde no hay vecina que leer y np.gradient usa diferencias de un solo lado.
    """
    from server.services.arome_forecast import _updraft_helicity_in_stripes

    p, t, td, u, v, terr, w, rejilla = _perfil_con_vorticidad()

    entero = _updraft_helicity_in_stripes(p, t, td, u, v, terr, w, rejilla, stripe_rows=0)
    troceado = _updraft_helicity_in_stripes(p, t, td, u, v, terr, w, rejilla, stripe_rows=24)

    # El interior tiene que coincidir exactamente, uniones incluidas.
    interior = slice(1, -1)
    assert np.allclose(
        troceado[interior], entero[interior], equal_nan=True
    ), "el troceado altera el interior del dominio"
    # Y en particular las filas donde se unen las bandas.
    for union in (24, 48, 72):
        assert np.allclose(
            troceado[union - 1 : union + 1], entero[union - 1 : union + 1], equal_nan=True
        ), f"costura en la fila {union}"


def test_updraft_helicity_has_no_seams_between_bands():
    """En las uniones no aparece ningún salto que el campo no tuviera ya.

    Medir el salto contra el resto del mapa no vale: un vórtice puede caer
    justo en una unión y su gradiente real dispararía la comparación. Lo que
    delata una costura es que el escalón sea mayor troceando que sin trocear.
    """
    from server.services.arome_forecast import _updraft_helicity_in_stripes

    p, t, td, u, v, terr, w, rejilla = _perfil_con_vorticidad()
    troceado = _updraft_helicity_in_stripes(
        p, t, td, u, v, terr, w, rejilla, stripe_rows=24
    )
    entero = _updraft_helicity_in_stripes(
        p, t, td, u, v, terr, w, rejilla, stripe_rows=0
    )

    saltos_troceado = np.abs(np.diff(troceado, axis=0))
    saltos_entero = np.abs(np.diff(entero, axis=0))
    for union in (24, 48, 72):
        troceando = np.nanmax(saltos_troceado[union - 1])
        sin_trocear = np.nanmax(saltos_entero[union - 1])
        assert troceando == pytest.approx(sin_trocear, rel=1e-9), (
            f"la unión {union} salta {troceando:.2f} troceando y "
            f"{sin_trocear:.2f} sin trocear"
        )


def test_updraft_helicity_without_vertical_velocity_is_empty():
    """Sin velocidad vertical no hay helicidad que integrar."""
    from server.services.arome_forecast import _updraft_helicity_in_stripes

    p, t, td, u, v, terr, _w, rejilla = _perfil_con_vorticidad(48, 30)

    vacio = _updraft_helicity_in_stripes(p, t, td, u, v, terr, None, rejilla)

    assert vacio.shape == terr.shape
    assert not np.isfinite(vacio).any()


def test_vertical_vorticity_keeps_the_sign_of_the_latitudinal_step():
    """Una rotación sólida ciclónica tiene que dar 2Ω, no cero.

    Las filas de la rejilla bajan de norte a sur, así que su paso latitudinal
    es negativo, y es ese signo el que convierte ∂u/∂fila en ∂u/∂y. Pasado en
    valor absoluto, el término entra cambiado: en un vórtice ideal las dos
    contribuciones se anulan en vez de sumarse y el mapa sale plano.
    """
    from server.services.convective_diagnostics import (
        EARTH_RADIUS_M,
        vertical_vorticity,
    )

    filas = columnas = 40
    paso = 0.025
    latitudes = 43.0 - (np.arange(filas) + 0.5) * paso
    paso_lat_m = np.radians(-paso) * EARTH_RADIUS_M
    paso_lon_m = np.radians(paso) * EARTH_RADIUS_M * np.cos(np.radians(latitudes))
    # Rotación sólida ciclónica en metros: u = −Ωy, v = Ωx, con y hacia el norte.
    omega = 1e-4
    norte = (np.arange(filas) - filas / 2) * paso_lat_m
    este = (np.arange(columnas) - columnas / 2)[None, :] * paso_lon_m[:, None]
    u = np.broadcast_to(-omega * norte[:, None], (1, filas, columnas))
    v = (omega * este)[None, ...]

    zeta = vertical_vorticity(u, v, latitudes, paso, -paso)

    assert np.allclose(zeta, 2 * omega, rtol=1e-6)
    # Con el paso en valor absoluto, que era el error, el vórtice desaparecía.
    plano = vertical_vorticity(u, v, latitudes, paso, paso)
    assert np.abs(plano).max() < 1e-3 * omega


def _columna_giratoria(niveles=24):
    """Una columna con ascenso y rotación, para la integral de la helicidad."""
    filas = columnas = 5
    paso = 0.025
    latitudes = 42.5 - (np.arange(filas) + 0.5) * paso
    altura = np.linspace(0.0, 8_000.0, niveles)[:, None, None] * np.ones(
        (niveles, filas, columnas)
    )
    u = np.zeros_like(altura)
    v = np.zeros_like(altura)
    # ∂v/∂x constante en toda la columna: la vorticidad no depende de la altura.
    v[:] = np.arange(columnas)[None, None, :] * 1.5
    w = np.full_like(altura, 5.0)
    return altura, w, u, v, latitudes, paso, -paso


def test_updraft_helicity_is_nan_when_a_level_inside_the_layer_is_missing():
    """Sin uno de los niveles de en medio, la helicidad no es un número menor.

    Comprobar sólo que la columna llega a 2 y a 5 km deja pasar los huecos
    interiores: si falta un nivel de IP3, ese tramo de w·ζ se salta y la
    integral sale corta. Un valor bajo no se distingue de una columna que gira
    poco, así que tiene que salir NaN.
    """
    from server.services.convective_diagnostics import updraft_helicity

    altura, w, u, v, latitudes, paso_lon, paso_lat = _columna_giratoria()
    entera = updraft_helicity(altura, w, u, v, latitudes, paso_lon, paso_lat)
    assert np.isfinite(entera).all() and (entera > 0).all()

    # Un nivel de IP3 que no llega: cae dentro de la capa 2-5 km.
    dentro = int(np.argmin(np.abs(altura[:, 0, 0] - 3_500.0)))
    assert 2_000.0 < altura[dentro, 0, 0] < 5_000.0
    hueca = w.copy()
    hueca[dentro, 2, 2] = np.nan

    salida = updraft_helicity(altura, hueca, u, v, latitudes, paso_lon, paso_lat)

    assert np.isnan(salida[2, 2]), "un tramo perdido no puede dar valor parcial"
    vecinas = np.delete(salida.ravel(), 2 * salida.shape[1] + 2)
    assert np.isfinite(vecinas).all(), "sólo esa columna se queda sin dato"


def test_updraft_helicity_ignores_a_hole_outside_the_layer():
    """Un nivel perdido fuera de 2-5 km no tiene por qué anular la columna.

    Lo que invalida la integral es el hueco que cae dentro de la capa. Por
    encima de la cima o por debajo de la base, el tramo no entra en la suma y
    la helicidad sigue estando completa.
    """
    from server.services.convective_diagnostics import updraft_helicity

    altura, w, u, v, latitudes, paso_lon, paso_lat = _columna_giratoria()
    entera = updraft_helicity(altura, w, u, v, latitudes, paso_lon, paso_lat)

    fuera = int(np.argmin(np.abs(altura[:, 0, 0] - 7_000.0)))
    assert altura[fuera, 0, 0] > 5_000.0
    hueca = w.copy()
    hueca[fuera, 2, 2] = np.nan

    salida = updraft_helicity(altura, hueca, u, v, latitudes, paso_lon, paso_lat)

    assert np.allclose(salida, entera)


def test_profiles_built_on_disk_give_the_same_numbers(tmp_path, monkeypatch):
    """Llenar los perfiles sobre disco no cambia ningún diagnóstico.

    Apilar en memoria y volcar después hacía convivir tres copias en el peor
    momento: la capa suelta, el perfil apilado y su destino en disco. Escribir
    cada nivel en su sitio elimina dos, pero sólo sirve si el resultado es
    idéntico.

    La referencia se redondea a la precisión con la que se guarda para que lo
    comparado sea el volcado y nada más. Que guardar en float32 no mueva los
    mapas es otra propiedad, y la vigila su propio test: mezclarlas aquí
    dejaría este test pasando por el motivo equivocado.
    """
    from server.services import arome_forecast
    from server.services.arome_forecast import (
        _convective_outputs_in_stripes,
        _empty_profiles_on_disk,
    )

    monkeypatch.setattr(arome_forecast, "PROFILE_SPILL_ENABLED", True)
    monkeypatch.setattr(arome_forecast, "_is_memory_backed", lambda ruta: False)
    monkeypatch.setattr(arome_forecast.tempfile, "gettempdir", lambda: str(tmp_path))

    p, t, td, u, v, terr, su, sv, niveles = _synthetic_profile(64, 40)
    guardado = arome_forecast.PROFILE_STORAGE_DTYPE
    p, t, td, u, v = (campo.astype(guardado) for campo in (p, t, td, u, v))

    en_memoria = _convective_outputs_in_stripes(
        p, t, td, u, v, terr, su, sv, niveles, stripe_rows=16, include_dcape=False
    )

    nombres = ("pressure", "temperature", "dewpoint", "u", "v")
    with _empty_profiles_on_disk(nombres, p.shape) as en_disco:
        assert en_disco is not None, "el volcado tiene que estar activo aquí"
        for nombre, origen in zip(nombres, (p, t, td, u, v)):
            for nivel in range(p.shape[0]):
                en_disco[nombre][nivel] = origen[nivel]
        desde_disco = _convective_outputs_in_stripes(
            *(en_disco[n] for n in nombres),
            terr, su, sv, niveles, stripe_rows=16, include_dcape=False,
        )

    for nombre, esperado in en_memoria.items():
        finitos = np.isfinite(esperado)
        assert (np.isfinite(desde_disco[nombre]) == finitos).all(), nombre
        assert np.array_equal(desde_disco[nombre][finitos], esperado[finitos]), nombre


def test_empty_profiles_fall_back_to_memory_without_a_place_to_spill(monkeypatch):
    """Sobre un tmpfs no se vuelca: quien llama apila en memoria."""
    from server.services import arome_forecast
    from server.services.arome_forecast import _empty_profiles_on_disk

    monkeypatch.setattr(arome_forecast, "_is_memory_backed", lambda ruta: True)

    with _empty_profiles_on_disk(("pressure",), (3, 4, 4)) as perfiles:
        assert perfiles is None

def test_float32_storage_keeps_the_maps_but_not_the_arithmetic():
    """Guardar en float32 no mueve los mapas; calcular en float32 sí los movería.

    Los perfiles ocupan la mitad en disco y en caché desde que se guardan en
    float32, y eso sólo es admisible porque cada banda vuelve a float64 antes
    de entrar en las fórmulas. Este test vigila las dos mitades del trato.

    La segunda mitad no se puede comprobar por el tipo del resultado: numpy
    promueve a float64 en cuanto un float32 se cruza con un escalar de doble
    precisión, así que los campos salen en float64 aunque por el camino se haya
    perdido precisión. Lo que sí distingue una cosa de la otra es exigir que
    partir de float32 dé bit a bit lo mismo que partir de esos mismos valores
    ya en float64: sólo se cumple si la conversión ocurre antes de calcular.

    La tolerancia de la primera mitad no es cosmética: DCAPE elige la capa de
    origen del descenso, y un cambio en el último bit puede hacerle elegir la
    de al lado. Medido sobre 38.400 celdas, eso le pasa a una; a CAPE y SRH, a
    ninguna.
    """
    from server.services.arome_forecast import (
        PROFILE_STORAGE_DTYPE,
        _convective_outputs_in_stripes,
    )

    p, t, td, u, v, terr, su, sv, niveles = _synthetic_profile(64, 60)
    comun = dict(stripe_rows=16, include_dcape=True)

    def diagnosticar(campos):
        return _convective_outputs_in_stripes(*campos, terr, su, sv, niveles, **comun)

    exacto = diagnosticar((p, t, td, u, v))
    como_se_guarda = [campo.astype(PROFILE_STORAGE_DTYPE) for campo in (p, t, td, u, v)]
    guardado = diagnosticar(como_se_guarda)

    # Segunda mitad del trato: el almacenamiento encoge, la aritmética no.
    en_doble = diagnosticar([campo.astype(np.float64) for campo in como_se_guarda])
    for nombre, valores in guardado.items():
        assert np.array_equal(valores, en_doble[nombre], equal_nan=True), (
            f"{nombre} depende de con qué precisión estaba guardado el perfil: "
            "las fórmulas están corriendo en float32"
        )

    # Primera mitad: frente al perfil sin redondear, los mapas no se mueven.
    holgura = {"dcape": 25.0, "mucape": 25.0, "mlcape": 25.0, "sbcape": 25.0,
               "srh_01": 5.0, "srh_03": 5.0}
    for nombre, esperado in exacto.items():
        obtenido = guardado[nombre]
        assert (np.isfinite(obtenido) == np.isfinite(esperado)).all(), (
            f"{nombre} cambia dónde hay dato, no sólo cuánto"
        )
        finitos = np.isfinite(esperado)
        if not finitos.any() or nombre not in holgura:
            continue
        desvia = (np.abs(obtenido[finitos] - esperado[finitos]) > holgura[nombre]).sum()
        limite = 0 if nombre != "dcape" else max(1, int(finitos.sum() * 0.001))
        assert desvia <= limite, f"{nombre}: {desvia} celdas fuera de tolerancia"
