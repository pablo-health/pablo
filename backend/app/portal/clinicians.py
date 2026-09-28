# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who a practice's clinicians are, and whose client a patient is.

Two portal paths need a clinician they cannot see from where they stand.
Account recovery runs before anybody has signed in and has to find a chart by
email address. An invitation names the client's primary clinician, and the
person sending it is often somebody else — front-desk staff send invitations
too.

Both are blocked by the same thing: ``patient_clinicians`` is visible only to
the clinician each grant belongs to, and ``patients`` only to clinicians with
a grant. So both read as each clinician of the practice in turn, one statement
each, through :func:`app.db.read_once_as`; nothing stays armed afterwards.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends
from sqlalchemy import func, or_, select

from ..auth.service import TenantContext, get_tenant_context
from ..db import get_db_session, read_once_as
from ..db.models import PatientClinicianRow
from ..db.platform_models import EmailTenantMappingRow, PlatformUserRow, PracticeRow
from ..utcnow import utc_now

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: patient_id -> the primary clinician's name as they entered it, or ``None``.
ClinicianName = Callable[[str], str | None]


def practice_clinicians(session: Session, schema: str) -> list[str]:
    """Every clinician user id this practice has: its members and its owner.

    From the platform schema, which is where practice membership lives and
    which carries no row-level security of its own. Membership is the
    address-to-practice mapping every clinician signs in through.
    """
    members = session.execute(
        select(PlatformUserRow.id)
        .join(
            EmailTenantMappingRow,
            func.lower(EmailTenantMappingRow.email) == func.lower(PlatformUserRow.email),
        )
        .join(PracticeRow, PracticeRow.id == EmailTenantMappingRow.practice_id)
        .where(PracticeRow.schema_name == schema)
    ).scalars()
    owner = session.execute(
        select(PracticeRow.owner_user_id).where(PracticeRow.schema_name == schema)
    ).scalar_one_or_none()
    ids = {str(member) for member in members}
    if owner is not None:
        ids.add(str(owner))
    return sorted(ids)


def primary_clinician_name(session: Session, schema: str, patient_id: str) -> str | None:
    """The name of this patient's primary clinician, as they entered it.

    ``None`` when the chart has no live primary grant, or the clinician has
    no name on file — the caller decides what stands in.
    """
    for clinician_id in practice_clinicians(session, schema):
        # Each clinician can see only their own grants, so the question is put
        # to each in turn: "is this patient yours, as primary?"
        rows = read_once_as(
            session,
            principal="app.current_user_id",
            value=clinician_id,
            statement=select(PatientClinicianRow.user_id).where(
                PatientClinicianRow.patient_id == patient_id,
                PatientClinicianRow.user_id == clinician_id,
                PatientClinicianRow.role == "primary",
                or_(
                    PatientClinicianRow.expires_at.is_(None),
                    PatientClinicianRow.expires_at > utc_now(),
                ),
            ),
        )
        if rows:
            name = session.execute(
                select(PlatformUserRow.name).where(PlatformUserRow.id == clinician_id)
            ).scalar_one_or_none()
            return name.strip() if name and name.strip() else None
    return None


def get_primary_clinician_name(
    tenant_ctx: Annotated[TenantContext, Depends(get_tenant_context)],
) -> ClinicianName:
    """FastAPI dependency: the primary-clinician lookup, on the caller's practice.

    Reads through the request's own session, which the database middleware
    has already scoped to the caller's practice.
    """
    schema = str(tenant_ctx.practice_schema or "")

    def lookup(patient_id: str) -> str | None:
        if not schema:
            return None
        return primary_clinician_name(get_db_session(), schema, patient_id)

    return lookup
