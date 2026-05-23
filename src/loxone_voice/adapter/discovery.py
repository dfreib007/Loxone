"""HTTP discovery helpers for the Loxone Miniserver.

A handful of endpoints (``/jdev/sys/getPublicKey`` in particular) need
to be reachable *before* the encrypted WebSocket session exists. We use
plain HTTP for them.

These helpers take an :class:`httpx.AsyncClient` injected by the caller
so tests can supply :class:`httpx.MockTransport` without monkey-patching.
"""

from __future__ import annotations

from typing import Any

import httpx


class DiscoveryError(RuntimeError):
    """Raised when a discovery call fails or returns an unexpected shape."""


async def fetch_public_key(client: httpx.AsyncClient, *, base_url: str) -> str:
    """Fetch the Miniserver's RSA public key.

    Returns the PEM string exactly as the Miniserver sent it — Loxone
    wraps it in ``BEGIN CERTIFICATE`` markers even though the body is a
    bare SubjectPublicKeyInfo. The caller is responsible for parsing it
    via :func:`loxone_voice.adapter.auth.parse_miniserver_public_key`.

    Raises:
        DiscoveryError: if the response envelope is malformed or the
            Miniserver returned a non-200 code.
        httpx.HTTPStatusError: if the HTTP status code is not 2xx.
    """
    response = await client.get(f"{base_url.rstrip('/')}/jdev/sys/getPublicKey")
    response.raise_for_status()
    payload = response.json()
    return _extract_ll_value(payload, control_hint="getPublicKey")


def _extract_ll_value(payload: Any, *, control_hint: str) -> str:
    """Pull the ``LL.value`` field from a Loxone JSON envelope, validating it.

    The Loxone envelope looks like:

        {"LL": {"control": "...", "value": "...", "Code": "200"}}
    """
    if not isinstance(payload, dict) or "LL" not in payload:
        raise DiscoveryError(f"{control_hint}: missing 'LL' envelope")
    ll = payload["LL"]
    if not isinstance(ll, dict):
        raise DiscoveryError(f"{control_hint}: 'LL' must be an object")
    code = str(ll.get("Code") or ll.get("code") or "")
    if code and code != "200":
        raise DiscoveryError(f"{control_hint}: Miniserver returned code {code}")
    value = ll.get("value")
    if not isinstance(value, str) or not value.strip():
        raise DiscoveryError(f"{control_hint}: missing or non-string 'value'")
    return value
