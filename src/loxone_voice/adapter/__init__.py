"""Loxone Miniserver adapter — protocol, structure parsing, state cache."""

from .auth import (
    AuthError,
    HashAlgorithm,
    SessionKey,
    Token,
    compute_gettoken_hash,
    compute_password_hash,
    compute_token_auth_hash,
    encrypt_command,
    encrypt_session_key_for_miniserver,
    generate_session_key,
    loxone_seconds_to_datetime,
    parse_miniserver_public_key,
)
from .discovery import DiscoveryError, fetch_public_key
from .messages import (
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
from .models import Category, Control, ControlCategory, Room, StructureFile
from .state import StateEntry, StateStore
from .structure import StructureFileError, categorize, parse_structure_file

__all__ = [
    "HEADER_LENGTH",
    "AuthError",
    "Category",
    "Control",
    "ControlCategory",
    "DiscoveryError",
    "HashAlgorithm",
    "MessageError",
    "MessageHeader",
    "MessageType",
    "Room",
    "SessionKey",
    "StateEntry",
    "StateStore",
    "StructureFile",
    "StructureFileError",
    "TextEvent",
    "Token",
    "ValueEvent",
    "categorize",
    "compute_gettoken_hash",
    "compute_password_hash",
    "compute_token_auth_hash",
    "encrypt_command",
    "encrypt_session_key_for_miniserver",
    "fetch_public_key",
    "format_loxone_uuid",
    "generate_session_key",
    "loxone_seconds_to_datetime",
    "parse_miniserver_public_key",
    "parse_structure_file",
    "parse_text_events",
    "parse_value_events",
]
