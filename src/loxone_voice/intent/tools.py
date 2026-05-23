"""The Loxone tool catalogue exposed to Claude.

Every factory in this module builds one :class:`Tool` by capturing its
dependencies (typically a :class:`StructureFile`, a :class:`StateStore`,
and optionally a :class:`LoxoneClient` for write tools). The factories
keep tool implementations small and self-contained — each handler is a
single closure under 30 lines.

Use :func:`build_default_toolset` to get the standard read + write
tools wired up against a connected client.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from loxone_voice.adapter import (
    ControlCategory,
    LoxoneClient,
    StateStore,
    StructureFile,
)

from .tool import Tool, ToolError

# ---------------------------------------------------------------------------
# Arg models — one per tool, generates the JSON schema Claude sees
# ---------------------------------------------------------------------------


class _NoArgs(BaseModel):
    """Marker for tools that take no arguments."""


class ListControlsArgs(BaseModel):
    room_id: str | None = Field(
        default=None, description="If set, restrict to controls in this room UUID."
    )
    category: ControlCategory | None = Field(
        default=None, description="Filter by high-level category (light, blind, …)."
    )


class GetStateArgs(BaseModel):
    control_id: str = Field(description="Control UUID returned by `list_controls` or `list_rooms`.")


class SetControlArgs(BaseModel):
    control_id: str = Field(description="Control UUID returned by `list_controls`.")
    command: str = Field(
        description="Loxone command, e.g. 'On', 'Off', 'Pulse', 'jumpToValue'.",
        min_length=1,
    )
    value: float | None = Field(
        default=None,
        description="Optional numeric argument for the command (e.g. brightness 0-100).",
    )


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def make_list_rooms_tool(structure: StructureFile) -> Tool:
    async def handler(_: dict[str, Any]) -> list[dict[str, str]]:
        return [{"id": r.uuid, "name": r.name} for r in structure.rooms.values()]

    return Tool(
        name="list_rooms",
        description="Lists every room defined in the Loxone configuration.",
        args_model=_NoArgs,
        handler=handler,
    )


def make_list_controls_tool(structure: StructureFile) -> Tool:
    async def handler(args: dict[str, Any]) -> list[dict[str, str]]:
        room_id = args.get("room_id")
        category_raw = args.get("category")
        category = ControlCategory(category_raw) if category_raw is not None else None

        controls = list(structure.controls.values())
        if room_id is not None:
            controls = [c for c in controls if c.room_uuid == room_id]
        if category is not None:
            controls = [c for c in controls if c.category is category]
        return [
            {
                "id": c.uuid,
                "name": c.name,
                "type": c.type,
                "category": c.category.value,
                "room_id": c.room_uuid or "",
            }
            for c in controls
        ]

    return Tool(
        name="list_controls",
        description=(
            "Lists controllable devices. Optionally filter by room UUID (`room_id`) "
            "and/or category (`light`, `blind`, `climate`, `switch`, `sensor`, "
            "`media`, `security`, `energy`, `other`)."
        ),
        args_model=ListControlsArgs,
        handler=handler,
    )


def make_get_state_tool(
    structure: StructureFile,
    state_store: StateStore,
) -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        control_id = args["control_id"]
        control = structure.controls.get(control_id)
        if control is None:
            raise ToolError(f"unknown control_id: {control_id}")
        states: dict[str, dict[str, Any]] = {}
        for state_name, state_uuid in control.state_uuids.items():
            entry = state_store.get(state_uuid)
            states[state_name] = (
                {"value": entry.value, "text": entry.text} if entry is not None else {}
            )
        return {
            "control_id": control.uuid,
            "name": control.name,
            "type": control.type,
            "states": states,
        }

    return Tool(
        name="get_state",
        description=(
            "Returns the latest known state of a control: all state slots "
            "(value + text) that have been seen on the WebSocket event stream."
        ),
        args_model=GetStateArgs,
        handler=handler,
    )


def make_set_control_tool(
    structure: StructureFile,
    client: LoxoneClient,
    *,
    requires_confirmation: bool = False,
) -> Tool:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        control_id = args["control_id"]
        command = args["command"]
        value = args.get("value")
        if control_id not in structure.controls:
            raise ToolError(f"unknown control_id: {control_id}")
        response = await client.send_command(control_id, command, value=value)
        return {
            "ok": response.ok,
            "code": response.code,
            "control": response.control,
            "value": response.value,
        }

    return Tool(
        name="set_control",
        description=(
            "Sends a command to a Loxone control. `command` is the Loxone-level "
            "verb (`On`, `Off`, `Pulse`, `jumpToValue`, ...). `value` is required "
            "for commands that take a parameter."
        ),
        args_model=SetControlArgs,
        handler=handler,
        requires_confirmation=requires_confirmation,
    )


# ---------------------------------------------------------------------------
# Default toolset
# ---------------------------------------------------------------------------


def build_default_toolset(
    *,
    structure: StructureFile,
    state_store: StateStore,
    client: LoxoneClient,
) -> Sequence[Tool]:
    """Return the standard read + write tools as a single list.

    The intent engine should hand this directly to Claude's tool-use API.
    """
    return (
        make_list_rooms_tool(structure),
        make_list_controls_tool(structure),
        make_get_state_tool(structure, state_store),
        make_set_control_tool(structure, client),
    )
