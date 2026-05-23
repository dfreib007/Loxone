"""Parser for the Loxone Miniserver structure file (`LoxAPP3.json`).

The structure file is a snapshot of the configured rooms, categories,
controls, and their state-UUIDs. We parse it into the immutable
:class:`StructureFile` so the rest of the system can reason about the
home without ever touching raw Loxone JSON.

Why a hand-rolled parser instead of `StructureFile(**raw)`?

* Loxone's JSON is *control-type-polymorphic* — different control types
  carry different keys. Pydantic's permissiveness would swallow typos.
* Some keys we want (e.g. `category`) are *derived*, not present in the
  raw file.
* Sub-controls are nested in the input but flattened in our model.

Keep this module deterministic and side-effect-free; integration tests
will run the parser against real exports from production installations.
"""

from __future__ import annotations

from typing import Any

from .models import Category, Control, ControlCategory, Room, StructureFile

_CONTROL_TYPE_TO_CATEGORY: dict[str, ControlCategory] = {
    # Lights
    "Dimmer": ControlCategory.LIGHT,
    "EIBDimmer": ControlCategory.LIGHT,
    "LightController": ControlCategory.LIGHT,
    "LightControllerV2": ControlCategory.LIGHT,
    "CentralLightController": ControlCategory.LIGHT,
    "ColorPickerV2": ControlCategory.LIGHT,
    # Blinds / shading
    "Jalousie": ControlCategory.BLIND,
    "AutomaticBlinds": ControlCategory.BLIND,
    "CentralJalousie": ControlCategory.BLIND,
    "Gate": ControlCategory.BLIND,
    # Climate
    "IRoomController": ControlCategory.CLIMATE,
    "IRoomControllerV2": ControlCategory.CLIMATE,
    "Daytimer": ControlCategory.CLIMATE,
    "ClimateController": ControlCategory.CLIMATE,
    # Generic switching
    "Switch": ControlCategory.SWITCH,
    "Pushbutton": ControlCategory.SWITCH,
    "TimedSwitch": ControlCategory.SWITCH,
    # Sensors / info-only blocks
    "InfoOnlyAnalog": ControlCategory.SENSOR,
    "InfoOnlyDigital": ControlCategory.SENSOR,
    "TextState": ControlCategory.SENSOR,
    "Tracker": ControlCategory.SENSOR,
    # Media
    "AudioZone": ControlCategory.MEDIA,
    "AudioZoneV2": ControlCategory.MEDIA,
    "Radio": ControlCategory.MEDIA,
    # Security
    "Alarm": ControlCategory.SECURITY,
    "WindowMonitor": ControlCategory.SECURITY,
    "AalEmergency": ControlCategory.SECURITY,
    "AalSmartAlarm": ControlCategory.SECURITY,
    # Energy
    "EnergyManager": ControlCategory.ENERGY,
    "Meter": ControlCategory.ENERGY,
}


def categorize(raw_type: str) -> ControlCategory:
    """Map a raw Loxone control-type string to a :class:`ControlCategory`.

    Unknown types fall back to :attr:`ControlCategory.OTHER` — we never
    raise, because we'd rather expose an unfamiliar device with reduced
    capability than crash the whole import.
    """
    return _CONTROL_TYPE_TO_CATEGORY.get(raw_type, ControlCategory.OTHER)


class StructureFileError(ValueError):
    """Raised when the structure file is malformed beyond the point of
    being safely usable (e.g. missing the `controls` root key)."""


def parse_structure_file(data: dict[str, Any]) -> StructureFile:
    """Parse a raw `LoxAPP3.json` payload into a :class:`StructureFile`.

    `data` is the already-deserialized dict from `json.loads(...)`. We
    accept malformed *individual* controls by skipping them with no logs
    yet (logging is wired up in a later phase); we *do* fail loudly if
    the top-level shape is wrong, since that means we'd be operating
    blind.
    """
    if not isinstance(data, dict):
        raise StructureFileError("structure file root must be a JSON object")
    if "controls" not in data:
        raise StructureFileError("structure file missing required key 'controls'")

    ms_info = data.get("msInfo") or {}

    rooms = {uuid: _parse_room(uuid, raw) for uuid, raw in (data.get("rooms") or {}).items()}
    categories = {
        uuid: _parse_category(uuid, raw) for uuid, raw in (data.get("cats") or {}).items()
    }

    controls: dict[str, Control] = {}
    for uuid, raw in data["controls"].items():
        try:
            top = _parse_control(uuid, raw, parent_uuid=None)
        except _SkipControl:
            continue
        controls[top.uuid] = top
        for sub_uuid, sub_raw in (raw.get("subControls") or {}).items():
            try:
                sub = _parse_control(sub_uuid, sub_raw, parent_uuid=top.uuid)
            except _SkipControl:
                continue
            controls[sub.uuid] = sub

    return StructureFile(
        serial_number=str(ms_info.get("serialNr", "")),
        miniserver_name=str(ms_info.get("msName", "")),
        last_modified=str(data.get("lastModified", "")),
        rooms=rooms,
        categories=categories,
        controls=controls,
    )


class _SkipControl(Exception):
    """Internal signal: this control is too malformed to include but the
    rest of the file is fine."""


def _parse_room(uuid: str, raw: dict[str, Any]) -> Room:
    return Room(
        uuid=uuid,
        name=str(raw.get("name") or uuid),
        default_rating=int(raw.get("defaultRating", 0) or 0),
    )


def _parse_category(uuid: str, raw: dict[str, Any]) -> Category:
    return Category(
        uuid=uuid,
        name=str(raw.get("name") or uuid),
        type=str(raw.get("type") or ""),
        is_favorite=bool(raw.get("isFavorite") or False),
    )


def _parse_control(
    uuid: str,
    raw: dict[str, Any],
    *,
    parent_uuid: str | None,
) -> Control:
    name = raw.get("name")
    raw_type = raw.get("type")
    if not isinstance(name, str) or not name.strip():
        raise _SkipControl
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise _SkipControl

    state_uuids_raw = raw.get("states") or {}
    state_uuids: dict[str, str] = {}
    if isinstance(state_uuids_raw, dict):
        for state_name, state_uuid in state_uuids_raw.items():
            if isinstance(state_name, str) and isinstance(state_uuid, str):
                state_uuids[state_name] = state_uuid

    return Control(
        uuid=uuid,
        name=name,
        type=raw_type,
        category=categorize(raw_type),
        room_uuid=raw.get("room") if isinstance(raw.get("room"), str) else None,
        cat_uuid=raw.get("cat") if isinstance(raw.get("cat"), str) else None,
        state_uuids=state_uuids,
        parent_uuid=parent_uuid,
        is_favorite=bool(raw.get("isFavorite") or False),
    )
