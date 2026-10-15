# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The live-grant predicate every patient-scoped repository joins through.

A clinician reaches a patient's rows through a ``patient_clinicians`` grant
that has not expired. Kept in one place so expiry semantics never drift
between repositories.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import or_

from ...db.models import PatientClinicianRow
from ...utcnow import utc_now

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement


def grant_not_expired() -> ColumnElement[bool]:
    """The joined ``patient_clinicians`` grant has no expiry, or one still ahead."""
    return or_(
        PatientClinicianRow.expires_at.is_(None),
        PatientClinicianRow.expires_at > utc_now(),
    )


def live_grant(user_id: str) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    """Predicates for "``user_id`` holds a non-expired grant on the joined row's patient".

    A tuple for ``.where(*live_grant(user_id))``.
    """
    return (PatientClinicianRow.user_id == user_id, grant_not_expired())
