# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the claims tracker shows about a claim: its receipts, its clock,
and what a person does next.

Pure view helpers over the stored claim: the deadline comes from
:func:`app.claims.deadlines.deadlines_for` (the same function the watchdog
raises alerts from, so the two never disagree) and the next action from
the state alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models.claims import ClaimDeadlineResponse, ClaimDetailResponse, NextAction
from .deadlines import deadlines_for

if TYPE_CHECKING:
    from datetime import date

    from ..models.claims import Claim, ClaimReceipt
    from ..models.coverage import Payer

#: States under the remittance clocks: the 835 that denied or short-paid
#: the claim is what ``adjudicated_at`` was stamped by.
_REMITTANCE_STATES = frozenset({"denied", "partial"})

_NEXT_ACTIONS: dict[str, NextAction] = {
    "draft": "review_and_file",
    "validated": "queued_to_send",
    "submitted": "await_acknowledgment",
    "ch_accepted": "await_payer",
    "payer_accepted": "await_remittance",
    "partial": "review_remittance",
    "rejected": "correct_and_resubmit",
    "denied": "appeal_or_correct",
    "stalled": "check_with_clearinghouse",
}


def next_action_for(claim: Claim) -> NextAction | None:
    """What a person does with the claim now; ``None`` on a paid one."""
    if claim.state == "validated" and claim.submission_pending_at is not None:
        return "sending"
    return _NEXT_ACTIONS.get(claim.state)


def deadline_for(claim: Claim, payer: Payer | None, *, today: date) -> ClaimDeadlineResponse | None:
    """The claim's clocks as the tracker renders them; ``None`` without a payer."""
    if payer is None:
        return None
    remittance_at = claim.adjudicated_at if claim.state in _REMITTANCE_STATES else None
    deadlines = deadlines_for(claim, payer, remittance_at, today=today)
    return ClaimDeadlineResponse(
        filing=deadlines.filing,
        correction=deadlines.correction,
        appeal=deadlines.appeal,
        applicable=deadlines.applicable,
        days_left=deadlines.days_left,
    )


def detail_response(
    claim: Claim, receipts: list[ClaimReceipt], payer: Payer | None, *, today: date
) -> ClaimDetailResponse:
    """The claim with everything the tracker and the claim page show."""
    return ClaimDetailResponse(
        **claim.model_dump(),
        receipts=receipts,
        deadline=deadline_for(claim, payer, today=today),
        next_action=next_action_for(claim),
    )
