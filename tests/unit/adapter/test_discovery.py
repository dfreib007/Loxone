"""Tests for `loxone_voice.adapter.discovery`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from loxone_voice.adapter import DiscoveryError, fetch_public_key

# An example PEM the Miniserver returns — content doesn't matter for the
# transport tests, the bytes are just round-tripped.
_FAKE_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA\n"
    "-----END CERTIFICATE-----\n"
)


@pytest.fixture
async def make_client() -> AsyncIterator[Callable[[httpx.MockTransport], httpx.AsyncClient]]:
    """Factory that wraps a supplied ``MockTransport`` in an ``AsyncClient``."""
    clients: list[httpx.AsyncClient] = []

    def _factory(transport: httpx.MockTransport) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=transport)
        clients.append(client)
        return client

    yield _factory

    for client in clients:
        await client.aclose()


async def test_fetch_public_key_returns_value(
    make_client: Callable[[httpx.MockTransport], httpx.AsyncClient],
) -> None:
    received_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_requests.append(request)
        return httpx.Response(
            200, json={"LL": {"control": "dev/sys/getPublicKey", "value": _FAKE_PEM, "Code": "200"}}
        )

    client = make_client(httpx.MockTransport(handler))
    pem = await fetch_public_key(client, base_url="http://miniserver.test")

    assert pem == _FAKE_PEM
    assert len(received_requests) == 1
    assert received_requests[0].method == "GET"
    assert received_requests[0].url.path == "/jdev/sys/getPublicKey"
    assert str(received_requests[0].url) == "http://miniserver.test/jdev/sys/getPublicKey"


async def test_fetch_public_key_strips_trailing_slash_from_base_url(
    make_client: Callable[[httpx.MockTransport], httpx.AsyncClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"LL": {"value": _FAKE_PEM, "Code": "200"}})

    client = make_client(httpx.MockTransport(handler))
    pem = await fetch_public_key(client, base_url="http://miniserver.test/")
    assert pem == _FAKE_PEM


async def test_fetch_public_key_raises_on_http_error(
    make_client: Callable[[httpx.MockTransport], httpx.AsyncClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await fetch_public_key(client, base_url="http://miniserver.test")


async def test_fetch_public_key_rejects_missing_envelope(
    make_client: Callable[[httpx.MockTransport], httpx.AsyncClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"oops": "no LL here"})

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(DiscoveryError, match="LL"):
        await fetch_public_key(client, base_url="http://miniserver.test")


async def test_fetch_public_key_rejects_non_object_envelope(
    make_client: Callable[[httpx.MockTransport], httpx.AsyncClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"LL": ["not", "an", "object"]})

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(DiscoveryError, match="must be an object"):
        await fetch_public_key(client, base_url="http://miniserver.test")


async def test_fetch_public_key_rejects_non_200_code(
    make_client: Callable[[httpx.MockTransport], httpx.AsyncClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"LL": {"value": _FAKE_PEM, "Code": "401"}})

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(DiscoveryError, match="code 401"):
        await fetch_public_key(client, base_url="http://miniserver.test")


async def test_fetch_public_key_accepts_lowercase_code_key(
    make_client: Callable[[httpx.MockTransport], httpx.AsyncClient],
) -> None:
    """Some firmwares spell the field ``code`` instead of ``Code``."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"LL": {"value": _FAKE_PEM, "code": "200"}})

    client = make_client(httpx.MockTransport(handler))
    pem = await fetch_public_key(client, base_url="http://miniserver.test")
    assert pem == _FAKE_PEM


async def test_fetch_public_key_rejects_missing_value(
    make_client: Callable[[httpx.MockTransport], httpx.AsyncClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"LL": {"Code": "200"}})

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(DiscoveryError, match="value"):
        await fetch_public_key(client, base_url="http://miniserver.test")


async def test_fetch_public_key_rejects_empty_value(
    make_client: Callable[[httpx.MockTransport], httpx.AsyncClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"LL": {"value": "   ", "Code": "200"}})

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(DiscoveryError, match="value"):
        await fetch_public_key(client, base_url="http://miniserver.test")


async def test_fetch_public_key_rejects_non_string_value(
    make_client: Callable[[httpx.MockTransport], httpx.AsyncClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"LL": {"value": 42, "Code": "200"}})

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(DiscoveryError, match="non-string"):
        await fetch_public_key(client, base_url="http://miniserver.test")


async def test_fetch_public_key_rejects_non_dict_payload(
    make_client: Callable[[httpx.MockTransport], httpx.AsyncClient],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected", "array"])

    client = make_client(httpx.MockTransport(handler))
    with pytest.raises(DiscoveryError, match="LL"):
        await fetch_public_key(client, base_url="http://miniserver.test")
