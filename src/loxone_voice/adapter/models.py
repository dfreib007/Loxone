"""Domain models for the parsed Loxone structure file.

These are intentionally *Loxone-agnostic* in spelling: a `Control` is the
unit of "something Claude can talk about", regardless of whether the
underlying Loxone block is a `Switch`, `Jalousie`, `IRoomController`, etc.
The mapping from raw Loxone type strings to `ControlCategory` happens in
:mod:`loxone_voice.adapter.structure`.

All models are frozen so they're hashable and can safely be shared between
the WebSocket task, the intent engine, and tool implementations without
worrying about mutation races. Mutable runtime data (state values) lives
in :mod:`loxone_voice.adapter.state`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ControlCategory(StrEnum):
    """High-level, voice-friendly category for a Loxone control.

    Derived from the raw Loxone `type` string. Used by the intent engine
    to filter "all lights", "all blinds", etc. without leaking Loxone
    implementation details into the prompt.
    """

    LIGHT = "light"
    BLIND = "blind"
    CLIMATE = "climate"
    SWITCH = "switch"
    SENSOR = "sensor"
    MEDIA = "media"
    SECURITY = "security"
    ENERGY = "energy"
    OTHER = "other"


class Room(BaseModel):
    """A room defined in Loxone Config."""

    model_config = ConfigDict(frozen=True)

    uuid: str = Field(min_length=1)
    name: str = Field(min_length=1)
    default_rating: int = 0


class Category(BaseModel):
    """A Loxone-defined functional category (e.g. *Lights*, *Audio*)."""

    model_config = ConfigDict(frozen=True)

    uuid: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = ""
    is_favorite: bool = False


class Control(BaseModel):
    """A controllable or observable element of the Loxone installation.

    Maps 1:1 to a "control" in the Loxone structure file, except that we
    materialise sub-controls (e.g. moods of a LightControllerV2) as
    siblings with a back-reference to the parent.
    """

    model_config = ConfigDict(frozen=True)

    uuid: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1, description="Raw Loxone control type, e.g. 'Switch'.")
    category: ControlCategory
    room_uuid: str | None = None
    cat_uuid: str | None = None
    state_uuids: dict[str, str] = Field(default_factory=dict)
    parent_uuid: str | None = Field(
        default=None,
        description="UUID of the parent control if this is a sub-control.",
    )
    is_favorite: bool = False


class StructureFile(BaseModel):
    """The parsed `LoxAPP3.json` as a queryable in-memory structure.

    Lookup helpers (`control_by_name`, `controls_in_room`) are O(n) — fine
    for typical installations (~100-1000 controls). If we ever need
    sub-millisecond lookups, add an index in `parse_structure_file`.
    """

    model_config = ConfigDict(frozen=True)

    serial_number: str = ""
    miniserver_name: str = ""
    last_modified: str = ""
    rooms: dict[str, Room] = Field(default_factory=dict)
    categories: dict[str, Category] = Field(default_factory=dict)
    controls: dict[str, Control] = Field(default_factory=dict)

    def control_by_name(self, name: str, *, room_uuid: str | None = None) -> Control | None:
        """Return the first control matching `name` (case-insensitive).

        If `room_uuid` is given, restrict the search to that room. Returns
        `None` if no match — callers must handle the missing case
        explicitly rather than treating it as a generic error.
        """
        needle = name.casefold().strip()
        for control in self.controls.values():
            if control.name.casefold() == needle and (
                room_uuid is None or control.room_uuid == room_uuid
            ):
                return control
        return None

    def controls_in_room(self, room_uuid: str) -> list[Control]:
        """Return all controls assigned to a given room."""
        return [c for c in self.controls.values() if c.room_uuid == room_uuid]

    def controls_by_category(self, category: ControlCategory) -> list[Control]:
        """Return all controls of a given high-level category."""
        return [c for c in self.controls.values() if c.category == category]

    def state_uuid_to_control(self) -> dict[str, str]:
        """Build a reverse map: state-UUID → control-UUID.

        Used by the WebSocket event handler to route a state update back
        to the control that owns it.
        """
        mapping: dict[str, str] = {}
        for control_uuid, control in self.controls.items():
            for state_uuid in control.state_uuids.values():
                mapping[state_uuid] = control_uuid
        return mapping
