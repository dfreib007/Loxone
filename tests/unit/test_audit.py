"""Tests for `loxone_voice.audit`."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest

from loxone_voice.audit import AuditLog, AuditRecord, hash_user_id


def test_hash_user_id_is_deterministic_and_truncated() -> None:
    a = hash_user_id(12345)
    b = hash_user_id(12345)
    assert a == b
    assert len(a) == 16


def test_hash_user_id_differs_for_different_ids() -> None:
    assert hash_user_id(1) != hash_user_id(2)


def test_hash_user_id_does_not_contain_raw_id() -> None:
    raw = "telegram-user-987654321"
    assert raw not in hash_user_id(raw)


def test_hash_user_id_accepts_int_and_str() -> None:
    assert hash_user_id(42) == hash_user_id("42")


def test_hash_user_id_changes_with_salt() -> None:
    assert hash_user_id(42) != hash_user_id(42, salt="other")


def test_audit_record_renders_iso_utc_timestamp() -> None:
    record = AuditRecord(
        timestamp=dt.datetime(2026, 5, 23, 14, 30, tzinfo=dt.UTC),
        user_id="u",
        channel="telegram",
        input_text="hi",
    )
    line = record.to_json_line()
    payload = json.loads(line)
    assert payload["ts"] == "2026-05-23T14:30:00+00:00"


def test_audit_record_includes_required_fields() -> None:
    record = AuditRecord(
        timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        user_id="abcd",
        channel="telegram",
        input_text="schalte das Licht an",
        tool_calls=[{"name": "set_control", "ok": True}],
        final_text="Erledigt.",
        model="claude-opus-4-7",
        duration_ms=540,
    )
    payload = json.loads(record.to_json_line())
    assert payload["user"] == "abcd"
    assert payload["channel"] == "telegram"
    assert payload["tool_calls"] == [{"name": "set_control", "ok": True}]
    assert payload["final"] == "Erledigt."
    assert payload["model"] == "claude-opus-4-7"
    assert payload["duration_ms"] == 540


def test_audit_record_omits_empty_extra() -> None:
    record = AuditRecord(
        timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        user_id="u",
        channel="telegram",
        input_text="hi",
    )
    payload = json.loads(record.to_json_line())
    assert "extra" not in payload


def test_audit_record_writes_extra_when_present() -> None:
    record = AuditRecord(
        timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        user_id="u",
        channel="telegram",
        input_text="hi",
        extra={"voice": True, "language": "de"},
    )
    payload = json.loads(record.to_json_line())
    assert payload["extra"] == {"voice": True, "language": "de"}


def test_audit_record_handles_unicode_in_text() -> None:
    record = AuditRecord(
        timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        user_id="u",
        channel="telegram",
        input_text="Mach das Wohnzimmerlicht ⭐ aus",
        final_text="Erledigt — 21 °C.",
    )
    payload = json.loads(record.to_json_line())
    assert payload["input"] == "Mach das Wohnzimmerlicht ⭐ aus"
    assert payload["final"] == "Erledigt — 21 °C."


async def test_audit_log_appends_jsonl_lines(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    record1 = AuditRecord(
        timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        user_id="u1",
        channel="telegram",
        input_text="hi",
    )
    record2 = AuditRecord(
        timestamp=dt.datetime(2026, 1, 1, 0, 1, tzinfo=dt.UTC),
        user_id="u2",
        channel="telegram",
        input_text="bye",
    )
    await log.append(record1)
    await log.append(record2)

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["user"] == "u1"
    assert json.loads(lines[1])["user"] == "u2"


async def test_audit_log_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "missing" / "dirs" / "audit.jsonl"
    AuditLog(nested)
    assert nested.parent.is_dir()


async def test_audit_log_concurrent_writes_dont_interleave(tmp_path: Path) -> None:
    """All lines must be intact JSON, no partial writes from concurrent tasks."""
    log = AuditLog(tmp_path / "audit.jsonl")

    async def write(i: int) -> None:
        await log.append(
            AuditRecord(
                timestamp=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
                user_id=f"u{i}",
                channel="telegram",
                input_text="x" * 200,
            )
        )

    await asyncio.gather(*(write(i) for i in range(50)))
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50
    for line in lines:
        # Must parse cleanly — proves no interleaving.
        json.loads(line)


async def test_audit_log_append_turn_helper(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    await log.append_turn(
        user_id_hash="abcd",
        channel="telegram",
        input_text="hi",
        final_text="hi back",
        tool_calls=[{"name": "ping"}],
        model="claude-opus-4-7",
        duration_ms=100,
    )
    line = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0]
    payload = json.loads(line)
    assert payload["user"] == "abcd"
    assert payload["duration_ms"] == 100
    assert payload["tool_calls"] == [{"name": "ping"}]


def test_audit_log_path_is_exposed(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    assert log.path == tmp_path / "audit.jsonl"


def test_audit_log_accepts_string_path(tmp_path: Path) -> None:
    log = AuditLog(str(tmp_path / "audit.jsonl"))
    assert isinstance(log.path, Path)


@pytest.mark.parametrize("raw_id", [1, "abc", 99_999_999_999])
def test_hash_user_id_handles_various_inputs(raw_id: int | str) -> None:
    h = hash_user_id(raw_id)
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)
