"""Loxone Miniserver token-authentication primitives.

This module contains the **pure** parts of the Loxone authentication
handshake: key parsing, session-key generation, RSA/AES helpers, and the
HMAC-based password and token hashes. Everything here is side-effect-free
and synchronous so it can be unit-tested without a real Miniserver.

The actual HTTP / WebSocket transport that *drives* these primitives
lives in :mod:`loxone_voice.adapter.client` (Phase 1b2).

Protocol reference: Loxone "Communicating with the Miniserver" Config 12+.

Security notes
--------------
* Loxone requires **RSA/ECB/PKCS1Padding** (PKCS#1 v1.5) to wrap the
  session key. PKCS#1 v1.5 is theoretically vulnerable to Bleichenbacher
  attacks, but the protocol mandates it and we can't change that.
* The user's `password` and the issued `token` are equivalent in power.
  They MUST never appear in logs, audit records, or `__repr__` output.
  The :class:`Token` model intentionally exposes only redacted reprs.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_pem_public_key

# Loxone counts seconds from 2009-01-01 00:00:00 UTC for token expiry.
_LOXONE_EPOCH: Final[dt.datetime] = dt.datetime(2009, 1, 1, tzinfo=dt.UTC)

_PEM_CERT_BEGIN: Final[str] = "-----BEGIN CERTIFICATE-----"
_PEM_CERT_END: Final[str] = "-----END CERTIFICATE-----"
_PEM_PUBKEY_BEGIN: Final[str] = "-----BEGIN PUBLIC KEY-----"
_PEM_PUBKEY_END: Final[str] = "-----END PUBLIC KEY-----"


class AuthError(ValueError):
    """Raised when authentication input is malformed or a hash mismatches."""


class HashAlgorithm(StrEnum):
    """Hash algorithm advertised by the Miniserver via `getkey2`.

    Older firmwares advertise SHA1, newer firmwares default to SHA256.
    The value comes from the Miniserver — we don't pick it.
    """

    SHA1 = "SHA1"
    SHA256 = "SHA256"

    @classmethod
    def from_miniserver(cls, raw: str) -> HashAlgorithm:
        """Parse the `hashAlg` field of a `getkey2` response, defaulting to SHA1.

        Loxone firmwares prior to ~10.0 omit the field entirely; in that
        case the doc says to assume SHA1.
        """
        if not raw:
            return cls.SHA1
        normalized = raw.strip().upper().replace("-", "")
        if normalized in {"SHA1"}:
            return cls.SHA1
        if normalized in {"SHA256"}:
            return cls.SHA256
        raise AuthError(f"unsupported Miniserver hash algorithm: {raw!r}")

    @property
    def _hashlib_name(self) -> str:
        return "sha1" if self is HashAlgorithm.SHA1 else "sha256"


@dataclass(frozen=True, slots=True)
class SessionKey:
    """AES-256-CBC session key negotiated at the start of a WS connection.

    The key is sent to the Miniserver wrapped in its RSA public key; every
    subsequent command on this connection is AES-encrypted with it.
    """

    aes_key: bytes
    aes_iv: bytes

    def __post_init__(self) -> None:
        if len(self.aes_key) != 32:
            raise AuthError(f"AES key must be 32 bytes, got {len(self.aes_key)}")
        if len(self.aes_iv) != 16:
            raise AuthError(f"AES IV must be 16 bytes, got {len(self.aes_iv)}")

    def to_loxone_payload(self) -> str:
        """Format as `hex(key):hex(iv)` — exactly what the Miniserver expects."""
        return f"{self.aes_key.hex()}:{self.aes_iv.hex()}"


def generate_session_key() -> SessionKey:
    """Return a fresh, cryptographically random AES-256 session key + IV."""
    return SessionKey(aes_key=secrets.token_bytes(32), aes_iv=secrets.token_bytes(16))


@dataclass(frozen=True, slots=True)
class Token:
    """A Miniserver-issued auth token.

    `value` is the bearer secret — treat it like a password. The
    `__repr__` is redacted so the token never accidentally leaks into a
    log line or traceback.
    """

    value: str
    valid_until: dt.datetime
    rights: int
    unsecure_password: bool
    replacement_hmac_key_hex: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return (
            f"Token(value=***redacted***, valid_until={self.valid_until.isoformat()}, "
            f"rights={self.rights}, unsecure_password={self.unsecure_password})"
        )

    def is_expired(self, *, now: dt.datetime | None = None, leeway_s: int = 0) -> bool:
        """Whether the token has expired (optionally with a safety leeway)."""
        current = now or dt.datetime.now(tz=dt.UTC)
        return current >= (self.valid_until - dt.timedelta(seconds=leeway_s))


def loxone_seconds_to_datetime(seconds: int) -> dt.datetime:
    """Convert a Loxone-epoch second count to a timezone-aware datetime."""
    return _LOXONE_EPOCH + dt.timedelta(seconds=seconds)


def parse_miniserver_public_key(pem: str) -> rsa.RSAPublicKey:
    """Parse the Miniserver's `getPublicKey` response into an RSA public key.

    Loxone wraps a `SubjectPublicKeyInfo` in `BEGIN CERTIFICATE` / `END
    CERTIFICATE` markers even though the body isn't a full X.509
    certificate. We rewrite the markers before handing it to the
    standard PEM loader. The Miniserver also serialises the body as a
    single line — we normalise whitespace before parsing.
    """
    if not isinstance(pem, str) or not pem.strip():
        raise AuthError("public key payload is empty")
    body = pem.strip()
    if _PEM_CERT_BEGIN in body and _PEM_CERT_END in body:
        body = body.replace(_PEM_CERT_BEGIN, _PEM_PUBKEY_BEGIN).replace(
            _PEM_CERT_END, _PEM_PUBKEY_END
        )
    elif _PEM_PUBKEY_BEGIN not in body:
        raise AuthError("public key payload lacks PEM markers")

    body = _normalize_pem(body)
    try:
        key = load_pem_public_key(body.encode("ascii"))
    except Exception as exc:
        raise AuthError("failed to parse Miniserver public key") from exc
    if not isinstance(key, rsa.RSAPublicKey):
        raise AuthError("Miniserver public key is not an RSA key")
    return key


def _normalize_pem(pem: str) -> str:
    """Rewrap a PEM body into 64-char lines (some Miniservers send it flat).

    If the input doesn't match the canonical BEGIN/END pattern, we return
    it untouched; the downstream PEM loader will then raise and we wrap
    that error in `AuthError`.
    """
    match = re.match(
        rf"^{re.escape(_PEM_PUBKEY_BEGIN)}\s*(?P<body>.*?)\s*{re.escape(_PEM_PUBKEY_END)}\s*$",
        pem,
        flags=re.DOTALL,
    )
    if match is None:
        return pem
    body = re.sub(r"\s+", "", match.group("body"))
    chunks = [body[i : i + 64] for i in range(0, len(body), 64)]
    return "\n".join([_PEM_PUBKEY_BEGIN, *chunks, _PEM_PUBKEY_END]) + "\n"


def encrypt_session_key_for_miniserver(
    session_key: SessionKey,
    miniserver_public_key: rsa.RSAPublicKey,
) -> str:
    """Wrap the session key in the Miniserver's public RSA key (PKCS#1 v1.5).

    Returns the base64-encoded ciphertext, ready to be appended to the
    `jdev/sys/keyexchange/...` command.
    """
    payload = session_key.to_loxone_payload().encode("ascii")
    ciphertext = miniserver_public_key.encrypt(payload, padding.PKCS1v15())
    return base64.b64encode(ciphertext).decode("ascii")


def encrypt_command(plaintext: str, session_key: SessionKey, *, salt: str = "") -> str:
    """AES-256-CBC encrypt a command for the `jdev/sys/enc/...` envelope.

    Loxone prefixes the plaintext with `salt/<salt>/` to mix in a value
    that changes per request, then PKCS#7-pads to 16-byte blocks. The
    result is base64-encoded.

    `salt` is optional — for the very first command after a keyexchange
    the docs allow an empty salt; later commands MUST set one to prevent
    replay. The transport layer is responsible for picking and rotating
    salts; this function just encodes whatever it's given.
    """
    framed = f"salt/{salt}/{plaintext}" if salt else plaintext
    body = framed.encode("utf-8")
    padded = _pkcs7_pad(body, block_size=16)
    cipher = Cipher(algorithms.AES(session_key.aes_key), modes.CBC(session_key.aes_iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("ascii")


def _pkcs7_pad(data: bytes, *, block_size: int) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len]) * pad_len


def compute_password_hash(password: str, salt: str, algorithm: HashAlgorithm) -> str:
    """Compute the per-user password hash sent during `gettoken`.

    The Miniserver compares this against `HASH(stored_password + ":" + salt)`
    where `salt` is the per-user salt returned by `getkey2`. Hash output
    is uppercased hex.
    """
    hasher = hashlib.new(algorithm._hashlib_name)
    hasher.update(f"{password}:{salt}".encode())
    return hasher.hexdigest().upper()


def compute_gettoken_hash(
    *,
    username: str,
    password: str,
    salt: str,
    hmac_key_hex: str,
    algorithm: HashAlgorithm,
) -> str:
    """Compute the `hash` parameter for `jdev/sys/gettoken`.

    Algorithm:

    1. `pw_hash = HASH(password + ":" + salt)` (uppercase hex)
    2. `result  = HMAC-HASH(key=hex_decode(hmac_key_hex), msg=username + ":" + pw_hash)`
    3. Return uppercase hex of the HMAC output.
    """
    pw_hash = compute_password_hash(password, salt, algorithm)
    return _hmac_hex(
        key_hex=hmac_key_hex,
        message=f"{username}:{pw_hash}",
        algorithm=algorithm,
    )


def compute_token_auth_hash(
    *,
    token: str,
    hmac_key_hex: str,
    algorithm: HashAlgorithm,
) -> str:
    """Compute the hash used to authenticate with an *existing* token.

    Sent as `authwithtoken/<hash>/<user>` after the session key exchange.
    """
    return _hmac_hex(
        key_hex=hmac_key_hex,
        message=token,
        algorithm=algorithm,
    )


def _hmac_hex(*, key_hex: str, message: str, algorithm: HashAlgorithm) -> str:
    try:
        key_bytes = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise AuthError("HMAC key from Miniserver is not valid hex") from exc
    digest = hmac.new(key_bytes, message.encode("utf-8"), algorithm._hashlib_name).hexdigest()
    return digest.upper()
