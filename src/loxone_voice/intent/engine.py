"""Anthropic-backed intent engine.

The engine drives a *tool-use loop*: it sends the user's request plus the
available tools to Claude, executes whatever tools Claude decides to call,
feeds the results back, and repeats until Claude produces a final text
answer (or the iteration cap is reached).

The Anthropic SDK isn't called directly — :class:`IntentEngine` accepts
any object that satisfies :class:`AnthropicMessagesClient`, which is the
minimal duck-typed surface we actually use. The real
``anthropic.AsyncAnthropic`` matches it; so does a tiny test fake.

Conversation state is kept inside the engine instance: every call to
``run`` appends to a rolling buffer. The buffer is automatically trimmed
when it exceeds ``max_buffer_messages`` so long-running sessions don't
balloon context.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .system_prompt import SystemPrompt
from .tool import Tool, ToolError, render_tool_result

logger = logging.getLogger(__name__)


class IntentEngineError(RuntimeError):
    """Raised when the engine cannot produce a coherent response.

    Includes hitting the tool-use iteration cap with no final answer
    and unrecoverable upstream errors from Claude.
    """


# ---------------------------------------------------------------------------
# Duck-typed interfaces we expect from the Anthropic SDK
# ---------------------------------------------------------------------------


@runtime_checkable
class _ContentBlock(Protocol):
    @property
    def type(self) -> str: ...


@runtime_checkable
class _AnthropicResponse(Protocol):
    @property
    def content(self) -> list[Any]: ...
    @property
    def stop_reason(self) -> str | None: ...


@runtime_checkable
class _AnthropicMessages(Protocol):
    async def create(self, **kwargs: Any) -> _AnthropicResponse: ...


@runtime_checkable
class AnthropicMessagesClient(Protocol):
    """Slice of ``anthropic.AsyncAnthropic`` the engine actually touches.

    The real client satisfies this naturally; tests inject a stub.
    """

    @property
    def messages(self) -> _AnthropicMessages: ...


# ---------------------------------------------------------------------------
# Result data
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool execution from inside a turn."""

    name: str
    arguments: dict[str, Any]
    result: str
    is_error: bool


@dataclass(frozen=True, slots=True)
class IntentResult:
    """Outcome of a single :meth:`IntentEngine.run` call."""

    final_text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    iterations: int = 1


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

_DEFAULT_MAX_ITERATIONS = 6
_DEFAULT_MAX_TOKENS = 1024
_DEFAULT_MAX_BUFFER_MESSAGES = 20


class IntentEngine:
    """Stateful per-user intent engine.

    One instance per conversation. Create a fresh one for a new user or
    when you want to wipe history.
    """

    def __init__(
        self,
        *,
        client: AnthropicMessagesClient,
        model: str,
        tools: Sequence[Tool],
        system_prompt: SystemPrompt,
        max_tool_iterations: int = _DEFAULT_MAX_ITERATIONS,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        max_buffer_messages: int = _DEFAULT_MAX_BUFFER_MESSAGES,
    ) -> None:
        self._client = client
        self._model = model
        self._tools_by_name = {t.name: t for t in tools}
        self._tool_schemas = [t.to_anthropic_schema() for t in tools]
        self._system_prompt = system_prompt
        self._max_tool_iterations = max_tool_iterations
        self._max_tokens = max_tokens
        self._max_buffer_messages = max_buffer_messages
        self._buffer: list[dict[str, Any]] = []

    # ---- Public ------------------------------------------------------------

    @property
    def conversation(self) -> list[dict[str, Any]]:
        """Read-only view of the rolling message buffer (for inspection)."""
        return list(self._buffer)

    def reset(self) -> None:
        """Drop the current conversation buffer."""
        self._buffer.clear()

    async def run(self, user_message: str) -> IntentResult:
        """Execute one user turn end-to-end and return the final text."""
        self._buffer.append({"role": "user", "content": user_message})
        tool_calls: list[ToolCall] = []

        for iteration in range(1, self._max_tool_iterations + 1):
            response = await self._client.messages.create(
                model=self._model,
                system=self._render_system_blocks(),
                tools=self._tool_schemas,
                messages=self._buffer,
                max_tokens=self._max_tokens,
            )
            self._buffer.append(
                {"role": "assistant", "content": _serialize_blocks(response.content)}
            )
            self._trim_buffer()

            if response.stop_reason != "tool_use":
                return IntentResult(
                    final_text=_extract_text(response.content),
                    tool_calls=tool_calls,
                    iterations=iteration,
                )

            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                call = await self._execute(name=block.name, raw_args=dict(block.input or {}))
                tool_calls.append(call)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": call.result,
                        "is_error": call.is_error,
                    }
                )
            self._buffer.append({"role": "user", "content": tool_results})
            self._trim_buffer()

        raise IntentEngineError(
            f"reached tool-use iteration cap ({self._max_tool_iterations}) without a final answer"
        )

    # ---- Internals ---------------------------------------------------------

    async def _execute(self, *, name: str, raw_args: dict[str, Any]) -> ToolCall:
        tool = self._tools_by_name.get(name)
        if tool is None:
            return ToolCall(
                name=name,
                arguments=raw_args,
                result=f"unknown tool: {name!r}",
                is_error=True,
            )
        try:
            result = await tool.invoke(raw_args)
        except ToolError as exc:
            return ToolCall(name=name, arguments=raw_args, result=str(exc), is_error=True)
        except Exception as exc:
            # Unexpected handler failure — return as tool error so Claude can
            # try a different approach, but log for diagnosis.
            logger.exception("tool %s raised an unexpected error", name)
            return ToolCall(
                name=name,
                arguments=raw_args,
                result=f"internal error in tool {name}: {exc!s}",
                is_error=True,
            )
        return ToolCall(
            name=name,
            arguments=raw_args,
            result=render_tool_result(result),
            is_error=False,
        )

    def _render_system_blocks(self) -> list[dict[str, Any]]:
        """Two-block system prompt with prompt-cache on the dynamic body."""
        return [
            {"type": "text", "text": self._system_prompt.behavioural_prefix},
            {
                "type": "text",
                "text": self._system_prompt.house_context,
                "cache_control": {"type": "ephemeral"},
            },
        ]

    def _trim_buffer(self) -> None:
        if len(self._buffer) <= self._max_buffer_messages:
            return
        # Always keep the buffer starting with a user message so Anthropic's
        # validator is happy; drop oldest assistant/tool pairs first.
        excess = len(self._buffer) - self._max_buffer_messages
        del self._buffer[:excess]
        while self._buffer and self._buffer[0].get("role") != "user":
            self._buffer.pop(0)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _serialize_blocks(blocks: Iterable[Any]) -> list[dict[str, Any]]:
    """Convert SDK ContentBlock objects into plain dicts for the buffer.

    The Anthropic SDK accepts both typed objects and plain dicts in the
    ``messages`` parameter. Storing dicts keeps the buffer JSON-serialisable
    and decouples us from SDK release-to-release type changes.
    """
    serialized: list[dict[str, Any]] = []
    for block in blocks:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            serialized.append({"type": "text", "text": getattr(block, "text", "")})
        elif block_type == "tool_use":
            serialized.append(
                {
                    "type": "tool_use",
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": dict(getattr(block, "input", {}) or {}),
                }
            )
        elif isinstance(block, dict):
            serialized.append(block)
    return serialized


def _extract_text(blocks: Iterable[Any]) -> str:
    """Concatenate every ``text`` block's content into one string."""
    parts: list[str] = []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p).strip()
