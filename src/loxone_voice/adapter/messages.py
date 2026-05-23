"""Loxone WebSocket binary header and event-payload parsers.

Every Loxone WebSocket message arrives as two frames:

1. An **8-byte binary header** (:class:`MessageHeader`) announcing the
   type and length of what follows.
2. A **payload frame** (text or binary) of exactly that length —
   except for keepalive (type 6), which is header-only.

This module deals strictly with the wire format. Higher-level decoding
(JSON-in-text, application semantics) happens in the WebSocket client.

Reference: Loxone "Communicating with the Miniserver" (Config 12+).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

HEADER_LENGTH: Final[int] = 8
UUID_LENGTH: Final[int] = 16
VALUE_EVENT_LENGTH: Final[int] = 24
TEXT_EVENT_HEADER_LENGTH: Final[int] = 36  # state UUID + icon UUID + uint32 text length
_HEADER_MAGIC: Final[int] = 0x03
_ESTIMATED_FLAG: Final[int] = 0x80


class MessageError(ValueError):
    """Raised when a binary frame can't be parsed safely."""


class MessageType(IntEnum):
    """Identifier byte (offset 1) of a Loxone WebSocket message header."""

    TEXT = 0
    BINARY_FILE = 1
    EVENT_VALUES = 2
    EVENT_TEXT = 3
    EVENT_DAYTIMER = 4
    OUT_OF_SERVICE = 5
    KEEPALIVE = 6
    EVENT_WEATHER = 7


@dataclass(frozen=True, slots=True)
class MessageHeader:
    """Decoded 8-byte Loxone message header.

    ``estimated`` indicates the length is approximate (Miniserver couldn't
    determine it precisely before sending the header); the actual frame
    is still framed by the WebSocket layer, so the client should accept
    whatever bytes arrive in the next frame and ignore length deltas
    when this flag is set.
    """

    type: MessageType
    estimated: bool
    length: int

    @classmethod
    def parse(cls, data: bytes) -> MessageHeader:
        if len(data) != HEADER_LENGTH:
            raise MessageError(f"header must be {HEADER_LENGTH} bytes, got {len(data)}")
        if data[0] != _HEADER_MAGIC:
            raise MessageError(f"header must start with 0x{_HEADER_MAGIC:02x}, got 0x{data[0]:02x}")
        try:
            msg_type = MessageType(data[1])
        except ValueError as exc:
            raise MessageError(f"unknown message type 0x{data[1]:02x}") from exc
        estimated = bool(data[2] & _ESTIMATED_FLAG)
        (length,) = struct.unpack("<I", data[4:8])
        return cls(type=msg_type, estimated=estimated, length=length)

    def to_bytes(self) -> bytes:
        info = _ESTIMATED_FLAG if self.estimated else 0x00
        return bytes([_HEADER_MAGIC, int(self.type), info, 0x00]) + struct.pack("<I", self.length)


def format_loxone_uuid(raw: bytes) -> str:
    """Format 16 binary bytes as Loxone's standard UUID string.

    Layout matches the Microsoft GUID encoding (first three fields
    little-endian, last field as opaque bytes):
    ``data1:LE32 data2:LE16 data3:LE16 data4[8]``.
    """
    if len(raw) != UUID_LENGTH:
        raise MessageError(f"UUID must be {UUID_LENGTH} bytes, got {len(raw)}")
    (d1,) = struct.unpack("<I", raw[0:4])
    (d2,) = struct.unpack("<H", raw[4:6])
    (d3,) = struct.unpack("<H", raw[6:8])
    return f"{d1:08x}-{d2:04x}-{d3:04x}-{raw[8:10].hex()}-{raw[10:16].hex()}"


@dataclass(frozen=True, slots=True)
class ValueEvent:
    """A numeric state update for one control state UUID."""

    state_uuid: str
    value: float


@dataclass(frozen=True, slots=True)
class TextEvent:
    """A text state update for one control state UUID."""

    state_uuid: str
    icon_uuid: str
    text: str


def parse_value_events(payload: bytes) -> list[ValueEvent]:
    """Parse an :attr:`MessageType.EVENT_VALUES` payload.

    Each record is exactly :data:`VALUE_EVENT_LENGTH` (24) bytes: a 16-byte
    UUID followed by an 8-byte little-endian IEEE-754 double.
    """
    if len(payload) % VALUE_EVENT_LENGTH != 0:
        raise MessageError(
            f"value-event payload must be a multiple of {VALUE_EVENT_LENGTH} bytes, "
            f"got {len(payload)}"
        )
    events: list[ValueEvent] = []
    for offset in range(0, len(payload), VALUE_EVENT_LENGTH):
        record = payload[offset : offset + VALUE_EVENT_LENGTH]
        uuid = format_loxone_uuid(record[:UUID_LENGTH])
        (value,) = struct.unpack("<d", record[UUID_LENGTH:])
        events.append(ValueEvent(state_uuid=uuid, value=value))
    return events


def parse_text_events(payload: bytes) -> list[TextEvent]:
    """Parse an :attr:`MessageType.EVENT_TEXT` payload.

    Each record contains a state UUID (16 B), an icon UUID (16 B), a
    little-endian uint32 text length, the UTF-8 text, and zero-padding
    so the next record starts at a 4-byte boundary.
    """
    events: list[TextEvent] = []
    offset = 0
    while offset < len(payload):
        if offset + TEXT_EVENT_HEADER_LENGTH > len(payload):
            raise MessageError("truncated text-event record header")
        state_uuid = format_loxone_uuid(payload[offset : offset + UUID_LENGTH])
        icon_uuid = format_loxone_uuid(payload[offset + UUID_LENGTH : offset + 2 * UUID_LENGTH])
        (text_len,) = struct.unpack(
            "<I", payload[offset + 2 * UUID_LENGTH : offset + TEXT_EVENT_HEADER_LENGTH]
        )
        offset += TEXT_EVENT_HEADER_LENGTH
        if offset + text_len > len(payload):
            raise MessageError("text-event text overflows payload")
        text = payload[offset : offset + text_len].decode("utf-8")
        offset += text_len
        # Pad to 4-byte boundary; (-n) % 4 yields 0..3 with the right alignment.
        padding = (-text_len) % 4
        if padding:
            if offset + padding > len(payload):
                raise MessageError("text-event padding overflows payload")
            offset += padding
        events.append(TextEvent(state_uuid=state_uuid, icon_uuid=icon_uuid, text=text))
    return events
