"""High-level Loxone WebSocket client.

Wires the adapter sub-modules into a usable interface:

* HTTP discovery (:mod:`discovery`) for the Miniserver's public RSA key
* Crypto primitives (:mod:`auth`) for session-key wrap, command encryption,
  and token negotiation
* Binary message parsing (:mod:`messages`) for header and event payloads
* :class:`StateStore` (:mod:`state`) for the latest-value cache

The actual transport is injected via the :class:`WsTransport` protocol
so unit tests can substitute an in-memory fake.

What this client does
---------------------
1. Open a transport (caller-supplied factory)
2. Exchange a session key via ``jdev/sys/keyexchange``
3. Run the token flow: ``getkey2`` → ``gettoken``
4. Enable binary status updates
5. Loop: receive header → receive payload → either dispatch as an event
   into the state store or correlate as a command response
6. Send keepalives every 240 s

What it does **not** do yet
---------------------------
* Reconnect on disconnect — that's wrapped at a higher layer
* Persist tokens between runs — always negotiates a fresh one
* Salt rotation on encrypted commands — first iteration uses empty salt
* Daytimer / weather event payloads
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from ._transport import TransportClosedError, WsTransport
from .auth import (
    HashAlgorithm,
    SessionKey,
    Token,
    compute_gettoken_hash,
    encrypt_command,
    encrypt_session_key_for_miniserver,
    generate_session_key,
    loxone_seconds_to_datetime,
    parse_miniserver_public_key,
)
from .messages import (
    HEADER_LENGTH,
    MessageError,
    MessageHeader,
    MessageType,
    parse_text_events,
    parse_value_events,
)
from .state import StateStore

logger = logging.getLogger(__name__)

TOKEN_PERMISSION_APP: Final[int] = 4
KEEPALIVE_INTERVAL_S: Final[float] = 240.0
DEFAULT_TIMEOUT_S: Final[float] = 10.0


class ClientError(RuntimeError):
    """Base class for protocol / lifecycle errors from :class:`LoxoneClient`."""


class ProtocolError(ClientError):
    """The Miniserver returned an unexpected response."""


class AuthenticationError(ClientError):
    """The Miniserver refused our credentials."""


class NotConnectedError(ClientError):
    """A command was attempted on a client that isn't connected."""


@dataclass(frozen=True, slots=True)
class CommandResponse:
    """Parsed Miniserver response to a single text command."""

    control: str
    value: str
    code: int

    @property
    def ok(self) -> bool:
        return self.code == 200


# Factory signatures kept narrow so the test transport plugs in without ceremony.
PublicKeyFetcher = Callable[[], Awaitable[str]]
TransportFactory = Callable[[], Awaitable[WsTransport]]


