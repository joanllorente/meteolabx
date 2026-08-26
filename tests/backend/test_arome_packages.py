"""Cliente de los paquetes GRIB2 de AROME."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

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

    def __enter__(self): return self
    def __exit__(self, *args): return False


def test_reader_selects_the_requested_hour_and_marks_missing_cells(monkeypatch):
    """Lee solo el plazo pedido y convierte el 9999 del GRIB en NaN.

    El resto del pipeline espera NaN fuera del dominio, igual que entrega el
    WCS; dejar el 9999 metería 138.000 celdas de basura en los cálculos.
    """
    stamp = int(RUN.timestamp())
    monkeypatch.setattr(paquetes.rasterio, "open", lambda _p: _DatasetFalso(stamp))

    perfil = paquetes.read_isobaric_profile(Path("da-igual"), RUN, [850.0, 500.0])

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
