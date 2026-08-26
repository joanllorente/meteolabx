"""Cliente de los paquetes GRIB2 de AROME."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from types import SimpleNamespace

from rasterio.transform import from_bounds

from server.services import arome_packages as paquetes


RUN = datetime(2026, 8, 26, 3, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("horizonte", "esperado"),
    [(0, "00H06H"), (3, "00H06H"), (6, "00H06H"), (7, "07H13H"), (13, "07H13H"), (36, "35H41H")],
)
def test_block_range_groups_seven_hours(horizonte, esperado):
    """Cada paquete cubre siete plazos consecutivos."""
    valid = RUN.replace() + __import__("datetime").timedelta(hours=horizonte)
    assert paquetes.block_range(RUN, valid) == esperado


def test_block_range_rejects_hours_before_the_run():
    anterior = RUN - __import__("datetime").timedelta(hours=1)
    with pytest.raises(paquetes.AromePackageError, match="anterior"):
        paquetes.block_range(RUN, anterior)


class _DatasetFalso:
    """Un GRIB mínimo con los elementos de IP1, dos niveles y dos plazos."""

    ELEMENTOS = ("TMP", "RH", "UGRD", "VGRD", "GP")

    def __init__(self, stamp, elementos=None):
        self._bandas = []
        for elemento in (elementos or self.ELEMENTOS):
            for nivel in (85000, 50000):
                for instante in (stamp, stamp + 3600):
                    self._bandas.append((elemento, nivel, instante))
        self.count = len(self._bandas)

    def tags(self, index):
        elemento, nivel, instante = self._bandas[index - 1]
        return {
            "GRIB_ELEMENT": elemento,
            "GRIB_SHORT_NAME": f"{nivel}-ISBL",
            "GRIB_VALID_TIME": str(instante),
        }

    def read(self, index, masked=False):
        elemento, nivel, _ = self._bandas[index - 1]
        base = 10.0 if elemento == "TMP" else 5.0
        datos = np.full((3, 4), base + nivel / 10000.0)
        datos[0, 0] = 9999.0  # celda fuera del dominio
        if masked:
            return np.ma.masked_equal(datos, 9999.0)
        return datos

    # Geometría del paquete: el lector debe devolverla para que quien la use
    # alinee los campos en vez de darles por buena una rejilla ajena.
    transform = "transform-del-paquete"
    crs = "crs-del-paquete"
    bounds = "bounds-del-paquete"

    def __enter__(self): return self
    def __exit__(self, *args): return False


def test_reader_selects_the_requested_hour_and_marks_missing_cells(monkeypatch):
    """Lee solo el plazo pedido y convierte el 9999 del GRIB en NaN.

    El resto del pipeline espera NaN fuera del dominio, igual que entrega el
    WCS; dejar el 9999 metería 138.000 celdas de basura en los cálculos.
    """
    stamp = int(RUN.timestamp())
    monkeypatch.setattr(paquetes.rasterio, "open", lambda _p: _DatasetFalso(stamp))

    perfil, geometria = paquetes.read_isobaric_profile(
        Path("da-igual"), RUN, [850.0, 500.0]
    )

    assert geometria == (
        "transform-del-paquete", "crs-del-paquete", "bounds-del-paquete",
    ), "la geometría debe ser la del paquete, no la de quien pregunta"

    assert set(perfil["temperature"]) == {850.0, 500.0}
    assert set(perfil["u"]) == {850.0, 500.0}
    campo = perfil["temperature"][850.0]
    assert np.isnan(campo[0, 0]), "el 9999 debe quedar como NaN"
    assert np.isfinite(campo[1, 1])
    # El plazo siguiente no debe colarse.
    assert campo[1, 1] == pytest.approx(10.0 + 8.5)


def test_reader_complains_when_an_element_is_missing(monkeypatch):
    stamp = int(RUN.timestamp())
    monkeypatch.setattr(paquetes.rasterio, "open", lambda _p: _DatasetFalso(stamp))
    monkeypatch.setattr(
        paquetes.rasterio, "open",
        lambda _p: _DatasetFalso(stamp, elementos=("TMP", "UGRD")),
    )
    with pytest.raises(paquetes.AromePackageError, match="no trae"):
        paquetes.read_isobaric_profile(Path("da-igual"), RUN, [850.0])


def test_old_packages_are_discarded(tmp_path, monkeypatch):
    """Los paquetes de pasadas anteriores ocupan cientos de megas."""
    monkeypatch.setenv("METEOLABX_AROME_PACKAGE_CACHE_DIR", str(tmp_path))
    (tmp_path / "IP1-20260825T21-00H06H.grib2").write_text("viejo")
    (tmp_path / "IP1-20260826T03-00H06H.grib2").write_text("actual")

    borrados = paquetes.discard_packages_before(RUN)

    assert [p.name for p in borrados] == ["IP1-20260825T21-00H06H.grib2"]
    assert (tmp_path / "IP1-20260826T03-00H06H.grib2").exists()


def test_shear_06_reuses_the_package_levels_instead_of_downloading(monkeypatch):
    """La cizalladura 0-6 km lee del paquete los niveles que ya están bajados.

    Interpola el viento a 6 km sobre seis niveles isobáricos; pedirlos al WCS
    son 18 peticiones por hora que el perfil convectivo ya ha traído.
    """
    from tabs import arome_forecast as wcs

    descargas: list[tuple] = []

    limites = (0.0, 40.0, 1.0, 41.0)
    transform = from_bounds(*limites, 5, 4)

    def campo(valor, unidad="m/s"):
        return wcs.RasterField(np.full((4, 5), valor), transform, None, limites, unidad)

    def registrar(_catalog, prefix, _run, _valid, nivel, tipo, component=None, period=None):
        descargas.append((prefix, nivel, tipo))
        return campo(10.0)

    cliente = SimpleNamespace(get_field=registrar)
    base = campo(3.0)
    niveles = {
        nivel: {
            "u": campo(20.0),
            "v": campo(0.0),
            "geopotential": campo(60000.0 + nivel * 10, "m^2/s^2"),
        }
        for nivel in (500.0, 450.0, 400.0, 350.0, 300.0, 250.0)
    }

    wcs._compute_shear(
        cliente,
        object(),
        {"terrain": None, "height_u": "U", "height_v": "V"},
        RUN,
        RUN,
        6000,
        base_uv=(base, base),
        isobaric_levels=niveles,
    )

    isobaricos = [d for d in descargas if d[2] == "pressure"]
    assert not isobaricos, f"no debería pedir niveles al WCS: {isobaricos}"


class _SuperficieFalsa:
    """Un GRIB de superficie con los elementos de SP1/SP2 y dos plazos."""

    transform = "transform-del-paquete"
    crs = "crs-del-paquete"
    bounds = "bounds-del-paquete"

    def __init__(self, stamp, elementos=None):
        catalogo = elementos or (
            ("UGRD", "10-HTGL"), ("VGRD", "10-HTGL"),
            ("DPT", "2-HTGL"), ("PRES", "0-SFC"),
            ("TMP", "2-HTGL"),          # presente pero no pedido
        )
        self._bandas = [
            (el, niv, instante)
            for el, niv in catalogo
            for instante in (stamp, stamp + 3600)
        ]
        self.count = len(self._bandas)

    def tags(self, index):
        elemento, nivel, instante = self._bandas[index - 1]
        return {
            "GRIB_ELEMENT": elemento,
            "GRIB_SHORT_NAME": nivel,
            "GRIB_VALID_TIME": str(instante),
        }

    def read(self, index, masked=False):
        elemento, _, instante = self._bandas[index - 1]
        datos = np.full((3, 4), float(len(elemento)) + (instante % 10))
        datos[0, 0] = 9999.0
        return np.ma.masked_equal(datos, 9999.0) if masked else datos

    def __enter__(self): return self
    def __exit__(self, *args): return False


def test_surface_reader_picks_the_requested_elements_and_hour(monkeypatch):
    """Sólo los elementos pedidos, sólo el plazo pedido, y 9999 como NaN.

    El paquete trae muchos más campos de los que hace falta —rachas, nubosidad,
    radiación—; leerlos todos sería tirar memoria y tiempo por la ventana.
    """
    stamp = int(RUN.timestamp())
    monkeypatch.setattr(paquetes.rasterio, "open", lambda _p: _SuperficieFalsa(stamp))

    campos, geometria = paquetes.read_surface_fields(
        Path("da-igual"), RUN, paquetes.SURFACE_ELEMENTS["SP1"]
    )

    assert set(campos) == {"surface_u", "surface_v"}, "TMP no se pidió"
    assert geometria == (
        "transform-del-paquete", "crs-del-paquete", "bounds-del-paquete",
    )
    valores, unidad = campos["surface_u"]
    assert unidad == "m/s"
    assert np.isnan(valores[0, 0]), "el 9999 debe quedar como NaN"
    # El plazo siguiente no debe colarse.
    assert valores[1, 1] == pytest.approx(len("UGRD") + stamp % 10)


def test_surface_reader_returns_what_it_finds_without_complaining(monkeypatch):
    """Faltar un campo no es un error: quien llama decide si vuelve al WCS."""
    stamp = int(RUN.timestamp())
    monkeypatch.setattr(
        paquetes.rasterio, "open",
        lambda _p: _SuperficieFalsa(stamp, elementos=(("DPT", "2-HTGL"),)),
    )

    campos, _ = paquetes.read_surface_fields(
        Path("da-igual"), RUN, paquetes.SURFACE_ELEMENTS["SP2"]
    )

    assert set(campos) == {"surface_dewpoint"}


def test_surface_package_falls_back_to_wcs_when_a_field_is_missing(monkeypatch):
    """Si a los paquetes les falta un campo, se vuelve al WCS entero.

    Durante la publicación de una pasada un paquete puede existir a medias.
    Devolver campos sueltos dejaría el diagnóstico mezclando fuentes sin que
    nadie lo note; es preferible pagar las cuatro descargas.
    """
    from server.services import arome_forecast as prevision

    monkeypatch.setattr(prevision, "_packages_available", lambda: True)
    monkeypatch.setattr(prevision, "ensure_package", lambda *a: Path("da-igual"))
    def solo_el_rocio(path, valid_time, wanted):
        # Faltan la presión y las dos componentes del viento.
        return {"surface_dewpoint": (np.zeros((3, 4)), "C")}, ("t", "c", "b")

    monkeypatch.setattr(prevision, "read_surface_fields", solo_el_rocio)
    referencia = SimpleNamespace(
        transform=from_bounds(0, 0, 1, 1, 4, 3), crs="epsg:4326", bounds=(0, 0, 1, 1)
    )

    assert prevision._surface_fields_from_package(referencia, RUN, RUN) is None


def test_surface_package_is_skipped_when_packages_are_not_available(monkeypatch):
    """Sin credencial de paquetes ni ámbito de producción, ni se intenta."""
    from server.services import arome_forecast as prevision

    monkeypatch.setattr(prevision, "_packages_available", lambda: False)

    def no_deberia_llamarse(*args, **kwargs):
        raise AssertionError("no debe tocarse el paquete")

    monkeypatch.setattr(prevision, "ensure_package", no_deberia_llamarse)

    assert prevision._surface_fields_from_package(None, RUN, RUN) is None


def test_only_one_process_downloads_a_block(monkeypatch, tmp_path):
    """Varios procesos pidiendo el mismo bloque lo descargan una sola vez.

    Un bloque cubre siete plazos y los workers avanzan por horas consecutivas,
    así que coinciden en el mismo fichero de medio giga. Sin cerrojo cada uno
    se bajaba su copia y competían por el ancho de banda.
    """
    import threading

    monkeypatch.setattr(paquetes, "_cache_dir", lambda: tmp_path)
    descargas = []
    empezada = threading.Event()

    def descarga_lenta(package, run, block, destination):
        descargas.append(block)
        empezada.set()
        # Da tiempo a que el otro hilo llegue al cerrojo antes de terminar.
        __import__("time").sleep(0.2)
        destination.write_bytes(b"grib")
        return destination

    monkeypatch.setattr(paquetes, "_download_package", descarga_lenta)

    resultados = []
    hilos = [
        threading.Thread(
            target=lambda: resultados.append(
                paquetes.ensure_package("IP1", RUN, RUN)
            )
        )
        for _ in range(4)
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=10)

    assert len(descargas) == 1, f"se descargó {len(descargas)} veces"
    assert len(resultados) == 4, "todos deben acabar con el fichero"
    assert all(r.read_bytes() == b"grib" for r in resultados)


def test_a_block_already_on_disk_is_not_downloaded_again(monkeypatch, tmp_path):
    monkeypatch.setattr(paquetes, "_cache_dir", lambda: tmp_path)

    def no_deberia_llamarse(*args, **kwargs):
        raise AssertionError("ya estaba en disco")

    monkeypatch.setattr(paquetes, "_download_package", no_deberia_llamarse)
    ya = paquetes._package_path("IP1", RUN, paquetes.block_range(RUN, RUN))
    ya.write_bytes(b"grib")

    assert paquetes.ensure_package("IP1", RUN, RUN) == ya


def test_old_locks_are_swept_with_their_packages(monkeypatch, tmp_path):
    """Los cerrojos de pasadas viejas también se recogen."""
    monkeypatch.setattr(paquetes, "_cache_dir", lambda: tmp_path)
    viejo = tmp_path / "IP1-20260825T18-00H06H.grib2"
    cerrojo_viejo = tmp_path / "IP1-20260825T18-00H06H.lock"
    actual = tmp_path / "IP1-20260826T03-00H06H.grib2"
    for f in (viejo, cerrojo_viejo, actual):
        f.write_bytes(b"x")

    borrados = paquetes.discard_packages_before(RUN)

    assert set(borrados) == {viejo, cerrojo_viejo}
    assert actual.exists(), "la pasada en curso no se toca"
