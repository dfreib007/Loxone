"""Tests for `loxone_voice.adapter.messages`."""

from __future__ import annotations

import struct

import pytest

from loxone_voice.adapter import (
    HEADER_LENGTH,
    MessageError,
    MessageHeader,
    MessageType,
    TextEvent,
    ValueEvent,
    format_loxone_uuid,
    parse_text_events,
    parse_value_events,
)

# ---------------------------------------------------------------------------
# UUID formatting
# ---------------------------------------------------------------------------


def test_format_uuid_known_vector() -> None:
    """The standard zero UUID round-trips through the documented layout."""
    raw = bytes(16)
    assert format_loxone_uuid(raw) == "00000000-0000-0000-0000-000000000000"


def test_format_uuid_field_layout() -> None:
    """First three fields are little-endian; last 8 bytes are hex-as-is."""
    raw = (
        struct.pack("<I", 0x11223344)
        + struct.pack("<H", 0xAABB)
        + struct.pack("<H", 0xCCDD)
        + bytes.fromhex("0102030405060708")
    )
    assert format_loxone_uuid(raw) == "11223344-aabb-ccdd-0102-030405060708"


def test_format_uuid_wrong_length_raises() -> None:
    with pytest.raises(MessageError, match="UUID"):
        format_loxone_uuid(b"\x00" * 15)


# ---------------------------------------------------------------------------
# MessageHeader
# ---------------------------------------------------------------------------


def _header_bytes(*, msg_type: int, info: int = 0, length: int = 0) -> bytes:
    return bytes([0x03, msg_type, info, 0x00]) + struct.pack("<I", length)


def test_header_parse_keepalive() -> None:
    header = MessageHeader.parse(_header_bytes(msg_type=int(MessageType.KEEPALIVE)))
    assert header.type is MessageType.KEEPALIVE
    assert header.length == 0
    assert header.estimated is False


def test_header_parse_event_values_with_length() -> None:
    header = MessageHeader.parse(_header_bytes(msg_type=2, length=240))
    assert header.type is MessageType.EVENT_VALUES
    assert header.length == 240


def test_header_parse_estimated_flag() -> None:
    header = MessageHeader.parse(_header_bytes(msg_type=0, info=0x80, length=42))
    assert header.estimated is True
    other_info = MessageHeader.parse(_header_bytes(msg_type=0, info=0x00, length=42))
    assert other_info.estimated is False


def test_header_round_trip() -> None:
    original = MessageHeader(type=MessageType.EVENT_TEXT, estimated=True, length=12345)
    assert MessageHeader.parse(original.to_bytes()) == original


def test_header_round_trip_all_types() -> None:
    for msg_type in MessageType:
        original = MessageHeader(type=msg_type, estimated=False, length=0)
        assert MessageHeader.parse(original.to_bytes()) == original


def test_header_rejects_wrong_length() -> None:
    with pytest.raises(MessageError, match="8 bytes"):
        MessageHeader.parse(b"\x03\x00")
    with pytest.raises(MessageError, match="8 bytes"):
        MessageHeader.parse(b"\x03\x00\x00\x00\x00\x00\x00\x00\x00")


def test_header_rejects_bad_magic() -> None:
    bad = bytes([0xFF, 0x00, 0x00, 0x00]) + struct.pack("<I", 0)
    with pytest.raises(MessageError, match="0x03"):
        MessageHeader.parse(bad)


def test_header_rejects_unknown_type() -> None:
    with pytest.raises(MessageError, match="unknown message type"):
        MessageHeader.parse(_header_bytes(msg_type=0xFE))


def test_header_to_bytes_length_matches_header_length() -> None:
    blob = MessageHeader(type=MessageType.TEXT, estimated=False, length=0).to_bytes()
    assert len(blob) == HEADER_LENGTH


# ---------------------------------------------------------------------------
# Value events
# ---------------------------------------------------------------------------


def _value_record(uuid_hex: str, value: float) -> bytes:
    return _uuid_to_bytes(uuid_hex) + struct.pack("<d", value)


def _uuid_to_bytes(uuid_str: str) -> bytes:
    """Inverse of ``format_loxone_uuid`` — for crafting test payloads."""
    parts = uuid_str.split("-")
    assert len(parts) == 5
    return (
        struct.pack("<I", int(parts[0], 16))
        + struct.pack("<H", int(parts[1], 16))
        + struct.pack("<H", int(parts[2], 16))
        + bytes.fromhex(parts[3])
        + bytes.fromhex(parts[4])
    )


