"""Tests for `loxone_voice.adapter.auth`.

Each hash is cross-checked against an independent computation (plain
`hashlib`/`hmac` calls) so we'd notice any drift from the documented
algorithm. The RSA tests round-trip through a freshly generated key
pair — no hardcoded private keys.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac

import pytest
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from loxone_voice.adapter import (
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

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[rsa.RSAPrivateKey, str]:
    """Generate a private RSA key and the Loxone-style PEM of its public key.

    The Miniserver returns the public key wrapped in `BEGIN CERTIFICATE`
    markers (even though the body is a SubjectPublicKeyInfo, not a
    certificate). We mimic that exact shape here so we can verify the
    parser handles the real wire format.
    """
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    loxone_pem = (
        pem_bytes.decode("ascii")
        .replace("-----BEGIN PUBLIC KEY-----", "-----BEGIN CERTIFICATE-----")
        .replace("-----END PUBLIC KEY-----", "-----END CERTIFICATE-----")
    )
    return private, loxone_pem


# ---------------------------------------------------------------------------
# HashAlgorithm
# ---------------------------------------------------------------------------


def test_hash_algorithm_from_miniserver_accepts_documented_forms() -> None:
    assert HashAlgorithm.from_miniserver("SHA1") is HashAlgorithm.SHA1
    assert HashAlgorithm.from_miniserver("sha1") is HashAlgorithm.SHA1
    assert HashAlgorithm.from_miniserver("SHA-1") is HashAlgorithm.SHA1
    assert HashAlgorithm.from_miniserver("SHA256") is HashAlgorithm.SHA256
    assert HashAlgorithm.from_miniserver("SHA-256") is HashAlgorithm.SHA256


def test_hash_algorithm_defaults_to_sha1_when_missing() -> None:
    """Old firmwares omit the field — the doc says assume SHA1."""
    assert HashAlgorithm.from_miniserver("") is HashAlgorithm.SHA1


def test_hash_algorithm_rejects_unknown() -> None:
    with pytest.raises(AuthError, match="unsupported"):
        HashAlgorithm.from_miniserver("MD5")


# ---------------------------------------------------------------------------
# SessionKey
# ---------------------------------------------------------------------------


def test_session_key_payload_format() -> None:
    key = SessionKey(aes_key=bytes(range(32)), aes_iv=bytes(range(16)))
    payload = key.to_loxone_payload()
    assert payload.count(":") == 1
    hex_key, hex_iv = payload.split(":")
    assert hex_key == bytes(range(32)).hex()
    assert hex_iv == bytes(range(16)).hex()


def test_session_key_rejects_wrong_lengths() -> None:
    with pytest.raises(AuthError, match="key"):
        SessionKey(aes_key=b"short", aes_iv=bytes(16))
    with pytest.raises(AuthError, match="IV"):
        SessionKey(aes_key=bytes(32), aes_iv=b"short")


def test_generate_session_key_is_random_and_well_formed() -> None:
    a = generate_session_key()
    b = generate_session_key()
    assert len(a.aes_key) == 32
    assert len(a.aes_iv) == 16
    assert a.aes_key != b.aes_key, "two consecutive keys must differ"
    assert a.aes_iv != b.aes_iv, "two consecutive IVs must differ"


# ---------------------------------------------------------------------------
# Public-key parsing
# ---------------------------------------------------------------------------


def test_parse_miniserver_public_key_accepts_certificate_markers(
    rsa_keypair: tuple[rsa.RSAPrivateKey, str],
) -> None:
    _, loxone_pem = rsa_keypair
    key = parse_miniserver_public_key(loxone_pem)
    assert isinstance(key, rsa.RSAPublicKey)


def test_parse_miniserver_public_key_accepts_native_pubkey_markers(
    rsa_keypair: tuple[rsa.RSAPrivateKey, str],
) -> None:
    private, _ = rsa_keypair
    native_pem = (
        private.public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )
    key = parse_miniserver_public_key(native_pem)
    assert isinstance(key, rsa.RSAPublicKey)


def test_parse_miniserver_public_key_handles_single_line_body(
    rsa_keypair: tuple[rsa.RSAPrivateKey, str],
) -> None:
    """Some firmwares return the PEM as one long line — we must rewrap."""
    _, loxone_pem = rsa_keypair
    flat = loxone_pem.replace("\n", "")
    flat = flat.replace(
        "-----BEGIN CERTIFICATE-----",
        "-----BEGIN CERTIFICATE-----\n",
    ).replace(
        "-----END CERTIFICATE-----",
        "\n-----END CERTIFICATE-----",
    )
    key = parse_miniserver_public_key(flat)
    assert isinstance(key, rsa.RSAPublicKey)


def test_parse_miniserver_public_key_rejects_empty() -> None:
    with pytest.raises(AuthError, match="empty"):
        parse_miniserver_public_key("")
    with pytest.raises(AuthError, match="empty"):
        parse_miniserver_public_key("   ")


def test_parse_miniserver_public_key_rejects_missing_markers() -> None:
    with pytest.raises(AuthError, match="PEM markers"):
        parse_miniserver_public_key("just some text with no markers")


def test_parse_miniserver_public_key_rejects_garbage() -> None:
    pem = (
        "-----BEGIN CERTIFICATE-----\n"
        "this is not actually base64 encoded key material\n"
        "-----END CERTIFICATE-----\n"
    )
    with pytest.raises(AuthError, match="parse"):
        parse_miniserver_public_key(pem)


def test_parse_miniserver_public_key_rejects_unterminated_pem() -> None:
    """BEGIN marker without END falls through normalisation and trips the loader."""
    pem = "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A\n"
    with pytest.raises(AuthError, match="parse"):
        parse_miniserver_public_key(pem)


def test_parse_miniserver_public_key_rejects_non_rsa_key() -> None:
    """A Miniserver MUST send an RSA key — reject anything else loudly."""
    from cryptography.hazmat.primitives.asymmetric import ec

    ec_private = ec.generate_private_key(ec.SECP256R1())
    pem = (
        ec_private.public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )
    with pytest.raises(AuthError, match="not an RSA key"):
        parse_miniserver_public_key(pem)


# ---------------------------------------------------------------------------
# Session-key wrapping (round-trip)
# ---------------------------------------------------------------------------


def test_session_key_round_trips_through_rsa(
    rsa_keypair: tuple[rsa.RSAPrivateKey, str],
) -> None:
    """Encrypt with the Loxone-formatted public key, decrypt with our
    private key, and check the recovered payload matches `to_loxone_payload`."""
    private, loxone_pem = rsa_keypair
    public = parse_miniserver_public_key(loxone_pem)
    session = SessionKey(aes_key=bytes(range(32)), aes_iv=bytes(range(16)))

    wrapped_b64 = encrypt_session_key_for_miniserver(session, public)
    wrapped = base64.b64decode(wrapped_b64)
    recovered = private.decrypt(wrapped, padding.PKCS1v15())

    assert recovered.decode("ascii") == session.to_loxone_payload()


# ---------------------------------------------------------------------------
# AES command encryption
# ---------------------------------------------------------------------------


def test_encrypt_command_round_trips_without_salt() -> None:
    session = generate_session_key()
    plaintext = "jdev/sps/io/0fa3/On"

    encrypted_b64 = encrypt_command(plaintext, session)
    decrypted = _aes_decrypt(encrypted_b64, session)
    assert decrypted == plaintext


def test_encrypt_command_prefixes_salt_when_provided() -> None:
    session = generate_session_key()
    plaintext = "jdev/sys/getkey2/admin"

    encrypted_b64 = encrypt_command(plaintext, session, salt="abcd1234")
    decrypted = _aes_decrypt(encrypted_b64, session)
    assert decrypted == f"salt/abcd1234/{plaintext}"


def _aes_decrypt(ciphertext_b64: str, session: SessionKey) -> str:
    raw = base64.b64decode(ciphertext_b64)
    cipher = Cipher(algorithms.AES(session.aes_key), modes.CBC(session.aes_iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(raw) + decryptor.finalize()
    pad_len = padded[-1]
    return padded[:-pad_len].decode("utf-8")


# ---------------------------------------------------------------------------
# Password and token hashes
# ---------------------------------------------------------------------------


def test_password_hash_matches_independent_sha1() -> None:
    expected = hashlib.sha1(b"hunter2:NaCl").hexdigest().upper()  # noqa: S324  protocol mandates SHA1
    assert compute_password_hash("hunter2", "NaCl", HashAlgorithm.SHA1) == expected


def test_password_hash_matches_independent_sha256() -> None:
    expected = hashlib.sha256(b"hunter2:NaCl").hexdigest().upper()
    assert compute_password_hash("hunter2", "NaCl", HashAlgorithm.SHA256) == expected


def test_gettoken_hash_matches_independent_hmac() -> None:
    key_hex = "DEADBEEF0123456789ABCDEF01234567"
    pw_hash = hashlib.sha256(b"hunter2:NaCl").hexdigest().upper()
    expected = (
        hmac.new(
            bytes.fromhex(key_hex),
            f"admin:{pw_hash}".encode(),
            "sha256",
        )
        .hexdigest()
        .upper()
    )

    assert (
        compute_gettoken_hash(
            username="admin",
            password="hunter2",
            salt="NaCl",
            hmac_key_hex=key_hex,
            algorithm=HashAlgorithm.SHA256,
        )
        == expected
    )


def test_token_auth_hash_matches_independent_hmac() -> None:
    key_hex = "0011223344556677"
    expected = (
        hmac.new(
            bytes.fromhex(key_hex),
            b"my-bearer-token",
            "sha1",
        )
        .hexdigest()
        .upper()
    )

    assert (
        compute_token_auth_hash(
            token="my-bearer-token",
            hmac_key_hex=key_hex,
            algorithm=HashAlgorithm.SHA1,
        )
        == expected
    )


def test_hmac_rejects_non_hex_key() -> None:
    with pytest.raises(AuthError, match="hex"):
        compute_token_auth_hash(
            token="t",
            hmac_key_hex="ZZZ",
            algorithm=HashAlgorithm.SHA1,
        )


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


def test_token_repr_redacts_value() -> None:
    token = Token(
        value="super-secret-token",
        valid_until=dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
        rights=4,
        unsecure_password=False,
        replacement_hmac_key_hex="deadbeef",
    )
    rendered = repr(token)
    assert "super-secret-token" not in rendered
    assert "deadbeef" not in rendered
    assert "redacted" in rendered


def test_token_expiry_with_leeway() -> None:
    valid_until = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.UTC)
    token = Token(value="t", valid_until=valid_until, rights=4, unsecure_password=False)
    before = dt.datetime(2026, 6, 1, 11, 0, tzinfo=dt.UTC)
    just_before = dt.datetime(2026, 6, 1, 11, 59, 50, tzinfo=dt.UTC)
    after = dt.datetime(2026, 6, 1, 12, 1, tzinfo=dt.UTC)

    assert token.is_expired(now=before) is False
    assert token.is_expired(now=after) is True
    # With a 60 s leeway, we count tokens as expired a minute early.
    assert token.is_expired(now=just_before, leeway_s=60) is True


def test_loxone_seconds_conversion() -> None:
    # Loxone epoch starts 2009-01-01 00:00:00 UTC.
    assert loxone_seconds_to_datetime(0) == dt.datetime(2009, 1, 1, tzinfo=dt.UTC)
    # One day later.
    assert loxone_seconds_to_datetime(86_400) == dt.datetime(2009, 1, 2, tzinfo=dt.UTC)


def test_token_is_frozen_and_hashable() -> None:
    token = Token(
        value="x",
        valid_until=dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
        rights=4,
        unsecure_password=False,
    )
    assert {token: 1}[token] == 1
