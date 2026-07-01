from __future__ import annotations

from server.config import Settings


_CREDENTIAL_ENV_NAMES = (
    "METEOLABX_AEMET_API_KEY",
    "METEOLABX_METEOCAT_API_KEY",
    "METEOLABX_METEOFRANCE_API_KEY",
    "METEOLABX_METOFFICE_API_KEY",
    "METEOLABX_FROST_CLIENT_ID",
    "METEOLABX_FROST_CLIENT_SECRET",
    "AEMET_API_KEY",
    "METEOCAT_API_KEY",
    "METEOFRANCE_API_KEY",
    "METOFFICE_API_KEY",
    "FROST_CLIENT_ID",
    "FROST_CLIENT_SECRET",
)


def test_provider_credentials_are_empty_without_environment(monkeypatch) -> None:
    for name in _CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.aemet_api_key == ""
    assert settings.meteocat_api_key == ""
    assert settings.meteofrance_api_key == ""
    assert settings.metoffice_api_key == ""
    assert settings.frost_client_id == ""
    assert settings.frost_client_secret == ""


def test_legacy_environment_aliases_remain_supported(monkeypatch) -> None:
    for name in _CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AEMET_API_KEY", "legacy-aemet")
    monkeypatch.setenv("FROST_CLIENT_ID", "legacy-frost-id")
    monkeypatch.setenv("FROST_CLIENT_SECRET", "legacy-frost-secret")

    settings = Settings(_env_file=None)

    assert settings.aemet_api_key == "legacy-aemet"
    assert settings.frost_client_id == "legacy-frost-id"
    assert settings.frost_client_secret == "legacy-frost-secret"


def test_prefixed_environment_takes_precedence(monkeypatch) -> None:
    for name in _CREDENTIAL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AEMET_API_KEY", "legacy")
    monkeypatch.setenv("METEOLABX_AEMET_API_KEY", "canonical")

    settings = Settings(_env_file=None)

    assert settings.aemet_api_key == "canonical"
