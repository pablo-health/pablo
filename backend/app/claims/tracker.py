# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a person does next with a claim, for the tracker to render.

Read off the state alone, so the tracker row and the detail view say the
same thing without either deriving it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.claims import Claim, NextAction

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
