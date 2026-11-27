# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who may bill for the practice, and the one read that lets them.

:func:`can_bill` is the only test of the billing capability: a later billing
role adds itself there, so a caller never checks "is owner" itself.
:func:`read_claim_source` is the claim fields of any session in the practice,
read past the row policies through ``practice_claim_source``
(``app.db.practice_directory``); it refuses anyone :func:`can_bill` refuses.
:func:`read_enrollment_status` is the same for where the visit's clinician
stands with the claim's payer, and also answers that clinician.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from . import repositories
from .credentialing.participation import DEFAULT_STATUS
from .db.practice_directory import CLAIM_SOURCE_FUNCTION, ENROLLMENT_FUNCTION
from .db.practice_owner import is_recorded_owner

if TYPE_CHECKING:
    from datetime import date, datetime

    from sqlalchemy.orm import Session

    from .db.platform_models import PracticeRow
    from .models import User
    from .models.claims import Claim


def can_bill(practice: PracticeRow, user: User) -> bool:
    """Whether ``user`` may build, correct or void a claim for any session in ``practice``.

    Today: the practice owner or a practice admin. A blank ``owner_email``
    names nobody here, unlike the settings gate, so only the recorded owner counts.
    """
    owner_email = practice.owner_email.strip().lower()
    if is_recorded_owner(practice, user.id) or (owner_email and owner_email == user.email.lower()):
        return True
    return repositories.get_practice_admin_repository().is_admin(user.id)


@dataclass(frozen=True)
class ClaimSource:
    """One visit as a claim names it; the columns ``practice_claim_source`` returns.

    The coverage fields are ``None`` when the client has no active coverage,
    the fee when the visit has no type, the NPI and taxonomy when the
    clinician has no profile.
    """

    appointment_id: str
    clinician_id: str
    patient_id: str
    appointment_type_id: str | None
    start_at: datetime
    service_code: str | None
    unit_count: int | None
    modifiers: list[str] | None
    diagnosis_codes: list[str] | None
    place_of_service: str | None
    video_link: str | None
    first_name: str
    last_name: str
    date_of_birth: date | None
    sex: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    phone: str | None
    rate_cents: int | None
    coverage_id: str | None
    payer_id: str | None
    member_id: str | None
    group_number: str | None
    plan_name: str | None
    subscriber_relationship: str | None
    subscriber_first_name: str | None
    subscriber_last_name: str | None
    subscriber_date_of_birth: date | None
    subscriber_sex: str | None
    subscriber_address_line1: str | None
    subscriber_address_line2: str | None
    subscriber_city: str | None
    subscriber_state: str | None
    subscriber_postal_code: str | None
    default_fee_cents: int | None
    npi_number: str | None
    taxonomy_code: str | None


_IDS = frozenset(
    {
        "appointment_id",
        "clinician_id",
        "patient_id",
        "appointment_type_id",
        "coverage_id",
        "payer_id",
    }
)


def read_claim_source(
    db: Session, practice: PracticeRow, user: User, appointment_id: str
) -> ClaimSource | None:
    """The claim fields of ``appointment_id``, for someone who may bill; else ``None``.

    ``None`` too for an unknown or malformed id, or a deleted client, so a
    refusal and a miss look the same. ``db`` is the request's session, scoped
    to ``practice``'s schema with ``user`` armed.
    """
    if not can_bill(practice, user):
        return None
    try:
        wanted = uuid.UUID(appointment_id)
    except ValueError:
        return None
    statement = text(f"SELECT * FROM {CLAIM_SOURCE_FUNCTION}(:id)")  # noqa: S608 — a constant
    row = db.execute(statement, {"id": wanted}).mappings().first()
    if row is None:
        return None
    values: dict[str, Any] = {
        f.name: (str(row[f.name]) if f.name in _IDS and row[f.name] is not None else row[f.name])
        for f in fields(ClaimSource)
    }
    return ClaimSource(**values)


def read_enrollment_status(
    db: Session, practice: PracticeRow, user: User, claim: Claim
) -> str | None:
    """The visit's clinician's participation status with ``claim``'s payer, or ``None``.

    ``None`` when the claim names no visit, the visit is gone, or ``user`` is
    neither the claim's clinician nor someone who may bill. No row is
    out-of-network, as in ``participation.status_for``.
    """
    visit = next((line.appointment_id for line in claim.lines if line.appointment_id), None)
    if visit is None:
        return None
    if user.id != claim.owner_user_id and not can_bill(practice, user):
        return None
    try:
        ids = {"appointment": uuid.UUID(visit), "payer": uuid.UUID(claim.payer_id)}
    except ValueError:
        return None
    statement = text(
        f"SELECT status FROM {ENROLLMENT_FUNCTION}(:appointment, :payer)"  # noqa: S608 — a constant
    )
    row = db.execute(statement, ids).first()
    if row is None:
        return None
    return str(row.status) if row.status is not None else DEFAULT_STATUS