def test_parse_value_events_single_record() -> None:
    payload = _value_record("11223344-aabb-ccdd-0102-030405060708", 42.5)
    events = parse_value_events(payload)
    assert events == [ValueEvent(state_uuid="11223344-aabb-ccdd-0102-030405060708", value=42.5)]


def test_parse_value_events_many_records() -> None:
    payload = b"".join(
        _value_record(f"{i:08x}-0000-0000-0000-000000000000", float(i)) for i in range(5)
    )
    events = parse_value_events(payload)
    assert [e.value for e in events] == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_parse_value_events_empty_payload_is_ok() -> None:
    assert parse_value_events(b"") == []


def test_parse_value_events_rejects_misaligned_length() -> None:
    with pytest.raises(MessageError, match="multiple of 24"):
        parse_value_events(b"\x00" * 23)


# ---------------------------------------------------------------------------
# Text events
# ---------------------------------------------------------------------------


def _text_record(state_uuid: str, icon_uuid: str, text: str, *, with_padding: bool = True) -> bytes:
    encoded = text.encode("utf-8")
    record = (
        _uuid_to_bytes(state_uuid)
        + _uuid_to_bytes(icon_uuid)
        + struct.pack("<I", len(encoded))
        + encoded
    )
    if with_padding:
        record += b"\x00" * ((-len(encoded)) % 4)
    return record


_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def test_parse_text_events_single_record() -> None:
    payload = _text_record(_ZERO_UUID, _ZERO_UUID, "hello")
    events = parse_text_events(payload)
    assert events == [TextEvent(state_uuid=_ZERO_UUID, icon_uuid=_ZERO_UUID, text="hello")]


def test_parse_text_events_pads_to_four_byte_boundary() -> None:
    """A 5-byte text needs 3 bytes of padding before the next record starts."""
    payload = _text_record(_ZERO_UUID, _ZERO_UUID, "hello") + _text_record(
        _ZERO_UUID, _ZERO_UUID, "world!"
    )
    events = parse_text_events(payload)
    assert [e.text for e in events] == ["hello", "world!"]


def test_parse_text_events_handles_aligned_lengths() -> None:
    """A 4-byte text needs no padding; consecutive records must still parse."""
    payload = _text_record(_ZERO_UUID, _ZERO_UUID, "abcd") + _text_record(
        _ZERO_UUID, _ZERO_UUID, "wxyz"
    )
    events = parse_text_events(payload)
    assert [e.text for e in events] == ["abcd", "wxyz"]


def test_parse_text_events_empty_text() -> None:
    payload = _text_record(_ZERO_UUID, _ZERO_UUID, "")
    events = parse_text_events(payload)
    assert events == [TextEvent(state_uuid=_ZERO_UUID, icon_uuid=_ZERO_UUID, text="")]


def test_parse_text_events_unicode_text() -> None:
    payload = _text_record(_ZERO_UUID, _ZERO_UUID, "Wohnzimmer · 21°C")
    events = parse_text_events(payload)
    assert events[0].text == "Wohnzimmer · 21°C"


def test_parse_text_events_empty_payload_is_ok() -> None:
    assert parse_text_events(b"") == []


def test_parse_text_events_rejects_truncated_header() -> None:
    with pytest.raises(MessageError, match="truncated text-event record header"):
        parse_text_events(b"\x00" * 35)


def test_parse_text_events_rejects_text_overflowing_payload() -> None:
    # Header claims 100 bytes of text but payload has none beyond the header.
    bad = _uuid_to_bytes(_ZERO_UUID) + _uuid_to_bytes(_ZERO_UUID) + struct.pack("<I", 100)
    with pytest.raises(MessageError, match="text-event text overflows"):
        parse_text_events(bad)


def test_parse_text_events_rejects_padding_overflowing_payload() -> None:
    """Header says 1-byte text (needs 3 bytes padding) but only the text byte exists."""
    bad = _uuid_to_bytes(_ZERO_UUID) + _uuid_to_bytes(_ZERO_UUID) + struct.pack("<I", 1) + b"x"
    with pytest.raises(MessageError, match="padding overflows"):
        parse_text_events(bad)
