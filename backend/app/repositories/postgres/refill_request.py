# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL RefillRequestRepository implementation.

The clinician arm reaches rows two ways, both the ``patient_clinicians``
grant: a single request through the ``has_patient_access`` SQL function,
and the cross-patient queue through a join on the grant table, the same
join the chat repository uses for its lists. The patient arm matches on the
principal's ``patient_id``. Row security tests both independently
underneath, so a query that forgot its filter still sees nothing it should
not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, or_, select, text, update

from ...db.models import PatientClinicianRow, PatientMedicationRow, PatientRow, RefillRequestRow
from ...models.refill_request import REFILL_STATUS_REQUESTED, RefillQueueEntry, RefillRequest
from ...utcnow import utc_now
from ..refill_request import (
    RECENT_DECISIONS_LIMIT,
    RefillQueueView,
    RefillRequestAccessDeniedError,
    RefillRequestAlreadyDecidedError,
    RefillRequestRepository,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Select
    from sqlalchemy.orm import Session

_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)

_ACTIVE_MEDICATION = (
    PatientMedicationRow.status == "active",
    PatientMedicationRow.deleted_at.is_(None),
)


def _request(row: RefillRequestRow) -> RefillRequest:
    return RefillRequest(
        id=row.id,
        patient_id=row.patient_id,
        medication_id=row.medication_id,
        medication_text=row.medication_text,
        pharmacy_text=row.pharmacy_text,
        patient_note=row.patient_note,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        decided_by_user_id=row.decided_by_user_id,
        decided_at=row.decided_at,
        prescriber_note=row.prescriber_note,
    )


def _entry_query(user_id: str) -> Select[tuple[RefillRequestRow, str, str, str | None]]:
    """Requests joined to their patient's name, through a live grant for *user_id*."""
    return (
        select(
            RefillRequestRow,
            PatientRow.first_name,
            PatientRow.last_name,
            PatientRow.preferred_name,
        )
        .join(PatientRow, PatientRow.id == RefillRequestRow.patient_id)
        .join(PatientClinicianRow, PatientClinicianRow.patient_id == RefillRequestRow.patient_id)
        .where(
            PatientClinicianRow.user_id == user_id,
            or_(
                PatientClinicianRow.expires_at.is_(None),
                PatientClinicianRow.expires_at > utc_now(),
            ),
        )
    )


class PostgresRefillRequestRepository(RefillRequestRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _has_access(self, patient_id: str, user_id: str) -> bool:
        return bool(
            self._session.execute(
                _HAS_PATIENT_ACCESS_SQL, {"pid": patient_id, "uid": user_id}
            ).scalar()
        )

    # --- patient arm ---

    def list_medication_options(self, patient_id: str) -> list[tuple[str, str, str]]:
        rows = self._session.execute(
            select(
                PatientMedicationRow.id, PatientMedicationRow.drug_name, PatientMedicationRow.dose
            )
            .where(PatientMedicationRow.patient_id == patient_id, *_ACTIVE_MEDICATION)
            .order_by(PatientMedicationRow.drug_name.asc())
        ).all()
        return [(row[0], row[1], row[2]) for row in rows]

    def get_medication_option(
        self, medication_id: str, patient_id: str
    ) -> tuple[str, str, str] | None:
        row = self._session.execute(
            select(
                PatientMedicationRow.id, PatientMedicationRow.drug_name, PatientMedicationRow.dose
            ).where(
                PatientMedicationRow.id == medication_id,
                PatientMedicationRow.patient_id == patient_id,
                *_ACTIVE_MEDICATION,
            )
        ).first()
        return None if row is None else (row[0], row[1], row[2])

    def add(self, request: RefillRequest) -> RefillRequest:
        row = RefillRequestRow(
            id=request.id,
            patient_id=request.patient_id,
            medication_id=request.medication_id,
            medication_text=request.medication_text,
            pharmacy_text=request.pharmacy_text,
            patient_note=request.patient_note,
            status=request.status,
            created_at=request.created_at,
            updated_at=request.updated_at,
        )
        self._session.add(row)
        self._session.flush()
        return _request(row)

    def list_for_patient(self, patient_id: str) -> list[RefillRequest]:
        rows = (
            self._session.execute(
                select(RefillRequestRow)
                .where(RefillRequestRow.patient_id == patient_id)
                .order_by(RefillRequestRow.created_at.desc())
            )
            .scalars()
            .all()
        )
        return [_request(r) for r in rows]

    # --- clinician arm ---

    def list_queue(self, user_id: str, view: RefillQueueView) -> list[RefillQueueEntry]:
        query = _entry_query(user_id)
        if view == "pending":
            query = query.where(RefillRequestRow.status == REFILL_STATUS_REQUESTED).order_by(
                RefillRequestRow.created_at.asc()
            )
        else:
            query = (
                query.where(RefillRequestRow.decided_at.is_not(None))
                .order_by(RefillRequestRow.decided_at.desc())
                .limit(RECENT_DECISIONS_LIMIT)
            )
        return [
            RefillQueueEntry(
                request=_request(row),
                patient_first_name=first,
                patient_last_name=last,
                patient_preferred_name=preferred,
            )
            for row, first, last, preferred in self._session.execute(query).all()
        ]

    def get_entry(self, request_id: str, user_id: str) -> RefillQueueEntry | None:
        found = self._session.execute(
            _entry_query(user_id).where(RefillRequestRow.id == request_id)
        ).first()
        if found is None:
            return None
        row, first, last, preferred = found
        return RefillQueueEntry(
            request=_request(row),
            patient_first_name=first,
            patient_last_name=last,
            patient_preferred_name=preferred,
        )

    def decide(
        self,
        request_id: str,
        user_id: str,
        *,
        status: str,
        prescriber_note: str | None,
        decided_at: datetime,
    ) -> RefillRequest:
        current = self._session.get(RefillRequestRow, request_id)
        if current is None or not self._has_access(current.patient_id, user_id):
            raise RefillRequestAccessDeniedError(request_id, user_id)
        # Conditional on still being ``requested``, so two prescribers
        # answering at once cannot both succeed: the second update matches
        # no row, and that is the already-decided answer.
        decided = self._session.execute(
            update(RefillRequestRow)
            .where(
                RefillRequestRow.id == request_id,
                RefillRequestRow.status == REFILL_STATUS_REQUESTED,
            )
            .values(
                status=status,
                prescriber_note=prescriber_note,
                decided_by_user_id=user_id,
                decided_at=decided_at,
                updated_at=decided_at,
            )
            .returning(RefillRequestRow.id)
        ).scalar()
        if decided is None:
            raise RefillRequestAlreadyDecidedError(request_id)
        self._session.refresh(current)
        return _request(current)
