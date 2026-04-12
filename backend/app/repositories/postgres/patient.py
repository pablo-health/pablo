# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL patient repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db.models import PatientRow, TherapySessionRow
from ...models import Patient
from ...utcnow import utc_now_iso
from ..patient import PatientRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresPatientRepository(PatientRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        row = self._session.get(PatientRow, patient_id)
        if row is None or row.user_id != user_id:
            return None
        return _row_to_patient(row)

    def get_multiple(self, patient_ids: list[str], user_id: str) -> dict[str, Patient]:
        if not patient_ids:
            return {}
        rows = (
            self._session.query(PatientRow)
            .filter(PatientRow.id.in_(patient_ids), PatientRow.user_id == user_id)
            .all()
        )
        return {r.id: _row_to_patient(r) for r in rows}

    def list_by_user(
        self,
        user_id: str,
        search: str | None = None,
        search_by: str = "last_name",
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Patient], int]:
        query = self._session.query(PatientRow).filter(PatientRow.user_id == user_id)

        if search:
            search_lower = search.lower()
            if search_by == "first_name":
                query = query.filter(PatientRow.first_name_lower.startswith(search_lower))
            else:
                query = query.filter(PatientRow.last_name_lower.startswith(search_lower))

        query = query.order_by(PatientRow.last_name_lower, PatientRow.first_name_lower)

        total = query.count()
        offset = (page - 1) * page_size
        rows = query.offset(offset).limit(page_size).all()
        return [_row_to_patient(r) for r in rows], total

    def create(self, patient: Patient) -> Patient:
        row = PatientRow()
        _patient_to_row(patient, row)
        self._session.add(row)
        self._session.flush()
        return patient

    def update(self, patient: Patient) -> Patient:
        patient.updated_at = utc_now_iso()
        patient.first_name_lower = patient.first_name.lower()
        patient.last_name_lower = patient.last_name.lower()
        row = self._session.get(PatientRow, patient.id)
        if row is None:
            row = PatientRow()
            self._session.add(row)
        _patient_to_row(patient, row)
        self._session.flush()
        return patient

    def delete(self, patient_id: str, user_id: str) -> bool:
        row = self._session.get(PatientRow, patient_id)
        if row is None or row.user_id != user_id:
            return False
        # Cascade: delete associated therapy sessions
        self._session.query(TherapySessionRow).filter(
            TherapySessionRow.patient_id == patient_id
        ).delete()
        self._session.delete(row)
        self._session.flush()
        return True


def _row_to_patient(row: PatientRow) -> Patient:
    return Patient(
        id=row.id,
        user_id=row.user_id,
        first_name=row.first_name,
        last_name=row.last_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
        first_name_lower=row.first_name_lower,
        last_name_lower=row.last_name_lower,
        session_count=row.session_count,
        email=row.email,
        phone=row.phone,
        status=row.status,
        date_of_birth=row.date_of_birth,
        diagnosis=row.diagnosis,
        last_session_date=row.last_session_date,
        next_session_date=row.next_session_date,
    )


def _patient_to_row(patient: Patient, row: PatientRow) -> None:
    row.id = patient.id
    row.user_id = patient.user_id
    row.first_name = patient.first_name
    row.last_name = patient.last_name
    row.first_name_lower = patient.first_name_lower
    row.last_name_lower = patient.last_name_lower
    row.email = patient.email
    row.phone = patient.phone
    row.status = patient.status
    row.date_of_birth = patient.date_of_birth
    row.diagnosis = patient.diagnosis
    row.session_count = patient.session_count
    row.last_session_date = patient.last_session_date
    row.next_session_date = patient.next_session_date
    row.created_at = patient.created_at
    row.updated_at = patient.updated_at
