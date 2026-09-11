# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Refusing to bill a client from a remittance that contradicts itself.

:mod:`app.claims.remittance_lines` works out *whether* an 835's own numbers
account for each other. This is what the posting path does about it: raise a
hold, withhold the client's ledger row, and leave the payer's payment
exactly where it was.

**Only the client's bill is held.** The payer's money is a fact — it landed
in the practice's account, and refusing to record it would not un-happen it,
it would only make a paid claim look unpaid and send somebody chasing a
payer who has already paid. What is in doubt is the *split*: how much of
the gap between charged and paid the client owes. So the certain half posts
and the uncertain half waits for a person.

**Nothing releases a hold but a person.** There is no timeout and no
auto-approve. Time passing is not evidence that a self-contradicting
remittance was right, and a hold that expires quietly into a bill is the
failure this whole module exists to prevent.

**The withholding does not depend on the recording.** A deployment with no
hold repository configured still withholds the row; it just logs that it
could not write the hold down. The safety property is the point, and making
it conditional on a backstop being present is how a safety property turns
into a preference.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from ..models.claims_holds import RemittanceHold

if TYPE_CHECKING:
    from datetime import datetime

    from ..models.claims import Claim
    from ..models.claims_responses import RemittanceClaim
    from ..repositories.remittance_hold import RemittanceHoldRepository
    from .remittance_lines import Disagreement

logger = logging.getLogger(__name__)


def codes_of(remittance: RemittanceClaim) -> list[dict[str, str]]:
    """Every adjustment code on the claim, claim level then line level.

    Codes only — the group and the reason — in the order the payer sent
    them. A CARC is a number from a public list and says nothing about a
    person, which is what makes it the one part of a remittance safe to
    carry onto a surface that must not hold clinical detail.
    """
    on_lines = (a for line in remittance.lines for a in line.adjustments)
    return [
        {"group_code": a.group_code, "reason_code": a.reason_code}
        for a in [*remittance.adjustments, *on_lines]
    ]


def hold_for(  # noqa: PLR0913 — the hold's own shape, keyword-only
    claim: Claim,
    remittance: RemittanceClaim,
    disagreement: Disagreement,
    *,
    patient_responsibility_cents: int,
    posting_key: str,
    now: datetime,
) -> RemittanceHold:
    """The hold this disagreement calls for, unsaved.

    Pure, so the shape of a hold can be asserted without a database.

    The payer's name is read off the claim's own subscriber snapshot rather
    than looked up: the snapshot is what the claim was filed against, so it
    names the payer that produced this remittance even if the practice has
    since renamed or replaced the ``payers`` row. Nothing else is taken
    from the snapshot — it also holds a named person's details, and none of
    them belong on a hold.
    """
    return RemittanceHold(
        id=str(uuid.uuid4()),
        claim_id=claim.id,
        patient_id=claim.patient_id,
        control_number=claim.control_number,
        posting_key=posting_key,
        state="open",
        reason=disagreement.reason,
        stated_cents=disagreement.stated_cents,
        computed_cents=disagreement.computed_cents,
        patient_responsibility_cents=patient_responsibility_cents,
        line_control_number=disagreement.line_control_number,
        codes=codes_of(remittance),
        line_count=len(remittance.lines),
        payer_name=claim.subscriber_snapshot.payer_name,
        detected_at=now,
    )


def record(
    holds: RemittanceHoldRepository | None,
    hold: RemittanceHold,
) -> RemittanceHold | None:
    """Write the hold down, or say plainly that it could not be.

    Returns the stored hold, or ``None`` when there was nowhere to store it
    or the same posting had already been held. Neither outcome changes what
    the caller does about the ledger row: the row stays withheld either
    way.

    The duplicate case is the ordinary one on a redelivered remittance and
    is logged at INFO, not WARNING — a payer or a vendor sending the same
    835 twice is expected behaviour, and a warning that fires on expected
    behaviour teaches a reader to skip warnings.
    """
    if holds is None:
        logger.warning(
            "remittance_hold_unrecorded claim_id=%s reason=%s "
            "posting_key=%s — ledger row withheld, no hold repository configured",
            hold.claim_id,
            hold.reason,
            hold.posting_key,
        )
        return None
    try:
        stored = holds.add(hold)
    except ValueError:
        logger.info(
            "remittance_hold_duplicate claim_id=%s posting_key=%s",
            hold.claim_id,
            hold.posting_key,
        )
        return None
    logger.warning(
        "remittance_hold_opened hold_id=%s claim_id=%s reason=%s stated=%d computed=%d",
        stored.id,
        stored.claim_id,
        stored.reason,
        stored.stated_cents,
        stored.computed_cents,
    )
    return stored


def withheld_cents(hold: RemittanceHold, *, already_billed: int) -> int:
    """The ledger row this hold is withholding, as of right now.

    Computed rather than stored, and computed against the ledger, because
    the ledger is where the answer lives: a remittance states the client's
    balance rather than adding to it, so the row is the difference from
    what this claim has already billed. A secondary payer that paid off the
    primary's coinsurance makes that difference negative, which is a credit
    and is exactly right.

    ``already_billed`` comes from
    :func:`app.claims.remittance.patient_responsibility_billed`, which reads
    the ledger. Kept as an argument rather than read here so this stays
    pure and the caller keeps one ledger read per posting.
    """
    return hold.patient_responsibility_cents - already_billed
