# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Why a claim should be read by a person before it is filed.

One question asked at one place: the filing boundary. Every path that files a
claim goes through :func:`app.claims.submit_worker.submit_pending`, so a check
there covers all of them, and a filing path added later cannot be written that
forgets to ask. Asking at validation instead would leave any direct-to-submit
path free to skip it — and the paths that skip a check are the ones nobody
remembers exist.

The answers are REASONS, not a boolean, because a reviewer who is told only
"this needs looking at" has to work out what to look at. A reason names the
question, and the operator surface can put that question on the screen.

What this is for: a denial is expensive and slow. Everything payer-specific
about a claim is unproven until one actually goes — the payer id resolved from
the directory, whether enrollment is live, the submitter identification, the
rate, and whether this payer is even the entity that pays for this kind of
care. A mistake in any of them does not fail on the first claim; it comes back
days later, by which time every claim since has gone out the same wrong way.
Holding the first one costs a day. Not holding it costs the backlog.

Off unless a deployment asks for it (``HOLD_FIRST_CLAIM_TO_PAYER``). A
deployment whose operator has no intention of reading claims should not
accumulate held ones — a hold nobody clears is a claim that never gets filed,
which is worse than the denial it was guarding against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..models.claims import Claim
    from ..models.coverage import Payer
    from ..repositories.claims import ClaimRepository

#: Why a claim is being held. Kept narrow on purpose — each reason has to be
#: something a reviewer can actually answer, not a general misgiving.
HoldReasonCode = Literal["first_claim_to_payer"]


@dataclass(frozen=True, slots=True)
class HoldReason:
    """One answerable question about a claim that is about to be filed."""

    code: HoldReasonCode
    #: What the reviewer is being asked, in their words. Short: it goes on a
    #: queue row beside the claim, not in a document.
    question: str
    #: The payer the question is about, so the surface can name it without
    #: re-reading the claim.
    payer_name: str


def reasons_to_hold(
    claim: Claim,  # noqa: ARG001 — part of the seam; the carve-out reason reads its codes
    payer: Payer | None,
    *,
    claims: ClaimRepository,
    hold_first_claim: bool,
) -> list[HoldReason]:
    """Every reason to have a person read ``claim`` before filing it.

    Empty means file it. ``payer`` is ``None`` when the claim's payer cannot be
    resolved, which is NOT a reason to hold: a claim with no payer row cannot
    be filed at all and fails further along with a better message than a hold
    would give. Holding it here would turn a clear failure into a queue item
    nobody can act on.

    ``claim`` is unused today and is part of the signature rather than added
    later on purpose: the carve-out check (does this payer administer
    behavioural benefits, or does someone else?) needs the claim's service
    codes, and a seam that has to change shape to accept its second caller is
    a seam that gets bypassed instead.
    """
    if payer is None:
        return []
    reasons: list[HoldReason] = []
    if hold_first_claim and not claims.any_accepted_for_payer(payer.id):
        reasons.append(
            HoldReason(
                code="first_claim_to_payer",
                question=(
                    f"First claim to {payer.name}. Is this the right payer for this "
                    f"care, and is the practice set up to bill it?"
                ),
                payer_name=payer.name,
            )
        )
    return reasons
