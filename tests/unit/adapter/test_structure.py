"""Tests for `loxone_voice.adapter.structure`."""

from __future__ import annotations

from typing import Any

import pytest

from loxone_voice.adapter import ControlCategory, parse_structure_file
from loxone_voice.adapter.structure import (
    StructureFileError,
    categorize,
)


def test_categorize_known_types() -> None:
    assert categorize("Switch") is ControlCategory.SWITCH
    assert categorize("Dimmer") is ControlCategory.LIGHT
    assert categorize("LightControllerV2") is ControlCategory.LIGHT
    assert categorize("Jalousie") is ControlCategory.BLIND
    assert categorize("IRoomControllerV2") is ControlCategory.CLIMATE
    assert categorize("InfoOnlyAnalog") is ControlCategory.SENSOR
    assert categorize("WindowMonitor") is ControlCategory.SECURITY
    assert categorize("Meter") is ControlCategory.ENERGY
    assert categorize("AudioZoneV2") is ControlCategory.MEDIA


def test_categorize_unknown_type_falls_back_to_other() -> None:
    assert categorize("ThisDoesNotExist") is ControlCategory.OTHER
    assert categorize("") is ControlCategory.OTHER


def test_parse_minimal_fixture(loxapp3_minimal: dict[str, Any]) -> None:
    s = parse_structure_file(loxapp3_minimal)
    assert s.serial_number == "504F94A0F00C"
    assert s.miniserver_name == "Test-Miniserver"
    assert s.last_modified == "2026-05-23 12:34:56"
    assert len(s.rooms) == 3
    assert len(s.categories) == 3


def test_room_names_parsed(loxapp3_minimal: dict[str, Any]) -> None:
    s = parse_structure_file(loxapp3_minimal)
    names = {r.name for r in s.rooms.values()}
    assert names == {"Wohnzimmer", "Küche", "Schlafzimmer"}


def test_control_categories_derived(loxapp3_minimal: dict[str, Any]) -> None:
    s = parse_structure_file(loxapp3_minimal)
    by_name = {c.name: c for c in s.controls.values()}
    assert by_name["Deckenlicht"].category is ControlCategory.SWITCH
    assert by_name["Stehlampe"].category is ControlCategory.LIGHT
    assert by_name["Rollo Süd"].category is ControlCategory.BLIND
    assert by_name["Heizung Wohnzimmer"].category is ControlCategory.CLIMATE
    assert by_name["Temperatur Küche"].category is ControlCategory.SENSOR
    assert by_name["Fenster Schlafzimmer"].category is ControlCategory.SECURITY


def test_unknown_control_type_becomes_other(loxapp3_minimal: dict[str, Any]) -> None:
    s = parse_structure_file(loxapp3_minimal)
    mystery = next(c for c in s.controls.values() if c.name == "Mystery-Box")
    assert mystery.category is ControlCategory.OTHER
    assert mystery.type == "FutureControlType9000"


def test_malformed_controls_are_skipped(loxapp3_minimal: dict[str, Any]) -> None:
    s = parse_structure_file(loxapp3_minimal)
    names = {c.name for c in s.controls.values() if c.parent_uuid is None}
    # The fixture has a control with empty name — must be skipped silently.
    assert "" not in names


def test_sub_controls_are_flattened_with_parent_reference(
    loxapp3_minimal: dict[str, Any],
) -> None:
    s = parse_structure_file(loxapp3_minimal)
    moods = [c for c in s.controls.values() if c.parent_uuid is not None]
    assert {m.name for m in moods} == {"Mood Abend", "Mood Lesen"}
    parent_uuid = next(c.uuid for c in s.controls.values() if c.name == "Wohnzimmer-Licht")
    assert all(m.parent_uuid == parent_uuid for m in moods)


def test_state_uuids_captured(loxapp3_minimal: dict[str, Any]) -> None:
    s = parse_structure_file(loxapp3_minimal)
    dimmer = next(c for c in s.controls.values() if c.name == "Stehlampe")
    assert dimmer.state_uuids["position"] == "44444444-0002-0002-0000-000000000000"
    assert "min" in dimmer.state_uuids
    assert "max" in dimmer.state_uuids


def test_orphan_control_has_no_room(loxapp3_minimal: dict[str, Any]) -> None:
    s = parse_structure_file(loxapp3_minimal)
    orphan = next(c for c in s.controls.values() if c.name == "Orphan Switch")
    assert orphan.room_uuid is None


def test_favorite_flag_carried_through(loxapp3_minimal: dict[str, Any]) -> None:
    s = parse_structure_file(loxapp3_minimal)
    decke = next(c for c in s.controls.values() if c.name == "Deckenlicht")
    assert decke.is_favorite is True


def test_missing_controls_key_raises() -> None:
    with pytest.raises(StructureFileError, match="controls"):
        parse_structure_file({"rooms": {}})


def test_non_dict_root_raises() -> None:
    with pytest.raises(StructureFileError, match="root"):
        parse_structure_file([])  # type: ignore[arg-type]


def test_empty_rooms_and_cats_default_to_empty() -> None:
    s = parse_structure_file({"controls": {}})
    assert s.rooms == {}
    assert s.categories == {}
    assert s.controls == {}


def test_reverse_state_map_excludes_skipped_controls(
    loxapp3_minimal: dict[str, Any],
) -> None:
    s = parse_structure_file(loxapp3_minimal)
    reverse = s.state_uuid_to_control()
    for state_uuid, control_uuid in reverse.items():
        assert control_uuid in s.controls, f"reverse map pointed to missing control {state_uuid}"


def test_control_with_missing_type_is_skipped() -> None:
    s = parse_structure_file(
        {
            "controls": {
                "c1": {"name": "TypeMissing", "states": {}},
                "c2": {"name": "TypeEmpty", "type": "", "states": {}},
                "c3": {"name": "TypeNonString", "type": 42, "states": {}},
                "c4": {"name": "Ok", "type": "Switch", "states": {}},
            }
        }
    )
    assert {c.name for c in s.controls.values()} == {"Ok"}


def test_sub_control_with_invalid_name_is_skipped() -> None:
    s = parse_structure_file(
        {
            "controls": {
                "parent": {
                    "name": "Parent",
                    "type": "LightControllerV2",
                    "states": {},
                    "subControls": {
                        "broken": {"name": "", "type": "Switch", "states": {}},
                        "ok": {"name": "Mood", "type": "Switch", "states": {}},
                    },
                }
            }
        }
    )
    names = {c.name for c in s.controls.values()}
    assert names == {"Parent", "Mood"}


def test_non_string_state_uuids_filtered_out() -> None:
    s = parse_structure_file(
        {
            "controls": {
                "c1": {
                    "name": "Mixed",
                    "type": "Switch",
                    "states": {
                        "good": "uuid-1",
                        "bad-value": 42,
                        99: "uuid-2",
                    },
                }
            }
        }
    )
    control = next(iter(s.controls.values()))
    assert control.state_uuids == {"good": "uuid-1"}


def test_states_field_missing_or_wrong_type_yields_empty_map() -> None:
    s = parse_structure_file(
        {
            "controls": {
                "c1": {"name": "NoStates", "type": "Switch"},
                "c2": {"name": "WeirdStates", "type": "Switch", "states": "not-a-dict"},
            }
        }
    )
    for control in s.controls.values():
        assert control.state_uuids == {}