class LoxoneClient:
    """Drives one Loxone Miniserver WebSocket session.

    Use as an async context manager — :meth:`__aenter__` performs the
    handshake, :meth:`__aexit__` shuts down cleanly:

        async with LoxoneClient(...) as client:
            await client.send_command(uuid, "On")
    """

    def __init__(
        self,
        *,
        user: str,
        password: str,
        fetch_public_key: PublicKeyFetcher,
        connect_transport: TransportFactory,
        state_store: StateStore | None = None,
        client_uuid: str | None = None,
        client_info: str = "loxone-voice",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        keepalive_interval_s: float = KEEPALIVE_INTERVAL_S,
    ) -> None:
        self._user = user
        self._password = password
        self._fetch_public_key = fetch_public_key
        self._connect_transport = connect_transport
        self._state = state_store or StateStore()
        self._client_uuid = client_uuid or self._generate_client_uuid()
        self._client_info = client_info
        self._timeout_s = timeout_s
        self._keepalive_interval_s = keepalive_interval_s

        self._transport: WsTransport | None = None
        self._session_key: SessionKey | None = None
        self._token: Token | None = None
        self._hmac_key_hex: str | None = None
        self._hash_algorithm: HashAlgorithm | None = None
        self._receiver_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[CommandResponse]] = {}
        self._orphans: list[CommandResponse] = []
        self._closing = False

    # ---- Properties --------------------------------------------------------

    @property
    def state(self) -> StateStore:
        return self._state

    @property
    def token(self) -> Token | None:
        return self._token

    @property
    def is_connected(self) -> bool:
        return self._transport is not None and not self._transport.closed

    # ---- Public lifecycle --------------------------------------------------

    async def __aenter__(self) -> LoxoneClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def connect(self) -> None:
        """Run the full handshake. Raises on any unrecoverable failure."""
        public_key_pem = await self._fetch_public_key()
        public_key = parse_miniserver_public_key(public_key_pem)

        self._transport = await self._connect_transport()
        self._receiver_task = asyncio.create_task(self._receive_loop(), name="loxone-client-recv")

        try:
            self._session_key = generate_session_key()
            wrapped = encrypt_session_key_for_miniserver(self._session_key, public_key)
            await self._exchange("jdev/sys/keyexchange/" + wrapped, encrypted=False)
            await self._acquire_token()
            await self._send_encrypted("jdev/sps/enablebinstatusupdate")
        except BaseException:
            await self._teardown()
            raise

        self._keepalive_task = asyncio.create_task(
            self._keepalive_loop(), name="loxone-client-keepalive"
        )

    async def close(self) -> None:
        await self._teardown()

    # ---- Public commands ---------------------------------------------------

    async def send_command(
        self,
        control_uuid: str,
        command: str,
        *,
        value: float | int | None = None,
    ) -> CommandResponse:
        """Send a command to a Loxone control and await its response."""
        if value is not None:
            path = f"jdev/sps/io/{control_uuid}/{command}/{value}"
        else:
            path = f"jdev/sps/io/{control_uuid}/{command}"
        return await self._send_encrypted(path)

    # ---- Internals: handshake ---------------------------------------------

    async def _acquire_token(self) -> None:
        getkey2 = await self._send_encrypted(f"jdev/sys/getkey2/{self._user}")
        if not getkey2.ok:
            raise AuthenticationError(f"getkey2 failed with code {getkey2.code}")
        params = self._parse_json_value(getkey2, hint="getkey2")

        try:
            self._hmac_key_hex = str(params["key"])
            salt = str(params["salt"])
        except KeyError as exc:
            raise ProtocolError(f"getkey2 payload missing field: {exc}") from exc
        self._hash_algorithm = HashAlgorithm.from_miniserver(str(params.get("hashAlg", "")))

        gettoken_hash = compute_gettoken_hash(
            username=self._user,
            password=self._password,
            salt=salt,
            hmac_key_hex=self._hmac_key_hex,
            algorithm=self._hash_algorithm,
        )
        gettoken_path = (
            f"jdev/sys/gettoken/{gettoken_hash}/{self._user}/{TOKEN_PERMISSION_APP}/"
            f"{self._client_uuid}/{self._client_info}"
        )
        response = await self._send_encrypted(gettoken_path)
        if not response.ok:
            raise AuthenticationError(f"gettoken failed with code {response.code}")
        token_data = self._parse_json_value(response, hint="gettoken")

        try:
            self._token = Token(
                value=str(token_data["token"]),
                valid_until=loxone_seconds_to_datetime(int(token_data["validUntil"])),
                rights=int(token_data["tokenRights"]),
                unsecure_password=bool(token_data.get("unsecurePass", False)),
                replacement_hmac_key_hex=(
                    str(token_data["key"]) if token_data.get("key") else None
                ),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ProtocolError(f"gettoken payload malformed: {exc}") from exc

        if self._token.replacement_hmac_key_hex:
            self._hmac_key_hex = self._token.replacement_hmac_key_hex

    @staticmethod
    def _parse_json_value(response: CommandResponse, *, hint: str) -> dict[str, Any]:
        try:
            data = json.loads(response.value)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"{hint}: response value is not JSON") from exc
        if not isinstance(data, dict):
            raise ProtocolError(f"{hint}: response value is not an object")
        return data

    # ---- Internals: send / correlate --------------------------------------

    async def _exchange(self, plaintext_path: str, *, encrypted: bool) -> CommandResponse:
        """Send a command (encrypted or not) and await the matching response."""
        if self._transport is None or self._transport.closed:
            raise NotConnectedError("client is not connected")

        wire = self._wire_envelope(plaintext_path, encrypted=encrypted)
        correlation = self._correlation_key(plaintext_path)
        future: asyncio.Future[CommandResponse] = asyncio.get_running_loop().create_future()
        self._pending[correlation] = future

        # If an out-of-order response for this command already arrived
        # (possible when the server pre-queues replies in tests, or when
        # a real Miniserver delivers an event before the response), use
        # it before sending.
        orphan = self._pop_matching_orphan(correlation)
        if orphan is not None:
            future.set_result(orphan)

        try:
            await self._transport.send(wire)
            return await asyncio.wait_for(future, timeout=self._timeout_s)
        finally:
            self._pending.pop(correlation, None)

    def _pop_matching_orphan(self, correlation: str) -> CommandResponse | None:
        for i, response in enumerate(self._orphans):
            normalized = response.control.removeprefix("j")
            if (
                normalized == correlation
                or correlation.startswith(normalized + "/")
                or normalized.startswith(correlation + "/")
            ):
                return self._orphans.pop(i)
        return None

    async def _send_encrypted(self, plaintext_path: str) -> CommandResponse:
        return await self._exchange(plaintext_path, encrypted=True)

    def _wire_envelope(self, plaintext_path: str, *, encrypted: bool) -> str:
        if not encrypted:
            return plaintext_path
        if self._session_key is None:
            raise NotConnectedError("session key has not been negotiated yet")
        body = encrypt_command(plaintext_path, self._session_key)
        return f"jdev/sys/enc/{body}"

    @staticmethod
    def _correlation_key(plaintext_path: str) -> str:
        """Strip the leading ``j`` so requests match Miniserver responses.

        Loxone replies with ``LL.control`` set to either ``dev/sys/...`` or
        ``jdev/sys/...`` depending on firmware version. We normalise both
        sides by removing a leading ``j``.
        """
        return plaintext_path.removeprefix("j")

    # ---- Internals: receive loop ------------------------------------------

    async def _receive_loop(self) -> None:
        transport = self._transport
        if transport is None:
            return
        try:
            while True:
                header = await self._read_header(transport)
                if header.type is MessageType.KEEPALIVE:
                    continue
                if header.length == 0:
                    continue
                payload = await transport.recv()
                self._dispatch_payload(header, payload)
        except TransportClosedError:
            self._abort_pending(ClientError("connection closed"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._abort_pending(exc)
            raise

    async def _read_header(self, transport: WsTransport) -> MessageHeader:
        frame = await transport.recv()
        if isinstance(frame, str):
            raise ProtocolError("expected binary header, got text frame")
        if len(frame) != HEADER_LENGTH:
            raise ProtocolError(f"header frame has wrong length: {len(frame)}")
        try:
            return MessageHeader.parse(frame)
        except MessageError as exc:
            raise ProtocolError(f"invalid header: {exc}") from exc

    def _dispatch_payload(self, header: MessageHeader, payload: str | bytes) -> None:
        if header.type is MessageType.TEXT:
            text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
            self._dispatch_text(text)
            return
        blob = payload.encode("utf-8") if isinstance(payload, str) else payload
        if header.type is MessageType.EVENT_VALUES:
            for value_ev in parse_value_events(blob):
                self._state.set_value(value_ev.state_uuid, value_ev.value)
        elif header.type is MessageType.EVENT_TEXT:
            for text_ev in parse_text_events(blob):
                self._state.set_text(text_ev.state_uuid, text_ev.text)
        else:
            logger.debug("ignoring message of type %s", header.type.name)

    def _dispatch_text(self, payload: str) -> None:
        try:
            envelope = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("dropping non-JSON text frame")
            return
        if not isinstance(envelope, dict):
            return
        ll = envelope.get("LL")
        if not isinstance(ll, dict):
            return
        control = str(ll.get("control") or "")
        if not control:
            return
        normalized = control.removeprefix("j")

        code = _coerce_code(ll.get("Code") or ll.get("code"))
        value_field = ll.get("value")
        value_str = (
            value_field
            if isinstance(value_field, str)
            else json.dumps(value_field, separators=(",", ":"))
        )
        response = CommandResponse(control=control, value=value_str, code=code)

        future = self._find_pending(normalized)
        if future is None or future.done():
            # Buffer for a future that's about to register — see
            # `_pop_matching_orphan` in `_exchange`. Cap the buffer so a
            # broken Miniserver can't OOM us.
            if len(self._orphans) < 64:
                self._orphans.append(response)
            else:
                logger.warning("orphan response buffer full, dropping control=%s", control)
            return

        future.set_result(response)

    def _find_pending(self, normalized_control: str) -> asyncio.Future[CommandResponse] | None:
        """Locate the pending future whose request matches this response.

        Loxone is inconsistent about how much of the request path it
        echoes back in ``LL.control``: sometimes the full path
        (``dev/sys/getkey2/admin``), sometimes only the command stem
        (``dev/sys/keyexchange`` after a ``keyexchange/<long-base64>``).
        We accept either direction as long as the match terminates on a
        slash boundary so unrelated commands don't collide.
        """
        if normalized_control in self._pending:
            return self._pending[normalized_control]
        for key, future in self._pending.items():
            if key.startswith(normalized_control + "/") or normalized_control.startswith(key + "/"):
                return future
        return None

    def _abort_pending(self, exc: BaseException) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)

    # ---- Internals: keepalive & teardown ----------------------------------

    async def _keepalive_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._keepalive_interval_s)
                transport = self._transport
                if transport is None or transport.closed:
                    return
                await transport.send("keepalive")
        except asyncio.CancelledError:
            raise
        except TransportClosedError:
            return

    async def _teardown(self) -> None:
        if self._closing:
            return
        self._closing = True

        for task in (self._keepalive_task, self._receiver_task):
            if task is not None:
                task.cancel()
        for task in (self._keepalive_task, self._receiver_task):
            if task is None:
                continue
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                # Receiver tasks routinely surface the disconnect that's
                # already in progress; log at debug, don't propagate during
                # shutdown.
                logger.debug("task %s raised during teardown", task.get_name(), exc_info=True)

        self._abort_pending(ClientError("client closed"))

        if self._transport is not None and not self._transport.closed:
            await self._transport.close()

        self._keepalive_task = None
        self._receiver_task = None

    # ---- Misc helpers ------------------------------------------------------

    @staticmethod
    def _generate_client_uuid() -> str:
        h = secrets.token_hex(16)
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _coerce_code(raw: object) -> int:
    if raw is None:
        return 200
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return 0
    return 0
