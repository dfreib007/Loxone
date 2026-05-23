"""Tests for `loxone_voice.adapter.models`."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from loxone_voice.adapter import Control, ControlCategory, Room, StructureFile


def _ctrl(uuid: str, name: str, **kwargs: Any) -> Control:
    return Control(
        uuid=uuid,
        name=name,
        type=kwargs.pop("type", "Switch"),
        category=kwargs.pop("category", ControlCategory.SWITCH),
        **kwargs,
    )


def test_room_requires_name() -> None:
    with pytest.raises(ValidationError):
        Room(uuid="r1", name="")


def test_models_are_frozen() -> None:
    room = Room(uuid="r1", name="Wohnzimmer")
    with pytest.raises(ValidationError):
        room.name = "Hacked"  # type: ignore[misc]


def test_models_are_hashable() -> None:
    room = Room(uuid="r1", name="Wohnzimmer")
    assert hash(room) == hash(Room(uuid="r1", name="Wohnzimmer"))


def test_structure_lookup_by_name_case_insensitive() -> None:
    s = StructureFile(
        rooms={"r1": Room(uuid="r1", name="Wohnzimmer")},
        controls={
            "c1": _ctrl("c1", "Deckenlicht", room_uuid="r1"),
            "c2": _ctrl("c2", "Stehlampe", room_uuid="r1"),
        },
    )
    assert s.control_by_name("DECKENLICHT") is not None
    assert s.control_by_name("deckenlicht").uuid == "c1"  # type: ignore[union-attr]
    assert s.control_by_name("nope") is None


def test_structure_lookup_by_name_scoped_to_room() -> None:
    s = StructureFile(
        rooms={
            "r1": Room(uuid="r1", name="Wohnzimmer"),
            "r2": Room(uuid="r2", name="Küche"),
        },
        controls={
            "c1": _ctrl("c1", "Licht", room_uuid="r1"),
            "c2": _ctrl("c2", "Licht", room_uuid="r2"),
        },
    )
    assert s.control_by_name("Licht", room_uuid="r1").uuid == "c1"  # type: ignore[union-attr]
    assert s.control_by_name("Licht", room_uuid="r2").uuid == "c2"  # type: ignore[union-attr]


def test_controls_in_room_and_by_category() -> None:
    s = StructureFile(
        rooms={"r1": Room(uuid="r1", name="Wohnzimmer")},
        controls={
            "c1": _ctrl("c1", "Licht", room_uuid="r1", category=ControlCategory.LIGHT),
            "c2": _ctrl("c2", "Steckdose", room_uuid="r1", category=ControlCategory.SWITCH),
            "c3": _ctrl("c3", "Orphan", room_uuid=None, category=ControlCategory.SWITCH),
        },
    )
    in_room = {c.uuid for c in s.controls_in_room("r1")}
    assert in_room == {"c1", "c2"}
    lights = {c.uuid for c in s.controls_by_category(ControlCategory.LIGHT)}
    assert lights == {"c1"}


def test_state_uuid_to_control_reverse_map() -> None:
    s = StructureFile(
        controls={
            "c1": _ctrl("c1", "Licht", state_uuids={"active": "s1", "value": "s2"}),
            "c2": _ctrl("c2", "Lampe", state_uuids={"active": "s3"}),
        },
    )
    assert s.state_uuid_to_control() == {"s1": "c1", "s2": "c1", "s3": "c2"}
