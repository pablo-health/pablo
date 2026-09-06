# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The claim state machine: every row of the table, and what is refused."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.claims.transitions import (
    CLAIM_EVENTS,
    TERMINAL_STATES,
    ClaimNotValidError,
    InvalidTransitionError,
    advance,
    next_state,
)
from app.db.models import CLAIM_STATES

from tests.claims_fixtures import TODAY, claim, line

_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

_EXPECTED = {
    ("draft", "validate"): "validated",
    ("validated", "submit"): "submitted",
    ("submitted", "ch_accept"): "ch_accepted",
    ("submitted", "reject"): "rejected",
    ("submitted", "stall"): "stalled",
    ("ch_accepted", "payer_accept"): "payer_accepted",
    ("ch_accepted", "reject"): "rejected",
    ("ch_accepted", "stall"): "stalled",
    ("payer_accepted", "pay"): "paid",
    ("payer_accepted", "pay_partial"): "partial",
    ("payer_accepted", "deny"): "denied",
    ("payer_accepted", "reject"): "rejected",
    ("payer_accepted", "stall"): "stalled",
    ("stalled", "ch_accept"): "ch_accepted",
    ("stalled", "payer_accept"): "payer_accepted",
    ("stalled", "pay"): "paid",
    ("stalled", "pay_partial"): "partial",
    ("stalled", "deny"): "denied",
    ("stalled", "reject"): "rejected",
}


@pytest.mark.parametrize("state", CLAIM_STATES)
@pytest.mark.parametrize("event", CLAIM_EVENTS)
def test_every_state_event_pair_matches_the_table(state: str, event: str) -> None:
    expected = _EXPECTED.get((state, event))
    assert next_state(state, event) == expected
    if expected is None:
        with pytest.raises(InvalidTransitionError):
            advance(claim(state=state), event, now=_NOW, today=TODAY)  # type: ignore[arg-type] — parametrized over the Literal's values
    else:
        moved = advance(claim(state=state), event, now=_NOW, today=TODAY)  # type: ignore[arg-type] — parametrized over the Literal's values
        assert moved.state == expected
        assert moved.updated_at == _NOW


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
@pytest.mark.parametrize("event", CLAIM_EVENTS)
def test_terminal_states_take_no_event(state: str, event: str) -> None:
    assert next_state(state, event) is None


def test_advance_returns_a_new_claim_and_leaves_the_input_alone() -> None:
    original = claim(state="validated")
    moved = advance(original, "submit", now=_NOW)
    assert original.state == "validated"
    assert original.submitted_at is None
    assert moved.state == "submitted"
    assert moved.submitted_at == _NOW


def test_payer_acceptance_and_adjudication_are_stamped() -> None:
    accepted = advance(claim(state="ch_accepted"), "payer_accept", now=_NOW)
    assert accepted.payer_accepted_at == _NOW
    assert accepted.adjudicated_at is None
    paid = advance(accepted, "pay", now=_NOW)
    assert paid.adjudicated_at == _NOW


def test_validate_refuses_a_claim_with_a_blocking_finding() -> None:
    broken = claim(lines=[line(charge_cents=0)], total_charge_cents=0)
    with pytest.raises(ClaimNotValidError) as excinfo:
        advance(broken, "validate", now=_NOW, today=TODAY)
    assert [f.code for f in excinfo.value.findings] == ["charge_zero"]


def test_validate_passes_a_claim_with_only_warnings() -> None:
    warned = claim(lines=[line(modifiers=[])])
    assert advance(warned, "validate", now=_NOW, today=TODAY).state == "validated"


def test_invalid_transition_names_state_and_event() -> None:
    with pytest.raises(InvalidTransitionError) as excinfo:
        advance(claim(state="draft"), "submit", now=_NOW)
    assert excinfo.value.state == "draft"
    assert excinfo.value.event == "submit"
