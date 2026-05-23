"""Tests for `loxone_voice.gateway.core`.

The Gateway is exercised against fake IntentEngine + IntentResult
objects so we don't pull Anthropic SDK semantics into these tests.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from loxone_voice.audit import AuditLog
from loxone_voice.gateway import Gateway, GatewayEngine, UserWhitelist
from loxone_voice.intent import IntentEngineError, IntentResult, ToolCall

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeEngine:
    """Stub engine that returns scripted IntentResults."""

    scripted: list[IntentResult | Exception]
    calls: list[str] = field(default_factory=list)
    _model: str = "claude-opus-4-7-test"

    async def run(self, text: str) -> IntentResult:
        self.calls.append(text)
        if not self.scripted:
            raise AssertionError("no more scripted results")
        item = self.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _intent_ok(text: str = "Erledigt.", tools: list[ToolCall] | None = None) -> IntentResult:
    return IntentResult(final_text=text, tool_calls=tools or [], iterations=1)


def _factory_returning(engine: FakeEngine) -> Callable[[int], Awaitable[GatewayEngine]]:
    async def factory(_user_id: int) -> GatewayEngine:
        return engine

    return factory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def whitelist() -> UserWhitelist:
    return UserWhitelist.from_iterable([42, 99])


# ---------------------------------------------------------------------------
# Whitelist enforcement
# ---------------------------------------------------------------------------


async def test_rejects_message_from_non_whitelisted_user(
    whitelist: UserWhitelist, audit: AuditLog
) -> None:
    engine = FakeEngine(scripted=[])
    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=_factory_returning(engine),
        audit=audit,
        channel="telegram",
    )
    response = await gateway.handle_text(user_id=1234, text="hi")
    assert response.is_error
    assert "nicht freigeschaltet" in response.text
    assert engine.calls == []


async def test_allows_message_from_whitelisted_user(
    whitelist: UserWhitelist, audit: AuditLog
) -> None:
    engine = FakeEngine(scripted=[_intent_ok("ok")])
    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=_factory_returning(engine),
        audit=audit,
        channel="telegram",
    )
    response = await gateway.handle_text(user_id=42, text="schalte das Licht")
    assert not response.is_error
    assert response.text == "ok"
    assert engine.calls == ["schalte das Licht"]


# ---------------------------------------------------------------------------
# Empty / whitespace messages
# ---------------------------------------------------------------------------


async def test_empty_message_rejected_with_friendly_error(
    whitelist: UserWhitelist, audit: AuditLog
) -> None:
    engine = FakeEngine(scripted=[])
    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=_factory_returning(engine),
        audit=audit,
        channel="telegram",
    )
    response = await gateway.handle_text(user_id=42, text="   ")
    assert response.is_error
    assert "Befehl" in response.text
    assert engine.calls == []


# ---------------------------------------------------------------------------
# Audit log writes
# ---------------------------------------------------------------------------


async def test_successful_turn_appends_audit_record(
    whitelist: UserWhitelist, audit: AuditLog
) -> None:
    engine = FakeEngine(
        scripted=[
            _intent_ok(
                text="Licht an.",
                tools=[
                    ToolCall(
                        name="set_control",
                        arguments={"control_id": "abc", "command": "On"},
                        result='{"ok": true}',
                        is_error=False,
                    )
                ],
            )
        ]
    )
    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=_factory_returning(engine),
        audit=audit,
        channel="telegram",
    )

    await gateway.handle_text(user_id=42, text="Licht an")

    lines = audit.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["channel"] == "telegram"
    assert record["input"] == "Licht an"
    assert record["final"] == "Licht an."
    assert record["tool_calls"][0]["name"] == "set_control"
    assert record["tool_calls"][0]["ok"] is True
    # Raw user id never appears.
    assert "42" not in record["user"]


async def test_audit_record_has_non_zero_duration(
    whitelist: UserWhitelist, audit: AuditLog
) -> None:
    engine = FakeEngine(scripted=[_intent_ok()])
    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=_factory_returning(engine),
        audit=audit,
        channel="telegram",
    )
    await gateway.handle_text(user_id=42, text="hi")
    record = json.loads(audit.path.read_text(encoding="utf-8").splitlines()[0])
    assert record["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Engine failure handling
# ---------------------------------------------------------------------------


async def test_engine_failure_returns_error_response(
    whitelist: UserWhitelist, audit: AuditLog
) -> None:
    engine = FakeEngine(scripted=[IntentEngineError("model unreachable")])
    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=_factory_returning(engine),
        audit=audit,
        channel="telegram",
    )
    response = await gateway.handle_text(user_id=42, text="hi")
    assert response.is_error
    assert "nochmal" in response.text


async def test_engine_failure_still_audited(whitelist: UserWhitelist, audit: AuditLog) -> None:
    engine = FakeEngine(scripted=[IntentEngineError("model unreachable")])
    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=_factory_returning(engine),
        audit=audit,
        channel="telegram",
    )
    await gateway.handle_text(user_id=42, text="hi")
    lines = audit.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert "engine error" in record["final"]


# ---------------------------------------------------------------------------
# Engine caching per user
# ---------------------------------------------------------------------------


async def test_engine_is_reused_across_messages_from_same_user(
    whitelist: UserWhitelist, audit: AuditLog
) -> None:
    engine = FakeEngine(scripted=[_intent_ok("A"), _intent_ok("B")])
    factory_calls: list[int] = []

    async def factory(user_id: int) -> FakeEngine:
        factory_calls.append(user_id)
        return engine

    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=factory,
        audit=audit,
        channel="telegram",
    )
    await gateway.handle_text(user_id=42, text="erste")
    await gateway.handle_text(user_id=42, text="zweite")
    assert factory_calls == [42]


async def test_reset_user_drops_cached_engine(whitelist: UserWhitelist, audit: AuditLog) -> None:
    engines: list[FakeEngine] = []
    factory_calls: list[int] = []

    async def factory(user_id: int) -> FakeEngine:
        eng = FakeEngine(scripted=[_intent_ok("hi")])
        engines.append(eng)
        factory_calls.append(user_id)
        return eng

    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=factory,
        audit=audit,
        channel="telegram",
    )
    await gateway.handle_text(user_id=42, text="hi")
    gateway.reset_user(42)
    await gateway.handle_text(user_id=42, text="hi again")
    assert factory_calls == [42, 42]
    assert len(engines) == 2


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


async def test_rate_limit_rejects_after_threshold(
    whitelist: UserWhitelist, audit: AuditLog
) -> None:
    engine = FakeEngine(scripted=[_intent_ok("ok")] * 5)
    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=_factory_returning(engine),
        audit=audit,
        channel="telegram",
        rate_limit_per_minute=3,
    )
    responses = [await gateway.handle_text(user_id=42, text=f"msg {i}") for i in range(5)]
    ok = [r for r in responses if not r.is_error]
    rate_limited = [r for r in responses if r.is_error and "Befehle" in r.text]
    assert len(ok) == 3
    assert len(rate_limited) == 2


async def test_rate_limit_disabled_when_zero(whitelist: UserWhitelist, audit: AuditLog) -> None:
    engine = FakeEngine(scripted=[_intent_ok("ok")] * 100)
    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=_factory_returning(engine),
        audit=audit,
        channel="telegram",
        rate_limit_per_minute=0,
    )
    for i in range(50):
        response = await gateway.handle_text(user_id=42, text=f"msg {i}")
        assert not response.is_error


async def test_rate_limit_is_per_user(whitelist: UserWhitelist, audit: AuditLog) -> None:
    """User A hitting the limit shouldn't block user B."""

    async def factory(user_id: int) -> FakeEngine:
        return FakeEngine(scripted=[_intent_ok("ok")] * 20)

    gateway = Gateway(
        whitelist=whitelist,
        engine_factory=factory,
        audit=audit,
        channel="telegram",
        rate_limit_per_minute=2,
    )
    # Saturate user 42.
    for _ in range(3):
        await gateway.handle_text(user_id=42, text="hit")
    # User 99 is still fresh.
    response = await gateway.handle_text(user_id=99, text="hi")
    assert not response.is_error
