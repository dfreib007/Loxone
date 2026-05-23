"""Tests for `loxone_voice.gateway.confirmation`."""

from __future__ import annotations

from typing import Any

import pytest

from loxone_voice.gateway.confirmation import ConfirmationGate, PendingAction
from loxone_voice.intent import ConfirmationDecision

# ---------------------------------------------------------------------------
# PendingAction
# ---------------------------------------------------------------------------


def test_action_id_is_deterministic_across_argument_orders() -> None:
    a = PendingAction(
        tool_name="set_control",
        tool_arguments={"command": "On", "control_id": "abc"},
        summary="",
    )
    b = PendingAction(
        tool_name="set_control",
        tool_arguments={"control_id": "abc", "command": "On"},
        summary="ignored",
    )
    assert a.action_id == b.action_id
    assert len(a.action_id) == 16


def test_action_id_handles_unicode_safely() -> None:
    pa = PendingAction(
        tool_name="set_control",
        tool_arguments={"command": "ja", "label": "Wohnzimmerlicht ⭐"},
        summary="",
    )
    # No exception, stable id.
    assert isinstance(pa.action_id, str)


# ---------------------------------------------------------------------------
# ConfirmationGate
# ---------------------------------------------------------------------------


@pytest.fixture
def gate() -> ConfirmationGate:
    return ConfirmationGate()


async def test_unknown_action_defers(gate: ConfirmationGate) -> None:
    decision = await gate.decide("set_control", {"control_id": "x", "command": "On"})
    assert decision is ConfirmationDecision.DEFER


async def test_approve_then_decide_returns_approve_once(gate: ConfirmationGate) -> None:
    args: dict[str, Any] = {"control_id": "x", "command": "On"}
    action = PendingAction(tool_name="set_control", tool_arguments=args, summary="")
    gate.approve([action.action_id])

    first = await gate.decide("set_control", args)
    second = await gate.decide("set_control", args)
    assert first is ConfirmationDecision.APPROVE
    # Approval is one-shot: the next call defers again.
    assert second is ConfirmationDecision.DEFER


async def test_deny_then_decide_returns_deny_once(gate: ConfirmationGate) -> None:
    args: dict[str, Any] = {"control_id": "x", "command": "On"}
    action = PendingAction(tool_name="set_control", tool_arguments=args, summary="")
    gate.deny([action.action_id])

    first = await gate.decide("set_control", args)
    second = await gate.decide("set_control", args)
    assert first is ConfirmationDecision.DENY
    assert second is ConfirmationDecision.DEFER


async def test_approval_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Approvals that linger past ttl_s decay to DEFER."""
    fake_now = [1000.0]
    monkeypatch.setattr(
        "loxone_voice.gateway.confirmation.time.monotonic",
        lambda: fake_now[0],
    )

    gate = ConfirmationGate(ttl_s=60.0)
    args = {"control_id": "x", "command": "On"}
    action = PendingAction(tool_name="set_control", tool_arguments=args, summary="")
    gate.approve([action.action_id])

    fake_now[0] += 120.0  # well past the TTL
    decision = await gate.decide("set_control", args)
    assert decision is ConfirmationDecision.DEFER


async def test_clear_drops_all_state(gate: ConfirmationGate) -> None:
    gate.approve(["x", "y"])
    gate.deny(["z"])
    gate.clear()
    assert not gate.has_pending_approval("x")
    assert not gate.has_pending_approval("y")


def test_has_pending_approval_reflects_state(gate: ConfirmationGate) -> None:
    gate.approve(["abc"])
    assert gate.has_pending_approval("abc")
    assert not gate.has_pending_approval("def")


async def test_approve_multiple_ids_at_once(gate: ConfirmationGate) -> None:
    a_args = {"control_id": "a", "command": "On"}
    b_args = {"control_id": "b", "command": "On"}
    a_id = PendingAction(tool_name="set_control", tool_arguments=a_args, summary="").action_id
    b_id = PendingAction(tool_name="set_control", tool_arguments=b_args, summary="").action_id
    gate.approve([a_id, b_id])

    assert await gate.decide("set_control", a_args) is ConfirmationDecision.APPROVE
    assert await gate.decide("set_control", b_args) is ConfirmationDecision.APPROVE
