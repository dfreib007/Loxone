"""Tests for the Anthropic-backed :class:`IntentEngine`.

We don't call the real Anthropic API. A ``FakeAnthropic`` client is
injected; tests script the sequence of responses it returns to model
single-turn answers, tool-use turns, and multi-iteration loops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel, Field

from loxone_voice.intent import (
    IntentEngine,
    IntentEngineError,
    SystemPrompt,
    Tool,
)

# ---------------------------------------------------------------------------
# Fake Anthropic response shapes
# ---------------------------------------------------------------------------


@dataclass
class _FakeText:
    text: str
    type: str = "text"


@dataclass
class _FakeToolUse:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _FakeResponse:
    content: list[Any]
    stop_reason: str = "end_turn"


@dataclass
class _FakeMessages:
    """Stub for `client.messages` exposing only `create`."""

    scripted: list[_FakeResponse]
    captured: list[dict[str, Any]] = field(default_factory=list)

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.captured.append(kwargs)
        if not self.scripted:
            raise AssertionError("no more scripted responses but engine asked for one")
        return self.scripted.pop(0)


@dataclass
class FakeAnthropic:
    """Stub for ``anthropic.AsyncAnthropic``."""

    messages: _FakeMessages

    @classmethod
    def with_script(cls, *responses: _FakeResponse) -> FakeAnthropic:
        return cls(messages=_FakeMessages(scripted=list(responses)))


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _PingArgs(BaseModel):
    """Arguments for the trivial 'ping' test tool."""


class _EchoArgs(BaseModel):
    msg: str = Field(min_length=1)


def _make_ping_tool() -> Tool:
    async def handler(_: dict[str, Any]) -> dict[str, str]:
        return {"pong": "yes"}

    return Tool(
        name="ping",
        description="Reply with pong.",
        args_model=_PingArgs,
        handler=handler,
    )


def _make_echo_tool() -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, str]:
        return {"echoed": args["msg"]}

    return Tool(
        name="echo",
        description="Echo the input.",
        args_model=_EchoArgs,
        handler=handler,
    )


def _make_broken_tool() -> Tool:
    async def handler(_: dict[str, Any]) -> None:
        raise ValueError("kaboom")

    return Tool(
        name="broken",
        description="Always raises.",
        args_model=_PingArgs,
        handler=handler,
    )


@pytest.fixture
def system_prompt() -> SystemPrompt:
    return SystemPrompt(behavioural_prefix="Be helpful.", house_context="No rooms.")


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_single_turn_no_tools_returns_text(system_prompt: SystemPrompt) -> None:
    client = FakeAnthropic.with_script(
        _FakeResponse(content=[_FakeText("Hallo!")], stop_reason="end_turn")
    )
    engine = IntentEngine(
        client=client,
        model="claude-opus-4-7",
        tools=[],
        system_prompt=system_prompt,
    )
    result = await engine.run("Hi")
    assert result.final_text == "Hallo!"
    assert result.iterations == 1
    assert result.tool_calls == []


async def test_tool_use_then_final_answer(system_prompt: SystemPrompt) -> None:
    client = FakeAnthropic.with_script(
        _FakeResponse(
            content=[_FakeToolUse(id="tu_1", name="ping", input={})],
            stop_reason="tool_use",
        ),
        _FakeResponse(content=[_FakeText("Pong empfangen.")], stop_reason="end_turn"),
    )
    engine = IntentEngine(
        client=client,
        model="claude-opus-4-7",
        tools=[_make_ping_tool()],
        system_prompt=system_prompt,
    )
    result = await engine.run("ping bitte")
    assert result.final_text == "Pong empfangen."
    assert result.iterations == 2
    assert [c.name for c in result.tool_calls] == ["ping"]
    assert result.tool_calls[0].is_error is False
    assert "pong" in result.tool_calls[0].result


async def test_multiple_tools_in_one_iteration(system_prompt: SystemPrompt) -> None:
    client = FakeAnthropic.with_script(
        _FakeResponse(
            content=[
                _FakeToolUse(id="tu_1", name="ping", input={}),
                _FakeToolUse(id="tu_2", name="echo", input={"msg": "hi"}),
            ],
            stop_reason="tool_use",
        ),
        _FakeResponse(content=[_FakeText("Beides erledigt.")], stop_reason="end_turn"),
    )
    engine = IntentEngine(
        client=client,
        model="claude-opus-4-7",
        tools=[_make_ping_tool(), _make_echo_tool()],
        system_prompt=system_prompt,
    )
    result = await engine.run("mach beides")
    assert {c.name for c in result.tool_calls} == {"ping", "echo"}
    assert result.final_text == "Beides erledigt."


# ---------------------------------------------------------------------------
# Errors and edges
# ---------------------------------------------------------------------------


async def test_unknown_tool_reported_back_to_claude(system_prompt: SystemPrompt) -> None:
    """If Claude hallucinates a tool name, we feed the error back instead of crashing."""
    client = FakeAnthropic.with_script(
        _FakeResponse(
            content=[_FakeToolUse(id="tu_1", name="invent_a_tool", input={})],
            stop_reason="tool_use",
        ),
        _FakeResponse(content=[_FakeText("Sorry, das geht nicht.")], stop_reason="end_turn"),
    )
    engine = IntentEngine(
        client=client,
        model="claude-opus-4-7",
        tools=[_make_ping_tool()],
        system_prompt=system_prompt,
    )
    result = await engine.run("erfinde ein tool")
    assert result.tool_calls[0].is_error is True
    assert "unknown tool" in result.tool_calls[0].result
    # The second turn must include the tool_result so Claude sees the failure.
    assert any(
        m.get("role") == "user" and isinstance(m.get("content"), list) for m in engine.conversation
    )


async def test_tool_validation_error_reported_back(system_prompt: SystemPrompt) -> None:
    client = FakeAnthropic.with_script(
        _FakeResponse(
            content=[_FakeToolUse(id="tu_1", name="echo", input={})],  # missing msg
            stop_reason="tool_use",
        ),
        _FakeResponse(content=[_FakeText("Argument fehlt.")], stop_reason="end_turn"),
    )
    engine = IntentEngine(
        client=client,
        model="claude-opus-4-7",
        tools=[_make_echo_tool()],
        system_prompt=system_prompt,
    )
    result = await engine.run("echo ohne Args")
    assert result.tool_calls[0].is_error is True
    assert "invalid arguments" in result.tool_calls[0].result


async def test_unexpected_tool_exception_is_caught(system_prompt: SystemPrompt) -> None:
    """A bug in a tool shouldn't kill the whole turn."""
    client = FakeAnthropic.with_script(
        _FakeResponse(
            content=[_FakeToolUse(id="tu_1", name="broken", input={})],
            stop_reason="tool_use",
        ),
        _FakeResponse(content=[_FakeText("Konnte nicht.")], stop_reason="end_turn"),
    )
    engine = IntentEngine(
        client=client,
        model="claude-opus-4-7",
        tools=[_make_broken_tool()],
        system_prompt=system_prompt,
    )
    result = await engine.run("breche es")
    assert result.tool_calls[0].is_error is True
    assert "internal error" in result.tool_calls[0].result


