"""Tests for :class:`loxone_voice.adapter.LoxoneClient`.

We don't run a real WebSocket server here. Instead, an
:class:`InMemoryTransport` is plugged into the client; a tiny
``FakeMiniserver`` helper formats and enqueues the binary header + JSON
payload pairs the client expects after each command, and we assert on
the client's outgoing wire traffic.
"""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from loxone_voice.adapter import (
    AuthenticationError,
    ClientError,
    InMemoryTransport,
    LoxoneClient,
    MessageHeader,
    MessageType,
    ProtocolError,
    TransportClosedError,
    format_loxone_uuid,
)

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fake_public_key_pem() -> str:
    """Generate a real RSA public key once per module, formatted Loxone-style."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = (
        private.public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )
    return pem.replace("-----BEGIN PUBLIC KEY-----", "-----BEGIN CERTIFICATE-----").replace(
        "-----END PUBLIC KEY-----", "-----END CERTIFICATE-----"
    )


class FakeMiniserver:
    """Helper to script Miniserver responses into an :class:`InMemoryTransport`.

    Each call to a ``respond_*`` method enqueues the binary message
    header + payload pair that the client expects to read.
    """

    def __init__(self, transport: InMemoryTransport) -> None:
        self.transport = transport

    # ---- High-level responses ---------------------------------------------

    def respond_text(self, control: str, value: Any, *, code: int = 200) -> None:
        envelope = {"LL": {"control": control, "value": value, "Code": str(code)}}
        self._enqueue_text(json.dumps(envelope))

    def respond_keepalive(self) -> None:
        header = MessageHeader(type=MessageType.KEEPALIVE, estimated=False, length=0)
        self.transport.server_send(header.to_bytes())

    def respond_value_event(self, state_uuid: str, value: float) -> None:
        body = _build_value_event(state_uuid, value)
        header = MessageHeader(type=MessageType.EVENT_VALUES, estimated=False, length=len(body))
        self.transport.server_send(header.to_bytes())
        self.transport.server_send(body)

    def respond_text_event(self, state_uuid: str, icon_uuid: str, text: str) -> None:
        body = _build_text_event(state_uuid, icon_uuid, text)
        header = MessageHeader(type=MessageType.EVENT_TEXT, estimated=False, length=len(body))
        self.transport.server_send(header.to_bytes())
        self.transport.server_send(body)

    # ---- Scripted full handshake ------------------------------------------

    def script_successful_handshake(
        self,
        *,
        user: str = "admin",
        hmac_key_hex: str = "DEADBEEF0011223344",
        salt: str = "saltyseas",
        hash_alg: str = "SHA256",
        token: str = "secret-token",
        valid_until: int = 12_345_678,
    ) -> None:
        """Enqueue the four responses required for a complete handshake."""
        # 1. keyexchange ack
        self.respond_text("dev/sys/keyexchange", "OK")
        # 2. getkey2
        self.respond_text(
            f"dev/sys/getkey2/{user}",
            json.dumps({"key": hmac_key_hex, "salt": salt, "hashAlg": hash_alg}),
        )
        # 3. gettoken
        self.respond_text(
            "dev/sys/gettoken",
            json.dumps(
                {
                    "token": token,
                    "validUntil": valid_until,
                    "tokenRights": 4,
                    "unsecurePass": False,
                }
            ),
        )
        # 4. enable status updates
        self.respond_text("dev/sps/enablebinstatusupdate", "1")

    # ---- Low-level ---------------------------------------------------------

    def _enqueue_text(self, payload_str: str) -> None:
        body = payload_str.encode("utf-8")
        header = MessageHeader(type=MessageType.TEXT, estimated=False, length=len(body))
        self.transport.server_send(header.to_bytes())
        self.transport.server_send(payload_str)


def _uuid_to_bytes(uuid_str: str) -> bytes:
    parts = uuid_str.split("-")
    return (
        struct.pack("<I", int(parts[0], 16))
        + struct.pack("<H", int(parts[1], 16))
        + struct.pack("<H", int(parts[2], 16))
        + bytes.fromhex(parts[3])
        + bytes.fromhex(parts[4])
    )


def _build_value_event(state_uuid: str, value: float) -> bytes:
    return _uuid_to_bytes(state_uuid) + struct.pack("<d", value)


def _build_text_event(state_uuid: str, icon_uuid: str, text: str) -> bytes:
    encoded = text.encode("utf-8")
    record = (
        _uuid_to_bytes(state_uuid)
        + _uuid_to_bytes(icon_uuid)
        + struct.pack("<I", len(encoded))
        + encoded
    )
    record += b"\x00" * ((-len(encoded)) % 4)
    return record


async def _make_connected_client(
    fake_pem: str,
    *,
    user: str = "admin",
    password: str = "hunter2",
    keepalive_interval_s: float = 60.0,
) -> tuple[LoxoneClient, InMemoryTransport, FakeMiniserver]:
    transport = InMemoryTransport()
    miniserver = FakeMiniserver(transport)
    miniserver.script_successful_handshake(user=user)

    client = LoxoneClient(
        user=user,
        password=password,
        fetch_public_key=_fetcher(fake_pem),
        connect_transport=_transport_factory(transport),
        keepalive_interval_s=keepalive_interval_s,
        timeout_s=2.0,
    )
    await client.connect()
    return client, transport, miniserver


def _fetcher(pem: str) -> Callable[[], Awaitable[str]]:
    async def fetch() -> str:
        return pem

    return fetch


def _transport_factory(transport: InMemoryTransport) -> Callable[[], Awaitable[InMemoryTransport]]:
    async def factory() -> InMemoryTransport:
        return transport

    return factory


# ---------------------------------------------------------------------------
# Happy-path handshake
# ---------------------------------------------------------------------------


async def test_connect_runs_full_handshake(fake_public_key_pem: str) -> None:
    client, transport, _ = await _make_connected_client(fake_public_key_pem)
    try:
        assert client.is_connected
        assert client.token is not None
        assert client.token.value == "secret-token"
        assert client.token.rights == 4
        # Four wire messages: keyexchange, getkey2 (encrypted), gettoken (encrypted),
        # enablebinstatusupdate (encrypted).
        assert len(transport.outgoing) == 4
        assert isinstance(transport.outgoing[0], str)
        assert transport.outgoing[0].startswith("jdev/sys/keyexchange/")
        for wire in transport.outgoing[1:]:
            assert isinstance(wire, str)
            assert wire.startswith("jdev/sys/enc/")
    finally:
        await client.close()


async def test_token_repr_never_leaks_value(fake_public_key_pem: str) -> None:
    client, _, _ = await _make_connected_client(fake_public_key_pem)
    try:
        assert "secret-token" not in repr(client.token)
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


async def test_connect_fails_when_getkey2_returns_non_200(fake_public_key_pem: str) -> None:
    transport = InMemoryTransport()
    miniserver = FakeMiniserver(transport)
    miniserver.respond_text("dev/sys/keyexchange", "OK")
    miniserver.respond_text("dev/sys/getkey2/admin", "Unauthorized", code=401)

    client = LoxoneClient(
        user="admin",
        password="wrong",
        fetch_public_key=_fetcher(fake_public_key_pem),
        connect_transport=_transport_factory(transport),
        timeout_s=2.0,
    )
    with pytest.raises(AuthenticationError, match="getkey2"):
        await client.connect()
    assert not client.is_connected


async def test_connect_fails_when_gettoken_returns_non_200(fake_public_key_pem: str) -> None:
    transport = InMemoryTransport()
    miniserver = FakeMiniserver(transport)
    miniserver.respond_text("dev/sys/keyexchange", "OK")
    miniserver.respond_text(
        "dev/sys/getkey2/admin",
        json.dumps({"key": "DEAD", "salt": "s", "hashAlg": "SHA256"}),
    )
    miniserver.respond_text("dev/sys/gettoken", "Unauthorized", code=401)

    client = LoxoneClient(
        user="admin",
        password="wrong",
        fetch_public_key=_fetcher(fake_public_key_pem),
        connect_transport=_transport_factory(transport),
        timeout_s=2.0,
    )
    with pytest.raises(AuthenticationError, match="gettoken"):
        await client.connect()


async def test_connect_fails_on_malformed_getkey2_value(fake_public_key_pem: str) -> None:
    transport = InMemoryTransport()
    miniserver = FakeMiniserver(transport)
    miniserver.respond_text("dev/sys/keyexchange", "OK")
    miniserver.respond_text("dev/sys/getkey2/admin", "not-json")

    client = LoxoneClient(
        user="admin",
        password="hunter2",
        fetch_public_key=_fetcher(fake_public_key_pem),
        connect_transport=_transport_factory(transport),
        timeout_s=2.0,
    )
    with pytest.raises(ProtocolError, match="getkey2"):
        await client.connect()


async def test_connect_fails_on_missing_token_field(fake_public_key_pem: str) -> None:
    transport = InMemoryTransport()
    miniserver = FakeMiniserver(transport)
    miniserver.respond_text("dev/sys/keyexchange", "OK")
    miniserver.respond_text(
        "dev/sys/getkey2/admin",
        json.dumps({"key": "DEAD", "salt": "s", "hashAlg": "SHA256"}),
    )
    miniserver.respond_text("dev/sys/gettoken", json.dumps({"validUntil": 1, "tokenRights": 4}))

    client = LoxoneClient(
        user="admin",
        password="hunter2",
        fetch_public_key=_fetcher(fake_public_key_pem),
        connect_transport=_transport_factory(transport),
        timeout_s=2.0,
    )
    with pytest.raises(ProtocolError, match="gettoken"):
        await client.connect()


# ---------------------------------------------------------------------------
# Commands and event dispatch
# ---------------------------------------------------------------------------


async def test_send_command_returns_parsed_response(fake_public_key_pem: str) -> None:
    client, transport, miniserver = await _make_connected_client(fake_public_key_pem)
    try:
        miniserver.respond_text("dev/sps/io/abc/On", "1")
        result = await client.send_command("abc", "On")
        assert result.ok is True
        assert result.value == "1"
        last = transport.outgoing[-1]
        assert isinstance(last, str)
        assert last.startswith("jdev/sys/enc/")
    finally:
        await client.close()


async def test_send_command_with_value_includes_argument(fake_public_key_pem: str) -> None:
    client, _transport, miniserver = await _make_connected_client(fake_public_key_pem)
    try:
        miniserver.respond_text("dev/sps/io/abc/jumpToValue/42", "1")
        await client.send_command("abc", "jumpToValue", value=42)
    finally:
        await client.close()


async def test_value_events_update_state_store(fake_public_key_pem: str) -> None:
    client, _, miniserver = await _make_connected_client(fake_public_key_pem)
    try:
        uuid = "01234567-89ab-cdef-0011-223344556677"
        miniserver.respond_value_event(uuid, 21.5)
        await _wait_until(lambda: uuid in client.state, timeout_s=1.0)
        entry = client.state.get(uuid)
        assert entry is not None
        assert entry.value == 21.5
    finally:
        await client.close()


async def test_text_events_update_state_store(fake_public_key_pem: str) -> None:
    client, _, miniserver = await _make_connected_client(fake_public_key_pem)
    try:
        uuid = "00000001-0002-0003-0004-000000000005"
        icon = "00000001-0002-0003-0004-000000000006"
        miniserver.respond_text_event(uuid, icon, "online")
        await _wait_until(lambda: uuid in client.state, timeout_s=1.0)
        entry = client.state.get(uuid)
        assert entry is not None
        assert entry.text == "online"
    finally:
        await client.close()


async def test_keepalive_frames_are_ignored_by_receiver(fake_public_key_pem: str) -> None:
    client, _, miniserver = await _make_connected_client(fake_public_key_pem)
    try:
        miniserver.respond_keepalive()
        # Sanity: client should still respond to normal commands afterwards.
        miniserver.respond_text("dev/sps/io/abc/On", "1")
        result = await client.send_command("abc", "On")
        assert result.ok
    finally:
        await client.close()


async def test_send_command_times_out_when_no_response(fake_public_key_pem: str) -> None:
    client, _, _ = await _make_connected_client(fake_public_key_pem)
    client._timeout_s = 0.1
    try:
        with pytest.raises(asyncio.TimeoutError):
            await client.send_command("abc", "On")
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_close_is_idempotent(fake_public_key_pem: str) -> None:
    client, transport, _ = await _make_connected_client(fake_public_key_pem)
    await client.close()
    await client.close()
    assert transport.closed
    assert not client.is_connected


async def test_send_command_after_close_raises(fake_public_key_pem: str) -> None:
    client, _, _ = await _make_connected_client(fake_public_key_pem)
    await client.close()
    with pytest.raises(ClientError):
        await client.send_command("abc", "On")


async def test_server_disconnect_propagates_to_pending_commands(
    fake_public_key_pem: str,
) -> None:
    client, transport, _ = await _make_connected_client(fake_public_key_pem)
    try:
        task = asyncio.create_task(client.send_command("abc", "On"))
        await asyncio.sleep(0)
        transport.server_close()
        with pytest.raises((ClientError, TransportClosedError)):
            await task
    finally:
        await client.close()


async def test_context_manager_protocol(fake_public_key_pem: str) -> None:
    transport = InMemoryTransport()
    miniserver = FakeMiniserver(transport)
    miniserver.script_successful_handshake()

    async with LoxoneClient(
        user="admin",
        password="hunter2",
        fetch_public_key=_fetcher(fake_public_key_pem),
        connect_transport=_transport_factory(transport),
        timeout_s=2.0,
    ) as client:
        assert client.is_connected

    assert transport.closed


# ---------------------------------------------------------------------------
# format_loxone_uuid is used by the test helpers — sanity check
# ---------------------------------------------------------------------------


def test_uuid_round_trip_through_helpers() -> None:
    original = "01234567-89ab-cdef-0011-223344556677"
    assert format_loxone_uuid(_uuid_to_bytes(original)) == original


# ---------------------------------------------------------------------------
# Wait helper
# ---------------------------------------------------------------------------


async def _wait_until(predicate: Callable[[], bool], *, timeout_s: float) -> None:
    """Poll a predicate until it returns truthy, or raise on timeout."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("predicate did not become true within timeout")
