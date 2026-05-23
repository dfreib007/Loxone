"""Tests for `loxone_voice.gateway.telegram_bot`.

aiogram ``Message`` objects are heavy Pydantic models, so we don't
build real ones. A minimal ``FakeMessage`` mimics the fields the
handlers actually touch (``from_user.id``, ``text``, ``answer``).
The dispatcher-building function is exercised end-to-end with the
real :class:`aiogram.Dispatcher` so we'd notice a filter registration
regression.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from loxone_voice.audit import AuditLog
from loxone_voice.gateway import (
    Gateway,
    GatewayEngine,
    TelegramGateway,
    UserWhitelist,
    build_dispatcher,
)
from loxone_voice.intent import IntentResult

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeUser:
    id: int


@dataclass
class FakeMessage:
    """Mimics enough of aiogram.types.Message for the handlers."""

    from_user: FakeUser | None
    text: str | None = None
    answers: list[str] = field(default_factory=list)

    async def answer(self, text: str) -> None:
        self.answers.append(text)


@dataclass
class FakeEngine:
    scripted: list[IntentResult]
    _model: str = "claude-opus-4-7-test"

    async def run(self, user_message: str) -> IntentResult:
        return self.scripted.pop(0)


@pytest.fixture
def gateway(tmp_path: Path) -> Gateway:
    engine = FakeEngine(
        scripted=[IntentResult(final_text="Erledigt.", tool_calls=[], iterations=1)] * 20
    )

    async def factory(_user_id: int, _on_confirm: object) -> GatewayEngine:
        return engine

    return Gateway(
        whitelist=UserWhitelist.from_iterable([42]),
        engine_factory=factory,
        audit=AuditLog(tmp_path / "audit.jsonl"),
        channel="telegram",
        rate_limit_per_minute=0,
    )


@pytest.fixture
def telegram(gateway: Gateway) -> TelegramGateway:
    return TelegramGateway(gateway)


# ---------------------------------------------------------------------------
# Slash command handlers
# ---------------------------------------------------------------------------


async def test_start_command_greets(telegram: TelegramGateway) -> None:
    msg = FakeMessage(from_user=FakeUser(id=42))
    await telegram.on_start(msg)
    assert msg.answers
    assert "Loxone" in msg.answers[0]


async def test_help_command_lists_commands(telegram: TelegramGateway) -> None:
    msg = FakeMessage(from_user=FakeUser(id=42))
    await telegram.on_help(msg)
    assert "/start" in msg.answers[0]
    assert "/help" in msg.answers[0]
    assert "/reset" in msg.answers[0]


async def test_reset_command_clears_user_engine(
    telegram: TelegramGateway, gateway: Gateway
) -> None:
    # Prime the engine cache.
    await gateway.handle_text(user_id=42, text="hi")
    assert 42 in gateway._engines

    await telegram.on_reset(FakeMessage(from_user=FakeUser(id=42)))
    assert 42 not in gateway._engines


# ---------------------------------------------------------------------------
# Text and voice
# ---------------------------------------------------------------------------


async def test_text_handler_forwards_to_gateway(telegram: TelegramGateway) -> None:
    msg = FakeMessage(from_user=FakeUser(id=42), text="Licht an")
    await telegram.on_text(msg)
    assert msg.answers == ["Erledigt."]


async def test_text_handler_replies_to_non_whitelisted_user(
    telegram: TelegramGateway,
) -> None:
    msg = FakeMessage(from_user=FakeUser(id=9999), text="hi")
    await telegram.on_text(msg)
    assert msg.answers
    assert "nicht freigeschaltet" in msg.answers[0]


async def test_text_handler_skips_message_without_user(
    telegram: TelegramGateway,
) -> None:
    msg = FakeMessage(from_user=None, text="orphan")
    await telegram.on_text(msg)
    assert msg.answers == []


async def test_text_handler_skips_message_without_text(
    telegram: TelegramGateway,
) -> None:
    msg = FakeMessage(from_user=FakeUser(id=42), text=None)
    await telegram.on_text(msg)
    assert msg.answers == []


async def test_voice_handler_returns_not_yet_supported(
    telegram: TelegramGateway,
) -> None:
    msg = FakeMessage(from_user=FakeUser(id=42))
    await telegram.on_voice(msg)
    assert any("Sprachnachrichten" in a for a in msg.answers)


# ---------------------------------------------------------------------------
# Dispatcher wiring
# ---------------------------------------------------------------------------


def test_build_dispatcher_registers_all_handlers(telegram: TelegramGateway) -> None:
    dp = build_dispatcher(telegram)
    # The Dispatcher should have at least one handler registered for messages.
    assert dp.message.handlers
    # We registered five handlers: start, help, reset, voice, text.
    assert len(dp.message.handlers) == 5


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


async def test_handler_tolerates_object_without_answer_method(
    telegram: TelegramGateway,
) -> None:
    """If aiogram ever sends us an event without `.answer`, log and move on."""

    class NoAnswerMessage:
        from_user = FakeUser(id=42)
        text = "hi"

    no_answer: Any = NoAnswerMessage()
    # Must not raise.
    await telegram.on_text(no_answer)
