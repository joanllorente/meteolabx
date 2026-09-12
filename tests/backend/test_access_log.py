"""El registro de accesos solo habla cuando algo va mal.

Uvicorn escribe una línea por petición y el servicio atiende varios miles por
hora: 19.817 accesos frente a 81 avisos en hora y media, el 92 % del log. Con
ese ruido, encontrar los 502 o los 429 que sí importan era buscar una aguja en
un pajar.
"""

from __future__ import annotations

import logging

import pytest

from server.main import _SoloAccesosConProblema, _quiet_successful_accesses


def _acceso(codigo: int) -> logging.LogRecord:
    """Un registro con la forma exacta que emite ``uvicorn.access``."""
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "POST", "/v1/observations/current/processed", "1.1", codigo),
        None,
    )


@pytest.mark.parametrize("codigo", [200, 201, 204, 301, 304])
def test_successful_requests_are_not_logged(codigo: int) -> None:
    assert _SoloAccesosConProblema().filter(_acceso(codigo)) is False


@pytest.mark.parametrize("codigo", [400, 404, 429, 500, 502, 504])
def test_failures_are_always_logged(codigo: int) -> None:
    """Apagar el acceso entero con ``--no-access-log`` habría escondido
    también esto, que es justo lo que se quiere leer."""
    assert _SoloAccesosConProblema().filter(_acceso(codigo)) is True


def test_an_unexpected_record_is_let_through() -> None:
    """Si uvicorn cambia el formato, se registra de más antes que de menos."""
    filtro = _SoloAccesosConProblema()
    suelto = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "algo", None, None)
    assert filtro.filter(suelto) is True

    corto = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s", ("x",), None)
    assert filtro.filter(corto) is True

    no_numerico = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, "%s", ("a", "b", "c", "d", "?"), None,
    )
    assert filtro.filter(no_numerico) is True


def test_the_full_log_can_be_recovered(monkeypatch) -> None:
    """Para depurar un problema de tráfico hace falta verlo todo."""
    from server.config import get_settings

    acceso = logging.getLogger("uvicorn.access")
    acceso.filters = [f for f in acceso.filters if not isinstance(f, _SoloAccesosConProblema)]

    monkeypatch.setenv("METEOLABX_ACCESS_LOG_ALL", "1")
    _quiet_successful_accesses(get_settings())
    assert not any(isinstance(f, _SoloAccesosConProblema) for f in acceso.filters)

    monkeypatch.delenv("METEOLABX_ACCESS_LOG_ALL")
    _quiet_successful_accesses(get_settings())
    assert any(isinstance(f, _SoloAccesosConProblema) for f in acceso.filters)


def test_the_filter_is_not_stacked_twice() -> None:
    """``create_app`` se llama varias veces en los tests."""
    from server.config import get_settings

    acceso = logging.getLogger("uvicorn.access")
    for _ in range(3):
        _quiet_successful_accesses(get_settings())
    instalados = [f for f in acceso.filters if isinstance(f, _SoloAccesosConProblema)]
    assert len(instalados) == 1
