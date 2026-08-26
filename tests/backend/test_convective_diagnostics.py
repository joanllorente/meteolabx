import numpy as np
import pytest

from server.services.convective_diagnostics import (
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
