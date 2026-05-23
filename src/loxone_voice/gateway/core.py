"""Channel-agnostic gateway core.

``Gateway`` is the single chokepoint every inbound user message passes
through, regardless of whether it arrived via Telegram, Alexa, or a
future channel. It is responsible for:

1. Enforcing the user allow-list (:class:`UserWhitelist`).
2. Forwarding the message to the per-user :class:`IntentEngine`.
3. Writing one audit-log entry per turn with the hashed user id.
4. Rate-limiting noisy users so a runaway script can't burn through
   Anthropic credits.
5. Driving the confirmation flow for ``requires_confirmation`` tools
   via per-user :class:`ConfirmationGate` instances.

The channel-specific layers (:mod:`telegram_bot`, Alexa handler) are
thin shells that translate their native message types into a call to
:meth:`Gateway.handle_text` or :meth:`Gateway.confirm_action`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from loxone_voice.audit import AuditLog, hash_user_id
from loxone_voice.intent import ConfirmationCallback, IntentEngineError, IntentResult

from .confirmation import ConfirmationGate, PendingAction
from .whitelist import UserWhitelist, WhitelistViolation

logger = logging.getLogger(__name__)


_DEFAULT_RATE_PER_MIN: Final[int] = 30


@dataclass(frozen=True, slots=True)
class GatewayResponse:
    """What the channel layer should send back to the user.

    ``pending_actions`` is non-empty when the engine deferred at least
    one ``requires_confirmation`` tool. The channel layer should render
    a yes/no prompt (e.g. Telegram inline buttons) carrying the action
    ids back via :meth:`Gateway.confirm_action`.
    """

    text: str
    is_error: bool = False
    pending_actions: list[PendingAction] = field(default_factory=list)


@runtime_checkable
class GatewayEngine(Protocol):
    """Minimal engine surface the gateway depends on.

    The real :class:`loxone_voice.intent.IntentEngine` satisfies this
    naturally; tests inject a fake that implements the same shape.
    """

    async def run(self, user_message: str) -> IntentResult: ...


EngineFactory = Callable[[int, ConfirmationCallback], Awaitable[GatewayEngine]]


_CONFIRMED_PROMPT = (
    "BENUTZER HAT BESTÄTIGT: Bitte die zuvor geplanten Aktionen jetzt ausführen "
    "und das Ergebnis kurz bestätigen."
)
_DENIED_PROMPT = (
    "BENUTZER HAT ABGELEHNT: Bitte die geplanten Aktionen NICHT ausführen "
    "und dem Benutzer kurz bestätigen, dass die Aktion abgebrochen wurde."
)


class Gateway:
    """Channel-agnostic message handler.

    Stateful only in caches per user: the conversation engine, request
    timestamps for rate limiting, the confirmation gate, and the set
    of pending actions waiting for a button press.
    """

    def __init__(
        self,
        *,
        whitelist: UserWhitelist,
        engine_factory: EngineFactory,
        audit: AuditLog,
        channel: str,
        rate_limit_per_minute: int = _DEFAULT_RATE_PER_MIN,
    ) -> None:
        self._whitelist = whitelist
        self._engine_factory = engine_factory
        self._audit = audit
        self._channel = channel
        self._rate_limit = rate_limit_per_minute
        self._engines: dict[int, GatewayEngine] = {}
        self._gates: dict[int, ConfirmationGate] = {}
        self._pending_actions: dict[int, dict[str, PendingAction]] = {}
        self._request_times: dict[int, list[float]] = {}

    # ---- Public ------------------------------------------------------------

    async def handle_text(self, *, user_id: int, text: str) -> GatewayResponse:
        """Process a single inbound text message and return what to send back."""
        text = text.strip()
        if not text:
            return GatewayResponse("Bitte schreib mir einen Befehl.", is_error=True)

        try:
            self._whitelist.require(user_id)
        except WhitelistViolation:
            logger.warning("rejecting message from non-allowed user %d", user_id)
            return GatewayResponse(
                "Du bist für diesen Bot nicht freigeschaltet.",
                is_error=True,
            )

        if not self._allow_under_rate_limit(user_id):
            return GatewayResponse(
                "Zu viele Befehle in kurzer Zeit. Bitte einen Moment warten.",
                is_error=True,
            )

        return await self._run_turn(user_id=user_id, prompt=text, audit_input=text)

    async def confirm_action(
        self, *, user_id: int, action_ids: list[str], approved: bool
    ) -> GatewayResponse:
        """React to the user's Yes/No on previously deferred actions.

        Looks up the pending actions, marks them approved or denied on
        the per-user :class:`ConfirmationGate`, and runs one more engine
        turn so Claude either executes the actions or cancels them.
        """
        try:
            self._whitelist.require(user_id)
        except WhitelistViolation:
            return GatewayResponse(
                "Du bist für diesen Bot nicht freigeschaltet.",
                is_error=True,
            )

        gate = self._gates.get(user_id)
        pending = self._pending_actions.get(user_id, {})
        known_ids = [aid for aid in action_ids if aid in pending]
        if gate is None or not known_ids:
            return GatewayResponse(
                "Keine offene Aktion zum Bestätigen gefunden (vielleicht abgelaufen?).",
                is_error=True,
            )

        if approved:
            gate.approve(known_ids)
            prompt = _CONFIRMED_PROMPT
        else:
            gate.deny(known_ids)
            prompt = _DENIED_PROMPT

        # Clear matched pendings before the rerun so a stale id doesn't
        # come back and re-trigger the prompt.
        for aid in known_ids:
            pending.pop(aid, None)

        audit_input = f"[confirm approved={approved} actions={','.join(known_ids)}]"
        return await self._run_turn(user_id=user_id, prompt=prompt, audit_input=audit_input)

    def reset_user(self, user_id: int) -> None:
        """Drop a user's conversation history and pending state."""
        self._engines.pop(user_id, None)
        self._gates.pop(user_id, None)
        self._pending_actions.pop(user_id, None)
        self._request_times.pop(user_id, None)

    # ---- Internals ---------------------------------------------------------

    async def _run_turn(self, *, user_id: int, prompt: str, audit_input: str) -> GatewayResponse:
        """Common path for handle_text and confirm_action."""
        engine = await self._get_or_create_engine(user_id)
        started = time.monotonic()
        try:
            result = await engine.run(prompt)
        except IntentEngineError as exc:
            logger.error("intent engine failed for user %d: %s", user_id, exc)
            await self._audit.append_turn(
                user_id_hash=hash_user_id(user_id),
                channel=self._channel,
                input_text=audit_input,
                final_text=f"(engine error: {exc})",
                tool_calls=[],
                model="",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return GatewayResponse(
                "Ich konnte die Anfrage nicht abschließen. Bitte versuche es nochmal.",
                is_error=True,
            )

        pending_actions = [PendingAction.from_engine(p) for p in result.pending_confirmations]
        if pending_actions:
            store = self._pending_actions.setdefault(user_id, {})
            for action in pending_actions:
                store[action.action_id] = action

        duration_ms = int((time.monotonic() - started) * 1000)
        await self._audit.append_turn(
            user_id_hash=hash_user_id(user_id),
            channel=self._channel,
            input_text=audit_input,
            final_text=result.final_text,
            tool_calls=[
                {
                    "name": call.name,
                    "args": call.arguments,
                    "ok": not call.is_error,
                    "result": call.result if call.is_error else "",
                }
                for call in result.tool_calls
            ],
            model=getattr(engine, "_model", ""),
            duration_ms=duration_ms,
        )

        return GatewayResponse(
            text=result.final_text or "(keine Antwort)",
            is_error=False,
            pending_actions=pending_actions,
        )

    async def _get_or_create_engine(self, user_id: int) -> GatewayEngine:
        engine = self._engines.get(user_id)
        if engine is None:
            gate = self._gates.setdefault(user_id, ConfirmationGate())
            engine = await self._engine_factory(user_id, gate.decide)
            self._engines[user_id] = engine
        return engine

    def _allow_under_rate_limit(self, user_id: int) -> bool:
        """Sliding-window rate check.

        Keeps a per-user list of request timestamps, drops everything
        older than 60 seconds, and rejects when the surviving count is
        already at the limit.
        """
        if self._rate_limit <= 0:
            return True
        now = time.monotonic()
        window_start = now - 60.0
        bucket = self._request_times.setdefault(user_id, [])
        bucket[:] = [t for t in bucket if t > window_start]
        if len(bucket) >= self._rate_limit:
            return False
        bucket.append(now)
        return True
