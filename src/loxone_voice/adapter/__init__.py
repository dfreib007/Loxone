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
from .models import Category, Control, ControlCategory, Room, StructureFile
from .state import StateEntry, StateStore
from .structure import StructureFileError, categorize, parse_structure_file

__all__ = [
    "AuthError",
    "Category",
    "Control",
    "ControlCategory",
    "HashAlgorithm",
    "Room",
    "SessionKey",
    "StateEntry",
    "StateStore",
    "StructureFile",
    "StructureFileError",
    "Token",
    "categorize",
    "compute_gettoken_hash",
    "compute_password_hash",
    "compute_token_auth_hash",
    "encrypt_command",
    "encrypt_session_key_for_miniserver",
    "generate_session_key",
    "loxone_seconds_to_datetime",
    "parse_miniserver_public_key",
    "parse_structure_file",
]
