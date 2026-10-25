# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The live-grant predicate every patient-scoped repository joins through.

A clinician reaches a patient's rows through a ``patient_clinicians`` grant
that is in force: it has started and has not expired. Kept in one place so
those semantics never drift between repositories, nor from
``has_patient_access``, which applies the same two checks in the database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, or_

from ...db.models import PatientClinicianRow
from ...utcnow import utc_now

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement


def grant_in_force() -> ColumnElement[bool]:
    """The joined ``patient_clinicians`` grant has started and not yet expired."""
    now = utc_now()
    return and_(
        or_(PatientClinicianRow.starts_at.is_(None), PatientClinicianRow.starts_at <= now),
        or_(PatientClinicianRow.expires_at.is_(None), PatientClinicianRow.expires_at > now),
    )


def live_grant(user_id: str) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    """Predicates for "``user_id`` holds a grant in force on the joined row's patient".

    A tuple for ``.where(*live_grant(user_id))``.
    """
    return (PatientClinicianRow.user_id == user_id, grant_in_force())
