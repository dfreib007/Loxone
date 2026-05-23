"""Append-only audit log.

Every action initiated via the assistant — whether by Telegram, Alexa,
or a future channel — is recorded as a single JSONL line. The log is
the project's *source of truth* for "what did the system actually do",
and it's required by the security baseline in :doc:`docs/security.md`.

Contract:
* JSON-encodable fields only — no Pydantic objects, no bytes, no secrets.
* External user identifiers (Telegram IDs, etc.) MUST be passed through
  :func:`hash_user_id` before they're written. The raw ID never lands
  on disk.
* Writes are serialized through an :class:`asyncio.Lock` so concurrent
  webhook handlers can't interleave half-written lines.

This module is intentionally tiny. Heavier features (rotation, remote
shipping, schema versioning) belong further down the road once we have
a baseline to migrate.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One row of the audit log.

    Use the constructor for ad-hoc rows or :meth:`from_turn` for the
    common ``IntentEngine`` turn case.
    """

    timestamp: dt.datetime
    user_id: str
    channel: str
    input_text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    model: str = ""
    duration_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json_line(self) -> str:
        """Serialise into a single JSONL line (newline-terminated)."""
        payload: dict[str, Any] = {
            "ts": self.timestamp.astimezone(dt.UTC).isoformat(),
            "user": self.user_id,
            "channel": self.channel,
            "input": self.input_text,
            "tool_calls": self.tool_calls,
            "final": self.final_text,
            "model": self.model,
            "duration_ms": self.duration_ms,
        }
        if self.extra:
            payload["extra"] = self.extra
        return json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"


def hash_user_id(raw_id: str | int, *, salt: str = "loxone-voice") -> str:
    """Return a stable, non-reversible identifier for an external user.

    SHA-256 with a fixed application salt. The hash is the *only* form
    of the user identifier that ever appears in the audit log so an
    attacker who exfiltrates `audit.jsonl` can't recover Telegram IDs
    or Amazon account IDs.
    """
    digest = hashlib.sha256(f"{salt}:{raw_id}".encode()).hexdigest()
    # Truncate to 16 hex chars (64 bits) — enough for collision-free
    # accounting at our scale, half the noise in logs.
    return digest[:16]


class AuditLog:
    """File-backed JSONL audit log.

    Use :meth:`append` from any coroutine; concurrent appends are
    serialised. The parent directory is created on construction; if you
    need a different opening mode (rotation, syslog, etc.) write a new
    class — don't extend this one.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def append(self, record: AuditRecord) -> None:
        line = record.to_json_line()
        async with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    async def append_turn(
        self,
        *,
        user_id_hash: str,
        channel: str,
        input_text: str,
        final_text: str,
        tool_calls: list[dict[str, Any]],
        model: str,
        duration_ms: int,
    ) -> None:
        """Convenience helper for the common ``IntentEngine`` turn case."""
        await self.append(
            AuditRecord(
                timestamp=dt.datetime.now(tz=dt.UTC),
                user_id=user_id_hash,
                channel=channel,
                input_text=input_text,
                final_text=final_text,
                tool_calls=tool_calls,
                model=model,
                duration_ms=duration_ms,
            )
        )
