"""Tests for `loxone_voice.intent.tools` factories."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from loxone_voice.adapter import (
    CommandResponse,
    ControlCategory,
    StateStore,
    StructureFile,
    parse_structure_file,
)
from loxone_voice.intent import (
    ToolError,
    build_default_toolset,
    make_get_state_tool,
    make_list_controls_tool,
    make_list_rooms_tool,
    make_set_control_tool,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def structure(loxapp3_minimal: dict[str, Any]) -> StructureFile:
    return parse_structure_file(loxapp3_minimal)


@pytest.fixture
def state_store() -> StateStore:
    return StateStore()


@pytest.fixture
def fake_client() -> AsyncMock:
    """A mock LoxoneClient stub with the methods our tools touch."""
    client = AsyncMock()
    client.send_command = AsyncMock(
        return_value=CommandResponse(control="dev/sps/io/x/On", value="1", code=200)
    )
    return client


# ---------------------------------------------------------------------------
# list_rooms
# ---------------------------------------------------------------------------


async def test_list_rooms_returns_all_rooms(structure: StructureFile) -> None:
    tool = make_list_rooms_tool(structure)
    rooms = await tool.invoke({})
    assert isinstance(rooms, list)
    assert {r["name"] for r in rooms} == {"Wohnzimmer", "Küche", "Schlafzimmer"}
    for r in rooms:
        assert {"id", "name"} <= set(r.keys())


def test_list_rooms_schema_has_no_required_fields(structure: StructureFile) -> None:
    tool = make_list_rooms_tool(structure)
    schema = tool.to_anthropic_schema()
    # _NoArgs has no properties → no `required` array or empty.
    required = schema["input_schema"].get("required", [])
    assert required == []


# ---------------------------------------------------------------------------
# list_controls
# ---------------------------------------------------------------------------


async def test_list_controls_returns_all_when_unfiltered(structure: StructureFile) -> None:
    tool = make_list_controls_tool(structure)
    controls = await tool.invoke({})
    names = {c["name"] for c in controls}
    assert {"Deckenlicht", "Stehlampe", "Rollo Süd"} <= names


async def test_list_controls_filtered_by_room(structure: StructureFile) -> None:
    tool = make_list_controls_tool(structure)
    wohnzimmer = next(r for r in structure.rooms.values() if r.name == "Wohnzimmer")
    controls = await tool.invoke({"room_id": wohnzimmer.uuid})
    assert all(c["room_id"] == wohnzimmer.uuid for c in controls)


async def test_list_controls_filtered_by_category(structure: StructureFile) -> None:
    tool = make_list_controls_tool(structure)
    lights = await tool.invoke({"category": ControlCategory.LIGHT.value})
    assert all(c["category"] == "light" for c in lights)
    assert {c["name"] for c in lights} == {"Stehlampe", "Wohnzimmer-Licht"}


async def test_list_controls_rejects_unknown_category(structure: StructureFile) -> None:
    tool = make_list_controls_tool(structure)
    with pytest.raises(ToolError, match="invalid arguments"):
        await tool.invoke({"category": "nonsense"})


# ---------------------------------------------------------------------------
# get_state
# ---------------------------------------------------------------------------


async def test_get_state_returns_known_state(
    structure: StructureFile, state_store: StateStore
) -> None:
    tool = make_get_state_tool(structure, state_store)
    dimmer = next(c for c in structure.controls.values() if c.name == "Stehlampe")
    position_uuid = dimmer.state_uuids["position"]
    state_store.set_value(position_uuid, 75.0)

    result = await tool.invoke({"control_id": dimmer.uuid})
    assert result["control_id"] == dimmer.uuid
    assert result["name"] == "Stehlampe"
    assert result["states"]["position"]["value"] == 75.0


async def test_get_state_returns_empty_slots_for_unseen_states(
    structure: StructureFile, state_store: StateStore
) -> None:
    tool = make_get_state_tool(structure, state_store)
    dimmer = next(c for c in structure.controls.values() if c.name == "Stehlampe")
    result = await tool.invoke({"control_id": dimmer.uuid})
    assert result["states"]["position"] == {}


async def test_get_state_unknown_control_raises_tool_error(
    structure: StructureFile, state_store: StateStore
) -> None:
    tool = make_get_state_tool(structure, state_store)
    with pytest.raises(ToolError, match="unknown control_id"):
        await tool.invoke({"control_id": "no-such-uuid"})


async def test_get_state_requires_control_id_argument(
    structure: StructureFile, state_store: StateStore
) -> None:
    tool = make_get_state_tool(structure, state_store)
    with pytest.raises(ToolError, match="invalid arguments"):
        await tool.invoke({})


# ---------------------------------------------------------------------------
# set_control
# ---------------------------------------------------------------------------


async def test_set_control_dispatches_command(
    structure: StructureFile, fake_client: AsyncMock
) -> None:
    tool = make_set_control_tool(structure, fake_client)
    deckenlicht = next(c for c in structure.controls.values() if c.name == "Deckenlicht")

    result = await tool.invoke({"control_id": deckenlicht.uuid, "command": "On"})

    fake_client.send_command.assert_awaited_once_with(deckenlicht.uuid, "On", value=None)
    assert result["ok"] is True


async def test_set_control_passes_value_argument(
    structure: StructureFile, fake_client: AsyncMock
) -> None:
    tool = make_set_control_tool(structure, fake_client)
    dimmer = next(c for c in structure.controls.values() if c.name == "Stehlampe")

    await tool.invoke({"control_id": dimmer.uuid, "command": "jumpToValue", "value": 42})
    fake_client.send_command.assert_awaited_once_with(dimmer.uuid, "jumpToValue", value=42)


async def test_set_control_rejects_unknown_control(
    structure: StructureFile, fake_client: AsyncMock
) -> None:
    tool = make_set_control_tool(structure, fake_client)
    with pytest.raises(ToolError, match="unknown control_id"):
        await tool.invoke({"control_id": "no-such-uuid", "command": "On"})
    fake_client.send_command.assert_not_awaited()


async def test_set_control_rejects_empty_command(
    structure: StructureFile, fake_client: AsyncMock
) -> None:
    tool = make_set_control_tool(structure, fake_client)
    deckenlicht = next(c for c in structure.controls.values() if c.name == "Deckenlicht")
    with pytest.raises(ToolError, match="invalid arguments"):
        await tool.invoke({"control_id": deckenlicht.uuid, "command": ""})


def test_set_control_can_require_confirmation(
    structure: StructureFile, fake_client: AsyncMock
) -> None:
    tool = make_set_control_tool(structure, fake_client, requires_confirmation=True)
    assert tool.requires_confirmation is True


# ---------------------------------------------------------------------------
# Default toolset bundle
# ---------------------------------------------------------------------------


def test_build_default_toolset_returns_all_four(
    structure: StructureFile, state_store: StateStore, fake_client: AsyncMock
) -> None:
    tools = build_default_toolset(structure=structure, state_store=state_store, client=fake_client)
    assert {t.name for t in tools} == {
        "list_rooms",
        "list_controls",
        "get_state",
        "set_control",
    }


def test_default_toolset_schemas_are_anthropic_compatible(
    structure: StructureFile, state_store: StateStore, fake_client: AsyncMock
) -> None:
    tools = build_default_toolset(structure=structure, state_store=state_store, client=fake_client)
    for tool in tools:
        schema = tool.to_anthropic_schema()
        assert set(schema) == {"name", "description", "input_schema"}
        # Round-trip through json so we'd catch any unserializable bits.
        json.dumps(schema)
