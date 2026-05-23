"""Tool data model used by the intent engine.

A :class:`Tool` is a triple of (name, description, input schema) plus an
async handler. The collection of all Loxone tools is what we hand to
Claude's tool-use API — Claude then decides which to call in response
to the user's natural-language request, the engine dispatches, and the
results are fed back to the model.

Design choices
--------------
* **Tools are immutable data, not class hierarchies.** Each tool is
  constructed once per session by a factory function that captures the
  dependencies (structure file, state store, Miniserver client). No
  inheritance.
* **Input is validated at the edge.** Every handler receives the raw
  dict from Claude and runs it through the tool's Pydantic ``args_model``
  before doing any work. Validation errors become :class:`ToolError`
  with a clear message; the engine surfaces those back to Claude so it
  can self-correct.
* **Output is JSON-serialisable.** Handlers return primitive values,
  lists, dicts, or Pydantic models — never raw adapter objects. The
  engine ``json.dumps`` the result before passing it to Claude.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError


class ToolError(Exception):
    """Raised by a tool handler when its input is unusable.

    The engine catches these, formats the message as a ``tool_result``
    with ``is_error=True``, and lets Claude decide whether to retry
    with corrected arguments or give up gracefully.
    """


ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class Tool:
    """Single tool exposed to Claude.

    ``requires_confirmation`` is a hint for the gateway: any call that
    would destructively change global state (``all_off``, ``leave_home``,
    bulk blind-down at night) sets this to ``True`` so the gateway can
    intercept and ask the user before executing.
    """

    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler
    requires_confirmation: bool = False

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Return the dict shape Anthropic's tool-use API expects."""
        schema = self.args_model.model_json_schema()
        # Anthropic ignores "title" but it's noise we don't want to ship.
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    async def invoke(self, raw_args: dict[str, Any]) -> Any:
        """Validate ``raw_args`` and run the handler."""
        try:
            validated = self.args_model.model_validate(raw_args)
        except ValidationError as exc:
            raise ToolError(f"invalid arguments for {self.name}: {exc.errors()}") from exc
        return await self.handler(validated.model_dump(exclude_none=True))


def render_tool_result(payload: Any) -> str:
    """Serialise a tool's return value into a string Claude can consume.

    Pydantic models, dataclasses, and primitives are all flattened into
    JSON. ``None`` becomes the literal ``null`` so Claude knows the call
    succeeded but produced no data.
    """
    if isinstance(payload, BaseModel):
        return payload.model_dump_json()
    return json.dumps(payload, default=_default_encoder, ensure_ascii=False)


def _default_encoder(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)
