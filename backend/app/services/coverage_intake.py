# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Turning what somebody typed into payer and coverage rows.

Shared by the chart card (a clinician adding a plan from the card in front
of them) and intake (the client typing the same card before any chart
exists). Both end in the same two rows; this module is where the shape of
those rows is decided once.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..db.models import DEFAULT_APPEAL_DAYS, DEFAULT_CORRECTED_CLAIM_DAYS
from ..models.coverage import PatientCoverage, Payer
from ..utcnow import utc_now
from .payer_defaults import default_timely_filing_days

if TYPE_CHECKING:
    from ..models.coverage import IntakeCoverage
    from ..repositories.coverage import PatientCoverageRepository, PayerRepository

#: What a payer typed from a card gets as its electronic id when the card
#: shows none. A placeholder the practice replaces from the payer directory
#: before anything is filed; kept distinct from a real id on purpose.
UNKNOWN_PAYER_ID = "UNKNOWN"


def new_payer(
    *,
    name: str,
    payer_id: str,
    is_carveout: bool = False,
    carveout_of: str | None = None,
    timely_filing_days: int | None = None,
    corrected_claim_days: int | None = None,
    appeal_days: int | None = None,
) -> Payer:
    """A payer row as the practice adds one, deadlines defaulted for the id."""
    now = utc_now()
    if timely_filing_days is None:
        timely_filing_days = default_timely_filing_days(payer_id)
    if corrected_claim_days is None:
        corrected_claim_days = DEFAULT_CORRECTED_CLAIM_DAYS
    if appeal_days is None:
        appeal_days = DEFAULT_APPEAL_DAYS
    return Payer(
        id=str(uuid.uuid4()),
        name=name.strip(),
        payer_id=payer_id.strip(),
        is_carveout=is_carveout,
        carveout_of=carveout_of,
        timely_filing_days=timely_filing_days,
        corrected_claim_days=corrected_claim_days,
        appeal_days=appeal_days,
        created_at=now,
        updated_at=now,
    )


def find_or_create_typed_payer(
    payers: PayerRepository, *, name: str, payer_id: str | None
) -> Payer:
    """The payer somebody typed: an existing match on the list, else a new row."""
    existing = payers.find_typed(name, payer_id)
    if existing is not None:
        return existing
    return payers.create(new_payer(name=name, payer_id=payer_id or UNKNOWN_PAYER_ID))


def record_intake_coverage(
    patient_id: str,
    intake: IntakeCoverage,
    payers: PayerRepository,
    coverage: PatientCoverageRepository,
) -> PatientCoverage:
    """Put the plan a client typed at intake on file for their new chart."""
    payer = find_or_create_typed_payer(payers, name=intake.payer_name, payer_id=intake.payer_id)
    now = utc_now()
    return coverage.create(
        PatientCoverage(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            payer_id=payer.id,
            member_id=intake.member_id.strip(),
            group_number=intake.group_number,
            plan_name=intake.plan_name,
            subscriber_relationship=intake.subscriber_relationship,
            subscriber_first_name=intake.subscriber_first_name,
            subscriber_last_name=intake.subscriber_last_name,
            subscriber_date_of_birth=intake.subscriber_date_of_birth,
            created_at=now,
            updated_at=now,
        )
    )
