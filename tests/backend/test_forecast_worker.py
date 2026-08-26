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
