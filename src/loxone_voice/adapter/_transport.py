"""Minimal WebSocket transport abstraction used by :class:`LoxoneClient`.

We define our own ``WsTransport`` Protocol so the client logic doesn't
depend on the ``websockets`` library directly — that lets us unit-test
the full handshake and event-dispatch flow against an in-memory fake.

Two implementations live here:

* :class:`InMemoryTransport` — test double with separate inbound /
  outbound queues, controlled by the test code.
* :class:`WebsocketsTransport` — thin adapter over
  ``websockets.asyncio.client.ClientConnection`` used at runtime.

Only the in-memory transport is exercised by unit tests; the
``websockets`` adapter is structurally trivial and verified against a
real Miniserver in a later integration test.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

import websockets
from websockets import Subprotocol
from websockets.asyncio.client import ClientConnection


class TransportClosedError(RuntimeError):
    """Raised when send/recv is attempted on a closed transport."""


@runtime_checkable
class WsTransport(Protocol):
    """The slice of a WebSocket connection the client needs.

    Implementations must be *full-duplex* — :meth:`send` and :meth:`recv`
    can be in flight on different tasks concurrently.
    """

    async def send(self, message: str | bytes) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...

    @property
    def closed(self) -> bool: ...


class InMemoryTransport:
    """Bidirectional in-memory transport for tests.

    Test code feeds server-to-client messages with :meth:`server_send`
    and inspects what the client sent via :attr:`outgoing`. Calling
    :meth:`server_close` simulates the peer disconnecting.
    """

    def __init__(self) -> None:
        self._incoming: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self.outgoing: list[str | bytes] = []
        self._closed = False

    async def send(self, message: str | bytes) -> None:
        if self._closed:
            raise TransportClosedError("transport is closed")
        self.outgoing.append(message)

    async def recv(self) -> str | bytes:
        # Explicit yield so back-to-back recvs don't starve the rest of
        # the event loop — real WebSockets always yield on each frame.
        await asyncio.sleep(0)
        msg = await self._incoming.get()
        if msg is None:
            self._closed = True
            raise TransportClosedError("transport closed by peer")
        return msg

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._incoming.put_nowait(None)

    @property
    def closed(self) -> bool:
        return self._closed

    # ---- test helpers ------------------------------------------------------

    def server_send(self, message: str | bytes) -> None:
        """Simulate a message arriving from the server."""
        self._incoming.put_nowait(message)

    def server_close(self) -> None:
        """Simulate the server closing the connection."""
        self._incoming.put_nowait(None)


class WebsocketsTransport:
    """Adapter from the ``websockets`` library to :class:`WsTransport`.

    Created via :meth:`connect`, which performs the handshake and
    returns a ready-to-use transport. Closing the transport closes the
    underlying connection.
    """

    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection
        self._closed = False

    @classmethod
    async def connect(
        cls, url: str, *, subprotocols: list[str] | None = None
    ) -> WebsocketsTransport:
        loxone_subprotocols = [Subprotocol(s) for s in (subprotocols or [])]
        connection = await websockets.connect(url, subprotocols=loxone_subprotocols)
        return cls(connection)

    async def send(self, message: str | bytes) -> None:
        if self._closed:
            raise TransportClosedError("transport is closed")
        await self._connection.send(message)

    async def recv(self) -> str | bytes:
        try:
            return await self._connection.recv()
        except websockets.ConnectionClosed as exc:
            self._closed = True
            raise TransportClosedError("transport closed by peer") from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._connection.close()

    @property
    def closed(self) -> bool:
        return self._closed
