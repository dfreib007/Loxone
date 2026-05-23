"""Tests for `loxone_voice.intent.tool`."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, Field

from loxone_voice.intent import Tool, ToolError, render_tool_result


class _Args(BaseModel):
    name: str = Field(min_length=1)
    count: int = 1


async def _echo_handler(args: dict[str, Any]) -> dict[str, Any]:
    return {"echo": args}


@pytest.fixture
def echo_tool() -> Tool:
    return Tool(
        name="echo",
        description="Echo the input.",
        args_model=_Args,
        handler=_echo_handler,
    )


def test_to_anthropic_schema_strips_title(echo_tool: Tool) -> None:
    schema = echo_tool.to_anthropic_schema()
    assert schema["name"] == "echo"
    assert schema["description"] == "Echo the input."
    assert schema["input_schema"]["type"] == "object"
    assert "name" in schema["input_schema"]["properties"]
    assert "title" not in schema["input_schema"]


async def test_invoke_validates_arguments(echo_tool: Tool) -> None:
    result = await echo_tool.invoke({"name": "wohnzimmer"})
    assert result == {"echo": {"name": "wohnzimmer", "count": 1}}


async def test_invoke_raises_tool_error_for_missing_field(echo_tool: Tool) -> None:
    with pytest.raises(ToolError, match="invalid arguments"):
        await echo_tool.invoke({})


async def test_invoke_raises_tool_error_for_wrong_type(echo_tool: Tool) -> None:
    with pytest.raises(ToolError, match="invalid arguments"):
        await echo_tool.invoke({"name": "x", "count": "not-an-int"})


async def test_invoke_excludes_none_defaults() -> None:
    """Optional Nones shouldn't show up as `null` in the handler args."""

    class Args(BaseModel):
        a: str
        b: str | None = None

    captured: dict[str, Any] = {}

    async def handler(args: dict[str, Any]) -> None:
        captured.update(args)

    tool = Tool(name="t", description="d", args_model=Args, handler=handler)
    await tool.invoke({"a": "set"})
    assert "b" not in captured


def test_render_tool_result_handles_primitives() -> None:
    assert render_tool_result({"x": 1}) == '{"x": 1}'
    assert render_tool_result([1, 2, 3]) == "[1, 2, 3]"
    assert render_tool_result(None) == "null"
    assert render_tool_result("hello") == '"hello"'


def test_render_tool_result_serializes_pydantic_models() -> None:
    class M(BaseModel):
        x: int
        y: str

    rendered = render_tool_result(M(x=1, y="z"))
    assert json.loads(rendered) == {"x": 1, "y": "z"}


def test_render_tool_result_falls_back_for_unknown_types() -> None:
    class Custom:
        def __init__(self) -> None:
            self.a = 1
            self.b = "two"

    rendered = render_tool_result(Custom())
    assert json.loads(rendered) == {"a": 1, "b": "two"}


def test_requires_confirmation_defaults_false(echo_tool: Tool) -> None:
    assert echo_tool.requires_confirmation is False


def test_requires_confirmation_can_be_set() -> None:
    tool = Tool(
        name="dangerous",
        description="d",
        args_model=_Args,
        handler=_echo_handler,
        requires_confirmation=True,
    )
    assert tool.requires_confirmation is True