async def test_iteration_cap_raises(system_prompt: SystemPrompt) -> None:
    """If Claude keeps calling tools forever, we bail out with IntentEngineError."""
    looping_response = _FakeResponse(
        content=[_FakeToolUse(id="tu", name="ping", input={})], stop_reason="tool_use"
    )
    client = FakeAnthropic.with_script(looping_response, looping_response, looping_response)
    engine = IntentEngine(
        client=client,
        model="claude-opus-4-7",
        tools=[_make_ping_tool()],
        system_prompt=system_prompt,
        max_tool_iterations=2,
    )
    with pytest.raises(IntentEngineError, match="iteration cap"):
        await engine.run("loop forever")


# ---------------------------------------------------------------------------
# Conversation buffer behaviour
# ---------------------------------------------------------------------------


async def test_conversation_buffer_accumulates_across_turns(
    system_prompt: SystemPrompt,
) -> None:
    client = FakeAnthropic.with_script(
        _FakeResponse(content=[_FakeText("Erste Antwort")], stop_reason="end_turn"),
        _FakeResponse(content=[_FakeText("Zweite Antwort")], stop_reason="end_turn"),
    )
    engine = IntentEngine(
        client=client,
        model="claude-opus-4-7",
        tools=[],
        system_prompt=system_prompt,
    )
    await engine.run("Hallo")
    await engine.run("Und jetzt?")
    # Both user messages and both assistant replies in the buffer.
    roles = [m["role"] for m in engine.conversation]
    assert roles == ["user", "assistant", "user", "assistant"]


