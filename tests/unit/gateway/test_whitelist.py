"""Tests for `loxone_voice.gateway.whitelist`."""

from __future__ import annotations

import pytest

from loxone_voice.gateway import UserWhitelist, WhitelistViolation


def test_from_iterable_dedupes() -> None:
    wl = UserWhitelist.from_iterable([1, 2, 2, 3])
    assert wl.allowed == frozenset({1, 2, 3})


def test_contains_returns_true_for_listed_user() -> None:
    wl = UserWhitelist.from_iterable([1, 2])
    assert wl.contains(1)
    assert not wl.contains(99)


def test_in_operator_uses_set_membership() -> None:
    wl = UserWhitelist.from_iterable([1, 2])
    assert 1 in wl
    assert 99 not in wl
    assert "1" not in wl  # type mismatch — explicit


def test_require_raises_for_disallowed_user() -> None:
    wl = UserWhitelist.from_iterable([1])
    wl.require(1)  # no raise
    with pytest.raises(WhitelistViolation, match="not allowed"):
        wl.require(2)


def test_whitelist_violation_is_permission_error() -> None:
    """The gateway can treat WhitelistViolation as a generic PermissionError."""
    assert issubclass(WhitelistViolation, PermissionError)


def test_empty_whitelist_rejects_everyone() -> None:
    wl = UserWhitelist.empty()
    assert not wl
    assert len(wl) == 0
    with pytest.raises(WhitelistViolation):
        wl.require(1)


def test_with_added_returns_new_instance() -> None:
    base = UserWhitelist.from_iterable([1])
    extended = base.with_added(2, 3)
    assert base.allowed == frozenset({1})
    assert extended.allowed == frozenset({1, 2, 3})


def test_whitelist_is_frozen() -> None:
    wl = UserWhitelist.from_iterable([1])
    # frozen dataclass means attribute mutation raises FrozenInstanceError,
    # which is a subclass of AttributeError.
    with pytest.raises(AttributeError):
        wl.allowed = frozenset({99})  # type: ignore[misc]


def test_whitelist_is_hashable() -> None:
    wl_a = UserWhitelist.from_iterable([1, 2])
    wl_b = UserWhitelist.from_iterable([1, 2])
    assert hash(wl_a) == hash(wl_b)
    assert {wl_a, wl_b} == {wl_a}


def test_bool_reflects_membership() -> None:
    assert not UserWhitelist.empty()
    assert UserWhitelist.from_iterable([1])
