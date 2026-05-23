"""Shared test fixtures and helpers."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from loxone_voice.config import Settings, get_settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def loxapp3_minimal() -> dict[str, Any]:
    """Return the parsed minimal `LoxAPP3.json` test fixture."""
    payload = (FIXTURES_DIR / "loxapp3_minimal.json").read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(payload)
    return data


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Populate the minimum env vars Settings needs and clear the cache.

    Tests that touch `get_settings()` should depend on this fixture so
    they don't pick up real `.env` values from the host machine.
    """
    monkeypatch.setenv("LOXONE_HOST", "miniserver.test")
    monkeypatch.setenv("LOXONE_USER", "test-user")
    monkeypatch.setenv("LOXONE_PASSWORD", "test-pw")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "12345,67890")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(settings_env: None) -> Settings:
    """Provide a fresh Settings instance built from the test env."""
    return get_settings()