async def test_reset_clears_conversation(system_prompt: SystemPrompt) -> None:
    client = FakeAnthropic.with_script(
        _FakeResponse(content=[_FakeText("Hi")], stop_reason="end_turn")
    )
    engine = IntentEngine(client=client, model="m", tools=[], system_prompt=system_prompt)
    await engine.run("hello")
    assert engine.conversation
    engine.reset()
    assert engine.conversation == []


async def test_buffer_is_trimmed_when_too_long(system_prompt: SystemPrompt) -> None:
    responses = [
        _FakeResponse(content=[_FakeText(f"reply {i}")], stop_reason="end_turn") for i in range(8)
    ]
    client = FakeAnthropic.with_script(*responses)
    engine = IntentEngine(
        client=client,
        model="m",
        tools=[],
        system_prompt=system_prompt,
        max_buffer_messages=4,
    )
    for i in range(8):
        await engine.run(f"q{i}")
    assert len(engine.conversation) <= 4
    # The first message of the trimmed buffer must still be a user turn.
    assert engine.conversation[0]["role"] == "user"


# ---------------------------------------------------------------------------
# Wire-shape assertions
# ---------------------------------------------------------------------------


async def test_system_prompt_split_into_cached_blocks(
    system_prompt: SystemPrompt,
) -> None:
    client = FakeAnthropic.with_script(
        _FakeResponse(content=[_FakeText("ok")], stop_reason="end_turn")
    )
    engine = IntentEngine(client=client, model="m", tools=[], system_prompt=system_prompt)
    await engine.run("hi")
    sent = client.messages.captured[0]
    system_blocks = sent["system"]
    assert isinstance(system_blocks, list)
    assert system_blocks[0] == {"type": "text", "text": "Be helpful."}
    assert system_blocks[1]["cache_control"] == {"type": "ephemeral"}
    assert system_blocks[1]["text"] == "No rooms."


async def test_tool_schemas_passed_to_anthropic(system_prompt: SystemPrompt) -> None:
    client = FakeAnthropic.with_script(
        _FakeResponse(content=[_FakeText("ok")], stop_reason="end_turn")
    )
    engine = IntentEngine(
        client=client,
        model="m",
        tools=[_make_ping_tool()],
        system_prompt=system_prompt,
    )
    await engine.run("hi")
    sent = client.messages.captured[0]
    assert any(t["name"] == "ping" for t in sent["tools"])


async def test_tool_result_appended_to_buffer_with_correct_id(
    system_prompt: SystemPrompt,
) -> None:
    client = FakeAnthropic.with_script(
        _FakeResponse(
            content=[_FakeToolUse(id="tu_42", name="ping", input={})],
            stop_reason="tool_use",
        ),
        _FakeResponse(content=[_FakeText("done")], stop_reason="end_turn"),
    )
    engine = IntentEngine(
        client=client,
        model="m",
        tools=[_make_ping_tool()],
        system_prompt=system_prompt,
    )
    await engine.run("ping")
    tool_result_msg = next(
        m for m in engine.conversation if m["role"] == "user" and isinstance(m["content"], list)
    )
    block = tool_result_msg["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu_42"
    assert block["is_error"] is False
