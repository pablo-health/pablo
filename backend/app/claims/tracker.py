# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a person does next with a claim, and where it has been, for the tracker to render.

Kept here, so the tracker row and the detail view say the same thing
without either deriving it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models.claims import ClaimHop

if TYPE_CHECKING:
    from ..models.claims import Claim, ClaimReceipt, NextAction

_NEXT_ACTIONS: dict[str, NextAction] = {
    "draft": "review_and_file",
    "validated": "queued_to_send",
    # Nothing for the practice to do, and nothing wrong with the claim — so
    # the wording says what is happening and stops there. "Being checked"
    # rather than "held", "blocked" or "pending approval": a first claim to a
    # payer is read before it goes because that is the one worth reading, not
    # because this claim looks bad, and a word that hints otherwise invites a
    # worried question the screen cannot answer.
    "in_review": "being_checked",
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


#: The states at or past each hop, in the order the claim reaches them. A
#: rejection or a stall keeps whatever hops the claim had already passed,
#: which its timestamps say; the table only decides the untimestamped one.
_PAST_CLEARINGHOUSE: frozenset[str] = frozenset(
    {"ch_accepted", "payer_accepted", "paid", "partial", "denied"}
)


def hops(claim: Claim, receipts: list[ClaimReceipt]) -> list[ClaimHop]:
    """Where the claim has been, read off its receipt timestamps.

    The clearinghouse hop has no column of its own on the row; its moment
    is the ``ch_accepted`` receipt's, when the pipeline recorded one.
    """
    clearinghouse_at = next(
        (
            r.occurred_at
            for r in receipts
            if r.kind == "ch_accepted" and r.to_state == "ch_accepted"
        ),
        None,
    )
    return [
        ClaimHop(kind="built", reached=True, at=claim.created_at),
        ClaimHop(kind="submitted", reached=claim.submitted_at is not None, at=claim.submitted_at),
        ClaimHop(
            kind="clearinghouse_accepted",
            reached=(
                claim.state in _PAST_CLEARINGHOUSE
                or claim.payer_accepted_at is not None
                or clearinghouse_at is not None
            ),
            at=clearinghouse_at,
        ),
        ClaimHop(
            kind="payer_accepted",
            reached=claim.payer_accepted_at is not None,
            at=claim.payer_accepted_at,
        ),
        ClaimHop(
            kind="adjudicated", reached=claim.adjudicated_at is not None, at=claim.adjudicated_at
        ),
    ]
