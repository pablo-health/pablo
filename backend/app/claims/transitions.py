# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The claim state machine, as a table.

A claim only ever moves on a receipt from the next hop::

    draft -> validated -> submitted -> ch_accepted -> payer_accepted
                                                   -> paid | partial | denied
    submitted | ch_accepted | payer_accepted -> rejected   (a refusal)
    submitted | ch_accepted | payer_accepted -> stalled    (a watchdog timeout)
    stalled -> whatever the late receipt says

``validated`` is the one transition with a precondition of its own: the
scrub must return no blocking finding. Everything not in the table raises
:class:`InvalidTransitionError` — there is no "force" path. A claim that
must change after it was validated is not moved back to ``draft``; it is
corrected or voided into a child claim (see :mod:`app.claims.assembly`).

Pure: :func:`advance` returns a new claim and touches nothing else. The
caller persists it and records whatever artifact drove the event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, get_args

from .scrub import blocking, scrub

if TYPE_CHECKING:
    from datetime import date, datetime

    from ..models.claims import Claim, ClaimState
    from .scrub import Finding

ClaimEvent = Literal[
    "validate",
    "submit",
    "ch_accept",
    "payer_accept",
    "pay",
    "pay_partial",
    "deny",
    "reject",
    "stall",
]

CLAIM_EVENTS: tuple[str, ...] = get_args(ClaimEvent)


class InvalidTransitionError(Exception):
    """``event`` is not a move the claim can make from ``state``."""

    def __init__(self, state: str, event: str) -> None:
        super().__init__(f"A claim in state {state!r} cannot take event {event!r}.")
        self.state = state
        self.event = event


class ClaimNotValidError(Exception):
    """The scrub found something blocking; the claim stays a draft."""

    def __init__(self, findings: list[Finding]) -> None:
        super().__init__(f"{len(findings)} blocking finding(s).")
        self.findings = findings


#: (state, event) -> next state. The whole machine; read it top to bottom.
_TRANSITIONS: dict[tuple[str, str], ClaimState] = {
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
    # A stalled claim is one nobody has heard from, not one that is over;
    # the receipt, when it comes, moves it the same way it would have.
    ("stalled", "ch_accept"): "ch_accepted",
    ("stalled", "payer_accept"): "payer_accepted",
    ("stalled", "pay"): "paid",
    ("stalled", "pay_partial"): "partial",
    ("stalled", "deny"): "denied",
    ("stalled", "reject"): "rejected",
}

#: States nothing moves out of. ``rejected`` is terminal for this row — the
#: answer to it is a corrected child claim, not another event here.
TERMINAL_STATES: frozenset[str] = frozenset({"paid", "partial", "denied", "rejected"})


def next_state(state: str, event: str) -> ClaimState | None:
    """Where ``event`` takes a claim in ``state``, or ``None`` if nowhere."""
    return _TRANSITIONS.get((state, event))


def advance(
    claim: Claim,
    event: ClaimEvent,
    *,
    now: datetime,
    today: date | None = None,
) -> Claim:
    """The claim after ``event``, or an exception saying why not.

    Stamps ``submitted_at`` / ``payer_accepted_at`` / ``adjudicated_at``
    with ``now`` as the matching event goes by. ``today`` only feeds the
    scrub's date-of-birth rule and defaults to the calendar date.
    """
    target = next_state(claim.state, event)
    if target is None:
        raise InvalidTransitionError(claim.state, event)
    if event == "validate":
        stops = blocking(scrub(claim, today=today))
        if stops:
            raise ClaimNotValidError(stops)

    changes: dict[str, object] = {"state": target, "updated_at": now}
    if event == "submit":
        changes["submitted_at"] = now
    elif event == "payer_accept":
        changes["payer_accepted_at"] = now
    elif event in ("pay", "pay_partial", "deny"):
        changes["adjudicated_at"] = now
    return claim.model_copy(update=changes)
