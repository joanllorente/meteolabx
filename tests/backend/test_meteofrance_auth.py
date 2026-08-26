"""Renovación de la credencial OAuth del portal de Météo-France."""

from __future__ import annotations

import json
import multiprocessing
import time

import pytest

from server.services import meteofrance_auth as auth


@pytest.fixture(autouse=True)
def _entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("METEOLABX_METEOFRANCE_TOKEN_CACHE", str(tmp_path / "token.json"))
    monkeypatch.setenv("METEOLABX_METEOFRANCE_APPLICATION_ID", "identificador-basic")


def _contador(monkeypatch):
    peticiones: list[int] = []

    def falso_token():
        peticiones.append(1)
        return f"token-{len(peticiones)}", time.time() + 3600

    monkeypatch.setattr(auth, "_request_token", falso_token)
    return peticiones


def test_token_is_requested_once_and_reused(monkeypatch):
    """El portal deja un solo token vivo: pedir de más invalida el anterior."""
    peticiones = _contador(monkeypatch)

    assert auth.access_token() == "token-1"
    assert auth.access_token() == "token-1"
    assert auth.access_token() == "token-1"
    assert len(peticiones) == 1


def test_token_is_renewed_before_it_expires(monkeypatch):
    """Se renueva antes de caducar: una descarga larga no debe quedarse a medias."""
    peticiones = _contador(monkeypatch)
    auth.access_token()

    auth._store("token-viejo", time.time() + auth.RENEWAL_MARGIN_S - 10)
    assert auth.access_token() == "token-2"
    assert len(peticiones) == 2


def test_a_rejected_token_is_replaced_only_if_it_is_still_the_stored_one(monkeypatch):
    """Si otro proceso ya renovó, se aprovecha en vez de pedir uno más.

    Cada petición de más invalidaría la credencial que ese otro proceso está
    usando, así que un rechazo no debe traducirse siempre en un token nuevo.
    """
    peticiones = _contador(monkeypatch)
    auth.access_token()  # token-1, el que usan todos

    # Otro proceso renovó por su cuenta mientras este seguía con el viejo.
    auth._store("token-de-otro", time.time() + 3600)

    assert auth.access_token(rejected="token-1") == "token-de-otro"
    assert len(peticiones) == 1, "no debe pedirse otro si ya hay uno nuevo guardado"

    # En cambio, si el rechazado es justo el guardado, sí toca renovar.
    assert auth.access_token(rejected="token-de-otro") == "token-2"
    assert len(peticiones) == 2


def _pedir(cache: str, resultados) -> None:  # pragma: no cover - subproceso
    import os
    os.environ["METEOLABX_METEOFRANCE_TOKEN_CACHE"] = cache
    os.environ["METEOLABX_METEOFRANCE_APPLICATION_ID"] = "basic"
    from server.services import meteofrance_auth as modulo
    contador = modulo._cache_path().with_suffix(".contador")

    def falso():
        # Cada emisión real deja rastro para contarlas desde el padre.
        with open(contador, "a", encoding="ascii") as fichero:
            fichero.write("x")
        return f"token-{os.getpid()}", time.time() + 3600

    modulo._request_token = falso
    resultados.put(modulo.access_token())


def test_concurrent_processes_share_a_single_token(tmp_path):
    """Seis procesos a la vez deben acabar con el mismo token, no con seis."""
    cache = str(tmp_path / "compartido.json")
    contexto = multiprocessing.get_context("spawn")
    cola = contexto.Queue()
    procesos = [contexto.Process(target=_pedir, args=(cache, cola)) for _ in range(6)]
    for proceso in procesos:
        proceso.start()
    for proceso in procesos:
        proceso.join(30)

    obtenidos = {cola.get(timeout=5) for _ in procesos}
    emisiones = len(
        (tmp_path / "compartido.contador").read_text(encoding="ascii")
        if (tmp_path / "compartido.contador").exists() else ""
    )
    assert len(obtenidos) == 1, f"cada proceso obtuvo un token distinto: {obtenidos}"
    assert emisiones == 1, f"se emitieron {emisiones} tokens; el portal solo mantiene uno"


def test_missing_application_id_is_reported_clearly(monkeypatch):
    monkeypatch.delenv("METEOLABX_METEOFRANCE_APPLICATION_ID", raising=False)
    with pytest.raises(auth.MeteoFranceAuthError, match="APPLICATION_ID"):
        auth._application_id()


def test_headers_carry_the_bearer_scheme(monkeypatch):
    monkeypatch.setattr(auth, "_request_token", lambda: ("abc", time.time() + 3600))
    assert auth.authorization_headers()["Authorization"] == "Bearer abc"
