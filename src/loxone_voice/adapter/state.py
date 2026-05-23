"""In-memory cache for the Miniserver's last-known state values.

The Loxone WebSocket pushes state updates as `(uuid, value)` or
`(uuid, text)` events. We cache them so read-tools answer in O(1) without
a roundtrip to the Miniserver. Cache is process-local and rebuilt on
every reconnect — no persistence needed.

`StateStore` is intentionally synchronous: writes happen only from the
single WebSocket task (no concurrent writers), reads happen from async
tool handlers but never block, so we don't need a lock.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StateEntry:
    """A single state value with its time of last update."""

    value: float | bool | None
    text: str | None
    updated_at: float

    def is_stale(self, *, max_age_s: float = 300.0) -> bool:
        """Whether this entry is older than `max_age_s` seconds."""
        return (time.monotonic() - self.updated_at) > max_age_s


class StateStore:
    """Latest-value-wins store keyed by Loxone state-UUID.

    The Miniserver sends *values* (numeric) and *text* (string) updates
    on separate channels for the same UUID. We keep both fields on each
    entry — whichever channel updated last sets its column; the other
    stays as it was.
    """

    def __init__(self) -> None:
        self._data: dict[str, StateEntry] = {}

    def set_value(self, uuid: str, value: float | bool | None) -> None:
        """Record a numeric/boolean value for `uuid`."""
        previous = self._data.get(uuid)
        self._data[uuid] = StateEntry(
            value=value,
            text=previous.text if previous else None,
            updated_at=time.monotonic(),
        )

    def set_text(self, uuid: str, text: str) -> None:
        """Record a text value for `uuid`."""
        previous = self._data.get(uuid)
        self._data[uuid] = StateEntry(
            value=previous.value if previous else None,
            text=text,
            updated_at=time.monotonic(),
        )

    def get(self, uuid: str) -> StateEntry | None:
        return self._data.get(uuid)

    def get_many(self, uuids: Iterable[str]) -> dict[str, StateEntry]:
        """Return entries for every UUID that has been seen; missing UUIDs are omitted."""
        return {uuid: self._data[uuid] for uuid in uuids if uuid in self._data}

    def clear(self) -> None:
        """Drop all entries — called on reconnect before the Miniserver re-sends state."""
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, uuid: object) -> bool:
        return isinstance(uuid, str) and uuid in self._data
