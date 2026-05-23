"""Tests for `loxone_voice.config`."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from loxone_voice.config import Settings, get_settings


def test_settings_loads_from_env(settings: Settings) -> None:
    assert settings.loxone_host == "miniserver.test"
    assert settings.loxone_user == "test-user"
    assert settings.telegram_allowed_user_ids == [12345, 67890]
    assert settings.log_level == "INFO"


def test_https_is_default(settings: Settings) -> None:
    """Miniservers behind real installations use HTTPS — that's our default."""
    assert settings.loxone_use_https is True
    assert settings.loxone_port == 443
    assert settings.loxone_http_url == "https://miniserver.test:443"
    assert settings.loxone_ws_url == "wss://miniserver.test:443/ws/rfc6455"


def test_http_scheme_picks_port_80(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOXONE_HOST", "h")
    monkeypatch.setenv("LOXONE_USER", "u")
    monkeypatch.setenv("LOXONE_PASSWORD", "p")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("LOXONE_USE_HTTPS", "false")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.loxone_use_https is False
    assert settings.loxone_port == 80
    assert settings.loxone_http_url == "http://h:80"
    assert settings.loxone_ws_url == "ws://h:80/ws/rfc6455"


def test_explicit_port_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOXONE_HOST", "h")
    monkeypatch.setenv("LOXONE_USER", "u")
    monkeypatch.setenv("LOXONE_PASSWORD", "p")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("LOXONE_PORT", "8443")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.loxone_port == 8443
    assert settings.loxone_http_url == "https://h:8443"


def test_verify_tls_defaults_to_false(settings: Settings) -> None:
    """Miniservers ship with self-signed certs — verify off by default."""
    assert settings.loxone_verify_tls is False


def test_secrets_are_wrapped_in_secret_str(settings: Settings) -> None:
    assert isinstance(settings.loxone_password, SecretStr)
    assert isinstance(settings.anthropic_api_key, SecretStr)
    assert isinstance(settings.telegram_bot_token, SecretStr)
    assert settings.loxone_password.get_secret_value() == "test-pw"


def test_secret_does_not_leak_in_repr(settings: Settings) -> None:
    rendered = repr(settings)
    assert "test-pw" not in rendered
    assert "sk-ant-test" not in rendered


def test_get_settings_is_cached(settings_env: None) -> None:
    first = get_settings()
    second = get_settings()
    assert first is second


def test_missing_required_field_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOXONE_HOST", raising=False)
    monkeypatch.delenv("LOXONE_USER", raising=False)
    monkeypatch.delenv("LOXONE_PASSWORD", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    get_settings.cache_clear()

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_telegram_ids_parsed_from_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOXONE_HOST", "h")
    monkeypatch.setenv("LOXONE_USER", "u")
    monkeypatch.setenv("LOXONE_PASSWORD", "p")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "  1, 2,3  ,4 ")
    get_settings.cache_clear()

    parsed = get_settings().telegram_allowed_user_ids
    assert parsed == [1, 2, 3, 4]


def test_extra_env_vars_are_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOXONE_HOST", "h")
    monkeypatch.setenv("LOXONE_USER", "u")
    monkeypatch.setenv("LOXONE_PASSWORD", "p")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("UNRELATED_VAR_FOR_LOXONE_VOICE", "boom")
    get_settings.cache_clear()

    settings = get_settings()
    assert not hasattr(settings, "unrelated_var_for_loxone_voice")


def test_invalid_log_level_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOXONE_HOST", "h")
    monkeypatch.setenv("LOXONE_USER", "u")
    monkeypatch.setenv("LOXONE_PASSWORD", "p")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("LOG_LEVEL", "TRACE")
    get_settings.cache_clear()

    with pytest.raises(ValidationError):
        get_settings()
