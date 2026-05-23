"""Per-user confirmation state for destructive actions.

When the intent engine asks our :meth:`ConfirmationGate.decide` callback
about a tool with ``requires_confirmation=True``, we look up the action
id (a stable hash of tool name + arguments) in our state machine:

* **First sighting** → ``DEFER``: tell the engine to stop, surface the
  pending action to the user (via Telegram buttons), and wait.
* **Approved** by a later button press → ``APPROVE`` and clear the
  marker so a *new* matching call would still defer.
* **Denied** → ``DENY`` and clear the marker.

Approvals are TTL'd. If the user walks away mid-confirmation, the
pending action expires and a fresh sighting starts the loop over.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from loxone_voice.intent import ConfirmationDecision, PendingConfirmation

_DEFAULT_APPROVAL_TTL_S = 300.0


@dataclass(frozen=True, slots=True)
class PendingAction:
    """A snapshot of a deferred tool call awaiting user confirmation."""

    tool_name: str
    tool_arguments: dict[str, Any]
    summary: str

    @property
    def action_id(self) -> str:
        """Stable short hash of ``tool_name + arguments``.

        Used as the Telegram callback-data payload and the lookup key in
        the gate's approve / deny dictionaries.
        """
        canonical = json.dumps(
            {"n": self.tool_name, "a": self.tool_arguments},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_engine(cls, pending: PendingConfirmation) -> PendingAction:
        return cls(
            tool_name=pending.tool_name,
            tool_arguments=dict(pending.tool_arguments),
            summary=pending.summary,
        )


@dataclass
class ConfirmationGate:
    """State machine the engine consults via its ``on_confirm`` callback.

    Use exactly one gate per user, kept alive across turns. Concurrent
    handle_text calls from the same user would race on this state, but
    the gateway already serialises per-user via the engine cache, so
    we keep it lock-free.
    """

    ttl_s: float = _DEFAULT_APPROVAL_TTL_S
    _approved: dict[str, float] = field(default_factory=dict)
    _denied: set[str] = field(default_factory=set)

    async def decide(self, tool_name: str, tool_arguments: dict[str, Any]) -> ConfirmationDecision:
        action = PendingAction(tool_name=tool_name, tool_arguments=tool_arguments, summary="")
        action_id = action.action_id
        self._expire_stale()

        if action_id in self._denied:
            self._denied.discard(action_id)
            return ConfirmationDecision.DENY
        if action_id in self._approved:
            self._approved.pop(action_id, None)
            return ConfirmationDecision.APPROVE
        return ConfirmationDecision.DEFER

    def approve(self, action_ids: list[str]) -> None:
        now = time.monotonic()
        for action_id in action_ids:
            self._approved[action_id] = now

    def deny(self, action_ids: list[str]) -> None:
        for action_id in action_ids:
            self._denied.add(action_id)

    def clear(self) -> None:
        self._approved.clear()
        self._denied.clear()

    def has_pending_approval(self, action_id: str) -> bool:
        self._expire_stale()
        return action_id in self._approved

    def _expire_stale(self) -> None:
        cutoff = time.monotonic() - self.ttl_s
        for action_id in [aid for aid, ts in self._approved.items() if ts < cutoff]:
            self._approved.pop(action_id, None)
