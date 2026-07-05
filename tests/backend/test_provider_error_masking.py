"""Enmascarado de credenciales en ProviderError.detail.

Red de seguridad: aunque los servicios sanean sus mensajes a mano, un
``detail=str(exc)`` con un ``httpx.HTTPStatusError`` incluiría la URL
completa con la query (``?apiKey=...``). El detail llega a logs,
``/v1/diagnostics`` y la respuesta HTTP → nunca debe salir una key.
"""
import pytest

from server.schemas.errors import ProviderError


def test_masks_wu_style_apikey_in_url():
    err = ProviderError(
        "provider_bad_response",
        provider="WU",
        detail=(
            "Client error '401 Unauthorized' for url "
            "'https://api.weather.com/v2/pws/observations/current"
            "?stationId=IMADRID1&format=json&apiKey=abc123SECRETO&units=m'"
        ),
    )
    assert "abc123SECRETO" not in err.detail
    assert "apiKey=***" in err.detail
    # El resto del contexto (útil para depurar) se conserva.
    assert "stationId=IMADRID1" in err.detail
    assert "401 Unauthorized" in err.detail


def test_masks_weatherlink_and_secret_params():
    err = ProviderError(
        "provider_network_error",
        provider="WEATHERLINK",
        detail="GET https://api.weatherlink.com/v2/current/123?api-key=KEY111 api_secret=SEC222",
    )
    assert "KEY111" not in err.detail
    assert "SEC222" not in err.detail
    assert "api-key=***" in err.detail
    assert "api_secret=***" in err.detail


@pytest.mark.parametrize(
    "detail",
    [
        "Read timeout after 10s",
        "AEMET estado=404: datos no encontrados",  # 'estado=' no es credencial
        "Station not found (HTTP 404)",
    ],
)
def test_clean_details_pass_through_unchanged(detail):
    assert ProviderError("x", detail=detail).detail == detail


def test_none_detail_stays_none():
    err = ProviderError("provider_timeout", provider="WU", detail=None)
    assert err.detail is None
    # str(exc) cae al error_code, como siempre.
    assert str(err) == "provider_timeout"


def test_masked_everywhere_str_and_response():
    err = ProviderError("x", provider="WU", detail="url?apiKey=TOPSECRET")
    assert "TOPSECRET" not in str(err)
    assert "TOPSECRET" not in (err.to_response().detail or "")
