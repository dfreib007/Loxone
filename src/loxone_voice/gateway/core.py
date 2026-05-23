"""Channel-agnostic gateway core.

``Gateway`` is the single chokepoint every inbound text message passes
through, regardless of whether it arrived via Telegram, Alexa, or a
future channel. It is responsible for:

1. Enforcing the user allow-list (:class:`UserWhitelist`).
2. Forwarding the message to the per-user :class:`IntentEngine`.
3. Writing one audit-log entry per turn with the hashed user id.
4. Rate-limiting noisy users so a runaway script can't burn through
   Anthropic credits.

The channel-specific layers (:mod:`telegram_bot`, Alexa handler) are
thin shells that translate their native message types into a call to
:meth:`Gateway.handle_text`.

Design notes
------------
* One :class:`IntentEngine` *per user* — Claude conversation history is
  per-user and isolated. Created lazily on first contact via an
  engine factory the caller supplies.
* The gateway never raises on user input. Errors (auth, rate limit,
  engine failure) come back as :class:`GatewayResponse` with
  ``is_error=True`` so the channel layer just renders whatever it gets.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from loxone_voice.audit import AuditLog, hash_user_id
from loxone_voice.intent import IntentEngineError, IntentResult

from .whitelist import UserWhitelist, WhitelistViolation

logger = logging.getLogger(__name__)


_DEFAULT_RATE_PER_MIN: Final[int] = 30


@dataclass(frozen=True, slots=True)
class GatewayResponse:
    """What the channel layer should send back to the user."""

    text: str
    is_error: bool = False


@runtime_checkable
class GatewayEngine(Protocol):
    """Minimal engine surface the gateway depends on.

    The real :class:`loxone_voice.intent.IntentEngine` satisfies this
    naturally; tests inject a fake that implements the same shape.
    """

    async def run(self, user_message: str) -> IntentResult: ...


EngineFactory = Callable[[int], Awaitable[GatewayEngine]]


class Gateway:
    """Channel-agnostic message handler.

    Stateless except for:
    - The per-user IntentEngine cache (so conversation history persists).
    - Per-user request timestamps used by the rate limiter.
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

        engine = await self._get_or_create_engine(user_id)

        started = time.monotonic()
        try:
            result = await engine.run(text)
        except IntentEngineError as exc:
            logger.error("intent engine failed for user %d: %s", user_id, exc)
            await self._audit.append_turn(
                user_id_hash=hash_user_id(user_id),
                channel=self._channel,
                input_text=text,
                final_text=f"(engine error: {exc})",
                tool_calls=[],
                model="",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return GatewayResponse(
                "Ich konnte die Anfrage nicht abschließen. Bitte versuche es nochmal.",
                is_error=True,
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        await self._audit.append_turn(
            user_id_hash=hash_user_id(user_id),
            channel=self._channel,
            input_text=text,
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
        )

    def reset_user(self, user_id: int) -> None:
        """Drop a user's conversation history.

        Useful for `/start` or `/reset` slash commands.
        """
        self._engines.pop(user_id, None)
        self._request_times.pop(user_id, None)

    # ---- Internals ---------------------------------------------------------

    async def _get_or_create_engine(self, user_id: int) -> GatewayEngine:
        engine = self._engines.get(user_id)
        if engine is None:
            engine = await self._engine_factory(user_id)
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
