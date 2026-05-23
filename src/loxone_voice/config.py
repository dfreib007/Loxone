"""Runtime configuration, loaded from environment / `.env`.

All secrets and per-environment settings live here. Nothing in the code
base reads `os.environ` directly — use `get_settings()` instead.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings.

    Sourced from environment variables and an optional `.env` file. Field
    names are case-insensitive — `LOXONE_HOST` and `loxone_host` both work.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    # Loxone Miniserver
    loxone_host: str = Field(
        description="Miniserver hostname or IP, e.g. 'miniserver.local' or '192.168.1.77'.",
    )
    loxone_port: int = Field(default=80, ge=1, le=65535)
    loxone_user: str = Field(description="Loxone username for the app account.")
    loxone_password: SecretStr = Field(description="Loxone password for the app account.")

    # Anthropic (Claude)
    anthropic_api_key: SecretStr = Field(description="Anthropic API key for the intent engine.")
    anthropic_model: str = Field(
        default="claude-opus-4-7",
        description="Model id used for intent recognition.",
    )

    # Telegram
    telegram_bot_token: SecretStr = Field(description="Token from @BotFather.")
    telegram_allowed_user_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
        description="Comma-separated Telegram user IDs allowed to use the bot.",
    )

    # Speech-to-text (Phase 4)
    openai_api_key: SecretStr | None = Field(default=None)
    whisper_endpoint: AnyHttpUrl | None = Field(
        default=None,
        description="Override for self-hosted Whisper. If unset, OpenAI API is used.",
    )

    # Runtime
    log_level: Annotated[str, Field(pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")] = "INFO"
    audit_log_path: str = "audit.jsonl"

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def _parse_user_ids(cls, value: object) -> object:
        """Accept either a list or a comma-separated string from env."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            return [int(part.strip()) for part in stripped.split(",") if part.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance.

    Cached so we parse env only once per process. Tests can clear the
    cache via `get_settings.cache_clear()` to inject overrides.
    """
    return Settings()
