"""Tests for `loxone_voice.adapter.state`."""

from __future__ import annotations

import time
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from loxone_voice.adapter import StateStore


@pytest.fixture
def fake_clock() -> Iterator[list[float]]:
    """Yield a mutable single-element list used as the monotonic clock."""
    now = [1000.0]
    with patch("loxone_voice.adapter.state.time.monotonic", side_effect=lambda: now[0]):
        yield now


def test_empty_store() -> None:
    s = StateStore()
    assert len(s) == 0
    assert s.get("nope") is None
    assert "nope" not in s


def test_set_and_get_value(fake_clock: list[float]) -> None:
    s = StateStore()
    s.set_value("u1", 42.5)
    entry = s.get("u1")
    assert entry is not None
    assert entry.value == 42.5
    assert entry.text is None
    assert entry.updated_at == 1000.0


def test_set_and_get_text(fake_clock: list[float]) -> None:
    s = StateStore()
    s.set_text("u1", "online")
    entry = s.get("u1")
    assert entry is not None
    assert entry.text == "online"
    assert entry.value is None


def test_value_and_text_coexist(fake_clock: list[float]) -> None:
    s = StateStore()
    s.set_value("u1", 1.0)
    fake_clock[0] = 1001.0
    s.set_text("u1", "ok")
    entry = s.get("u1")
    assert entry is not None
    # Both fields survive — the most recent update wins for `updated_at`.
    assert entry.value == 1.0
    assert entry.text == "ok"
    assert entry.updated_at == 1001.0


def test_overwrite_updates_value_and_timestamp(fake_clock: list[float]) -> None:
    s = StateStore()
    s.set_value("u1", 1)
    fake_clock[0] = 1500.0
    s.set_value("u1", 2)
    entry = s.get("u1")
    assert entry is not None
    assert entry.value == 2
    assert entry.updated_at == 1500.0


def test_boolean_and_none_values_supported(fake_clock: list[float]) -> None:
    s = StateStore()
    s.set_value("u1", True)
    s.set_value("u2", None)
    e1 = s.get("u1")
    e2 = s.get("u2")
    assert e1 is not None and e1.value is True
    assert e2 is not None and e2.value is None


def test_get_many_returns_only_present(fake_clock: list[float]) -> None:
    s = StateStore()
    s.set_value("u1", 1)
    s.set_value("u2", 2)
    result = s.get_many(["u1", "u2", "missing"])
    assert set(result) == {"u1", "u2"}


def test_clear_drops_everything(fake_clock: list[float]) -> None:
    s = StateStore()
    s.set_value("u1", 1)
    s.set_text("u2", "x")
    assert len(s) == 2
    s.clear()
    assert len(s) == 0
    assert s.get("u1") is None


def test_contains_rejects_non_string_keys(fake_clock: list[float]) -> None:
    s = StateStore()
    s.set_value("u1", 1)
    assert "u1" in s
    assert 42 not in s
    assert None not in s


def test_real_time_clock_is_monotonic_compatible() -> None:
    """Sanity-check: without the fake clock we still record sensible timestamps."""
    s = StateStore()
    before = time.monotonic()
    s.set_value("u1", 1)
    after = time.monotonic()
    entry = s.get("u1")
    assert entry is not None
    assert before <= entry.updated_at <= after


def test_is_stale_detects_age(fake_clock: list[float]) -> None:
    s = StateStore()
    s.set_value("u1", 1)
    entry = s.get("u1")
    assert entry is not None
    assert not entry.is_stale(max_age_s=10.0)
    fake_clock[0] += 15.0
    assert entry.is_stale(max_age_s=10.0)
    assert not entry.is_stale(max_age_s=60.0)
