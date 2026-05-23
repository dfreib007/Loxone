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
from loxone_voice.intent import IntentResult, PendingConfirmation

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
    last_reply_markup: Any | None = None

    async def answer(self, text: str, reply_markup: Any | None = None) -> None:
        self.answers.append(text)
        self.last_reply_markup = reply_markup


@dataclass
class FakeCallbackQuery:
    """Mimics aiogram.types.CallbackQuery: data + message + from_user + answer."""

    from_user: FakeUser
    data: str
    message: FakeMessage
    acks: list[str] = field(default_factory=list)

    async def answer(self, text: str = "") -> None:
        self.acks.append(text)


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


# ---------------------------------------------------------------------------
# Confirmation inline buttons
# ---------------------------------------------------------------------------


def _make_gateway_with(tmp_path: Path, *scripted: IntentResult) -> Gateway:
    engine = FakeEngine(scripted=list(scripted))

    async def factory(_user_id: int, _on_confirm: object) -> GatewayEngine:
        return engine

    return Gateway(
        whitelist=UserWhitelist.from_iterable([42]),
        engine_factory=factory,
        audit=AuditLog(tmp_path / "audit.jsonl"),
        channel="telegram",
        rate_limit_per_minute=0,
    )


def _result_with_pending(text: str = "Bitte bestätige.") -> IntentResult:
    return IntentResult(
        final_text=text,
        tool_calls=[],
        iterations=1,
        pending_confirmations=[
            PendingConfirmation(
                tool_name="set_control",
                tool_arguments={"control_id": "abc", "command": "On"},
                summary="Fenster öffnen",
            )
        ],
    )


def _plain(text: str = "Erledigt.") -> IntentResult:
    return IntentResult(final_text=text, tool_calls=[], iterations=1)


async def test_text_handler_attaches_inline_keyboard_when_pending(tmp_path: Path) -> None:
    gateway = _make_gateway_with(tmp_path, _result_with_pending())
    telegram = TelegramGateway(gateway)
    msg = FakeMessage(from_user=FakeUser(id=42), text="alle Fenster auf")

    await telegram.on_text(msg)

    assert msg.last_reply_markup is not None
    # The keyboard exposes two buttons (Yes, No) with the documented callback_data.
    buttons = [btn for row in msg.last_reply_markup.inline_keyboard for btn in row]
    callback_data = {btn.callback_data for btn in buttons}
    assert callback_data == {"cnf:y", "cnf:n"}


async def test_text_handler_omits_keyboard_when_no_pending(tmp_path: Path) -> None:
    gateway = _make_gateway_with(tmp_path, _plain("Hi."))
    telegram = TelegramGateway(gateway)
    msg = FakeMessage(from_user=FakeUser(id=42), text="hallo")
    await telegram.on_text(msg)
    assert msg.last_reply_markup is None


async def test_callback_yes_executes_via_confirm_action(tmp_path: Path) -> None:
    gateway = _make_gateway_with(tmp_path, _result_with_pending(), _plain("Erledigt."))
    telegram = TelegramGateway(gateway)

    # Prime the pending action.
    prompt_msg = FakeMessage(from_user=FakeUser(id=42), text="Fenster auf")
    await telegram.on_text(prompt_msg)

    # User clicks Yes.
    reply_msg = FakeMessage(from_user=FakeUser(id=42))
    callback = FakeCallbackQuery(from_user=FakeUser(id=42), data="cnf:y", message=reply_msg)
    await telegram.on_confirmation_callback(callback)

    assert reply_msg.answers == ["Erledigt."]
    assert callback.acks == [""]  # spinner ack with no toast text


async def test_callback_no_uses_denied_path(tmp_path: Path) -> None:
    gateway = _make_gateway_with(tmp_path, _result_with_pending(), _plain("Abgebrochen."))
    telegram = TelegramGateway(gateway)

    prompt_msg = FakeMessage(from_user=FakeUser(id=42), text="Fenster auf")
    await telegram.on_text(prompt_msg)

    reply_msg = FakeMessage(from_user=FakeUser(id=42))
    callback = FakeCallbackQuery(from_user=FakeUser(id=42), data="cnf:n", message=reply_msg)
    await telegram.on_confirmation_callback(callback)
    assert reply_msg.answers == ["Abgebrochen."]


async def test_callback_with_no_pending_returns_friendly_message(tmp_path: Path) -> None:
    gateway = _make_gateway_with(tmp_path)  # nothing scripted
    telegram = TelegramGateway(gateway)
    reply_msg = FakeMessage(from_user=FakeUser(id=42))
    callback = FakeCallbackQuery(from_user=FakeUser(id=42), data="cnf:y", message=reply_msg)
    await telegram.on_confirmation_callback(callback)
    assert any("offene Aktion" in a for a in reply_msg.answers)
    assert callback.acks == [
        "Keine offene Aktion mehr (eventuell schon bestätigt oder abgelaufen)."
    ]


async def test_callback_with_unknown_data_silently_acks(tmp_path: Path) -> None:
    """Defensive: a stray callback with unexpected data must not crash."""
    gateway = _make_gateway_with(tmp_path)
    telegram = TelegramGateway(gateway)
    callback = FakeCallbackQuery(
        from_user=FakeUser(id=42),
        data="some-other-prefix",
        message=FakeMessage(from_user=FakeUser(id=42)),
    )
    await telegram.on_confirmation_callback(callback)
    # Just an empty ack — no message answer.
    assert callback.acks == [""]


def test_dispatcher_registers_callback_handler(tmp_path: Path) -> None:
    gateway = _make_gateway_with(tmp_path)
    telegram = TelegramGateway(gateway)
    dp = build_dispatcher(telegram)
    assert dp.callback_query.handlers, "callback_query handler must be registered"
