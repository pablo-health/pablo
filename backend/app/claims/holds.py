# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Refusing to bill a client from a remittance that contradicts itself.

:mod:`app.claims.remittance_lines` decides whether an 835's numbers account
for each other; this is what the posting path does about it.

Only the client's bill is held — the payer's payment, the state advance and
the receipt all post. What is in doubt is the split, not the money that
arrived.

Nothing releases a hold but a person: no timeout, no auto-approve. And the
withholding does not depend on the recording — a deployment with no hold
repository still refuses to bill, and says so.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from ..db.models import DEFAULT_CHARGE_CURRENCY
from ..models.claims_holds import FINDINGS_THAT_BILL, RemittanceHold

if TYPE_CHECKING:
    from datetime import datetime

    from ..models.claims import Claim
    from ..models.claims_holds import HoldFinding
    from ..models.claims_responses import RemittanceClaim
    from ..repositories.patient_payment import PatientPaymentRepository
    from ..repositories.remittance_hold import RemittanceHoldRepository
    from .remittance_lines import Disagreement

#: Lives here rather than in :mod:`app.claims.remittance`, which imports
#: this module.
PATIENT_RESPONSIBILITY_KIND = "patient_resp"

logger = logging.getLogger(__name__)


def codes_of(remittance: RemittanceClaim) -> list[dict[str, str]]:
    """Every adjustment code on the claim, claim level then line level.

    Codes only. A CARC is a number from a public list, so it is the one part
    of a remittance safe to carry onto a surface that must not hold clinical
    detail.
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

    The payer's name comes off the claim's subscriber snapshot, so it names
    the payer the claim was filed against even if the ``payers`` row has
    since changed. Nothing else is taken from the snapshot — the rest of it
    is a named person's details.
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

    ``None`` when there was nowhere to store it, or the posting was already
    held. Neither changes what the caller does: the ledger row stays
    withheld either way.

    A duplicate is INFO, not WARNING — a redelivered 835 is expected, and
    warnings that fire on expected things get skipped.
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


def settle(  # noqa: PLR0913 — the decision and everything it needs to write
    holds: RemittanceHoldRepository,
    hold: RemittanceHold,
    *,
    finding: HoldFinding,
    charges: PatientPaymentRepository | None,
    already_billed: int,
    user_id: str,
    now: datetime,
) -> RemittanceHold | None:
    """Close a hold the way a person decided, and write what that implies.

    ``bill_as_stated`` writes the withheld ledger row — the difference
    against the ledger now, not a figure stored when the hold was raised.
    Every other finding writes nothing.

    Both answers stay available for the whole life of a hold: nothing here
    consults its age, its acknowledgement, or anyone else.

    The ledger row is written BEFORE the hold resolves, so a failure leaves
    the hold open. "Click again" beats "never billed, and the record says
    they were".
    """
    if finding in FINDINGS_THAT_BILL:
        if charges is None:
            msg = "cannot bill a held remittance without a charge ledger"
            raise ValueError(msg)
        difference = withheld_cents(hold, already_billed=already_billed)
        if difference:
            charges.add_ledger_row(
                patient_id=hold.patient_id,
                kind=PATIENT_RESPONSIBILITY_KIND,
                amount_cents=difference,
                currency=DEFAULT_CHARGE_CURRENCY,
                user_id=user_id,
                claim_id=hold.claim_id,
                note=f"payer remittance {hold.posting_key}",
            )
    resolved = holds.resolve(hold.id, finding=finding, user_id=user_id, at=now)
    if resolved is not None:
        logger.info(
            "remittance_hold_resolved hold_id=%s finding=%s",
            resolved.id,
            resolved.finding,
        )
    return resolved


def withheld_cents(hold: RemittanceHold, *, already_billed: int) -> int:
    """The ledger row this hold is withholding, as of right now.

    A remittance states the client's balance rather than adding to it, so
    the row is the difference from what this claim already billed. Negative
    is a credit — a secondary payer clearing the primary's coinsurance.

    ``already_billed`` is passed in so this stays pure and the caller keeps
    one ledger read per posting.
    """
    return hold.patient_responsibility_cents - already_billed
