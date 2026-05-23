"""User allow-listing for the gateway.

The bot must reject every message from a user it doesn't know.
This is the first line of defence against an attacker who finds the
bot's chat handle: even a leaked Telegram bot token doesn't let them
into the smart home unless their user ID is on the list.

`UserWhitelist` is a small, frozen wrapper around a set of allowed IDs.
We keep the implementation deliberately boring — no glob patterns, no
role-based access, no per-tool permissions. Add those later if the
threat model demands them; right now plain inclusion checks are the
right level.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


class WhitelistViolation(PermissionError):
    """Raised by :meth:`UserWhitelist.require` when a user isn't allowed.

    Inherits from :class:`PermissionError` so the gateway can choose to
    treat it as a normal access denial without a special branch.
    """


@dataclass(frozen=True, slots=True)
class UserWhitelist:
    """Immutable set of permitted user identifiers.

    User IDs are stored as ints because Telegram and Alexa both use
    integer identifiers internally. If a future channel uses strings,
    add a parallel `UserWhitelistStr` rather than mixing the types.
    """

    allowed: frozenset[int]

    @classmethod
    def from_iterable(cls, ids: Iterable[int]) -> UserWhitelist:
        return cls(allowed=frozenset(ids))

    @classmethod
    def empty(cls) -> UserWhitelist:
        return cls(allowed=frozenset())

    def contains(self, user_id: int) -> bool:
        return user_id in self.allowed

    def require(self, user_id: int) -> None:
        """Raise :class:`WhitelistViolation` if ``user_id`` isn't permitted."""
        if user_id not in self.allowed:
            raise WhitelistViolation(f"user {user_id} is not allowed")

    def with_added(self, *user_ids: int) -> UserWhitelist:
        """Return a new whitelist with additional members.

        Useful for tests; production code should configure the whitelist
        from `Settings.telegram_allowed_user_ids`.
        """
        return UserWhitelist(allowed=self.allowed | frozenset(user_ids))

    def __contains__(self, user_id: object) -> bool:
        return isinstance(user_id, int) and user_id in self.allowed

    def __bool__(self) -> bool:
        return bool(self.allowed)

    def __len__(self) -> int:
        return len(self.allowed)
