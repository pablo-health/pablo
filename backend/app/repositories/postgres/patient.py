# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL patient repository implementation.

Every access-bounded method delegates to the schema-local
``has_patient_access(patient_id, user_id)`` SQL function (or its join-
through-``patient_clinicians`` equivalent for queries that already
need a join). Patient ownership lives in ``patient_clinicians``, not
on a column of the patient row — see migration ``9dea1edf7fe0``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, delete, func, or_, select, text, tuple_, update

from ...db.models import (
    NoteRow,
    PatientClinicianRow,
    PatientRow,
    TherapySessionRow,
)
from ...models import Patient
from ...models.enums import ClinicianRole
from ...utcnow import utc_now
from ..patient import PatientRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)


def _live_grant_filter(user_id: str) -> tuple:
    """SQL predicates for "user has a non-expired patient_clinicians grant".

    Returns a tuple usable in ``.filter(*predicates)`` calls. Used by
    every method that joins through ``PatientClinicianRow``; keeping it
    in one place ensures expiration semantics never drift between
    methods.
    """
    return (
        PatientClinicianRow.user_id == user_id,
        or_(
            PatientClinicianRow.expires_at.is_(None),
            PatientClinicianRow.expires_at > utc_now(),
        ),
    )


class PostgresPatientRepository(PatientRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _has_access(self, patient_id: str, user_id: str) -> bool:
        result = self._session.execute(
            _HAS_PATIENT_ACCESS_SQL,
            {"pid": patient_id, "uid": user_id},
        ).scalar()
        return bool(result)

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        """Fetch the patient if it exists, is live, and ``user_id`` has a grant.

        Single-query join through ``patient_clinicians`` so denial is
        indistinguishable from a missing row at the SQL layer (no
        existence oracle).
        """
        row = (
            self._session.execute(
                select(PatientRow)
                .join(PatientClinicianRow, PatientClinicianRow.patient_id == PatientRow.id)
                .where(
                    PatientRow.id == patient_id,
                    PatientRow.deleted_at.is_(None),
                    *_live_grant_filter(user_id),
                )
            )
            .scalars()
            .one_or_none()
        )
        return _row_to_patient(row) if row else None

    def get_last_name(self, patient_id: str, user_id: str) -> str | None:
        """Single-column variant of :meth:`get` — never selects the rest
        of the chart row, so it stays valid under column-scoped grants."""
        last_name: str | None = self._session.scalar(
            select(PatientRow.last_name)
            .join(PatientClinicianRow, PatientClinicianRow.patient_id == PatientRow.id)
            .where(
                PatientRow.id == patient_id,
                PatientRow.deleted_at.is_(None),
                *_live_grant_filter(user_id),
            )
        )
        return last_name

    def has_live_grant(self, patient_id: str, user_id: str) -> bool:
        """Whether ``user_id`` holds a non-expired ``patient_clinicians``
        grant on ``patient_id`` — checked directly against the grant table,
        independent of the patient row's ``deleted_at`` and without
        selecting any chart column. Needs only ``patient_clinicians`` SELECT
        (which the cross-tenant review role holds), so the audit reviewer
        can compute its "access was not grant-backed" signal under the
        column-scoped grant. Approximate current-state: a grant revoked
        since the access cannot be recovered (the table keeps no history)."""
        return bool(
            self._session.execute(
                select(PatientClinicianRow.patient_id).where(
                    PatientClinicianRow.patient_id == patient_id,
                    *_live_grant_filter(user_id),
                )
            ).first()
        )

    def live_grant_pairs(self, pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
        if not pairs:
            return set()
        # One query for all (user_id, patient_id) pairs. The expiry predicate
        # can't reuse _live_grant_filter (that pins a single user_id), so the
        # not-expired clause is inlined; the tuple IN matches each pair exactly.
        rows = self._session.execute(
            select(PatientClinicianRow.user_id, PatientClinicianRow.patient_id).where(
                tuple_(PatientClinicianRow.user_id, PatientClinicianRow.patient_id).in_(
                    [(uid, pid) for (uid, pid) in pairs]
                ),
                or_(
                    PatientClinicianRow.expires_at.is_(None),
                    PatientClinicianRow.expires_at > utc_now(),
                ),
            )
        ).all()
        return {(row.user_id, row.patient_id) for row in rows}

    def get_multiple(self, patient_ids: list[str], user_id: str) -> dict[str, Patient]:
        if not patient_ids:
            return {}
        rows = (
            self._session.execute(
                select(PatientRow)
                .join(PatientClinicianRow, PatientClinicianRow.patient_id == PatientRow.id)
                .where(
                    PatientRow.id.in_(patient_ids),
                    PatientRow.deleted_at.is_(None),
                    *_live_grant_filter(user_id),
                )
            )
            .scalars()
            .all()
        )
        return {r.id: _row_to_patient(r) for r in rows}

    def list_by_user(
        self,
        user_id: str,
        search: str | None = None,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Patient], int]:
        """List patients the user has a grant for, with pagination.

        ``search`` is a case-insensitive substring matched against both
        first and last name (the lower-cased indexed columns), so a name
        fragment or a first name finds the patient without a field toggle.

        The join through ``patient_clinicians`` returns rows where
        ``user_id`` has any non-expired grant — primary, co-treating,
        supervisor, or covering. v1 ships with primary-only grants so
        the result set matches the prior ``user_id``-column semantics.
        """
        stmt = (
            select(PatientRow)
            .join(PatientClinicianRow, PatientClinicianRow.patient_id == PatientRow.id)
            .where(
                PatientRow.deleted_at.is_(None),
                *_live_grant_filter(user_id),
            )
        )

        if search:
            search_lower = search.lower()
            stmt = stmt.where(
                or_(
                    PatientRow.first_name_lower.contains(search_lower),
                    PatientRow.last_name_lower.contains(search_lower),
                )
            )

        stmt = stmt.order_by(PatientRow.last_name_lower, PatientRow.first_name_lower)

        total = self._session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        offset = (page - 1) * page_size
        rows = self._session.execute(stmt.offset(offset).limit(page_size)).scalars().all()
        return [_row_to_patient(r) for r in rows], total

    def create(self, patient: Patient, user_id: str) -> Patient:
        """Create the patient and the primary-clinician grant atomically.

        ``user_id`` becomes the ``role='primary'`` clinician — the
        creator owns the chart. Co-treating / supervisor / coverage
        grants are inserted later via the (forthcoming) admin endpoints.

        The two rows are added and flushed *separately* — not because
        we want two transactions (we don't; both flushes run inside
        the request's single open transaction and commit/rollback
        together via ``DatabaseSessionMiddleware``), but because
        SQLAlchemy's unit-of-work has been observed to skip the
        ``patients`` INSERT when both objects are added before a
        single ``flush()``, emitting only the dependent
        ``patient_clinicians`` INSERT and failing on the FK. Isolating
        the parent flush gives us the canonical ordering: parent INSERT,
        then child INSERT, both inside the same transaction, atomic
        on commit. Documented in [[uow-patient-grant-flush-bug]].
        """
        row = PatientRow()
        _patient_to_row(patient, row)
        self._session.add(row)
        self._session.flush([row])

        grant = PatientClinicianRow(
            patient_id=patient.id,
            user_id=user_id,
            role=ClinicianRole.PRIMARY.value,
            granted_by=user_id,
        )
        self._session.add(grant)
        self._session.flush([grant])
        return patient
        return patient

    def update(self, patient: Patient) -> Patient:
        """Update a patient row. Access is gated by RLS at the DB layer.

        Does not take ``user_id`` because the caller (a service / route
        handler) has already loaded the patient via ``get()``, which
        enforces the access check. Any path that bypasses ``get()``
        still hits RLS at commit time — fail-closed.
        """
        patient.updated_at = utc_now()
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
        """Soft-delete the patient and cascade to therapy_sessions + notes.

        Cascade order matches the prior hard-delete:
            therapy_sessions → notes → patients

        Returns False if the row is gone, soft-deleted, or the user has
        no grant — in all three cases the caller's invariant ("nothing
        live visible with this id") is satisfied without further work.
        """
        row = self._session.get(PatientRow, patient_id)
        if row is None or row.deleted_at is not None:
            return False
        if not self._has_access(patient_id, user_id):
            return False

        now = utc_now()
        self._session.execute(
            update(TherapySessionRow)
            .where(
                TherapySessionRow.patient_id == patient_id,
                TherapySessionRow.deleted_at.is_(None),
            )
            .values({TherapySessionRow.deleted_at: now})
            .execution_options(synchronize_session=False)
        )
        self._session.execute(
            update(NoteRow)
            .where(
                NoteRow.patient_id == patient_id,
                NoteRow.deleted_at.is_(None),
            )
            .values({NoteRow.deleted_at: now})
            .execution_options(synchronize_session=False)
        )
        row.deleted_at = now
        self._session.flush()
        return True

    def list_recently_deleted(
        self,
        user_id: str,
        *,
        window_days: int = 30,
    ) -> list[tuple[Patient, datetime]]:
        """Soft-deleted patients still inside the undo window."""
        cutoff = utc_now() - timedelta(days=window_days)
        rows = (
            self._session.execute(
                select(PatientRow)
                .join(PatientClinicianRow, PatientClinicianRow.patient_id == PatientRow.id)
                .where(
                    PatientRow.deleted_at.isnot(None),
                    PatientRow.deleted_at > cutoff,
                    *_live_grant_filter(user_id),
                )
                .order_by(PatientRow.last_name_lower, PatientRow.first_name_lower)
            )
            .scalars()
            .all()
        )
        return [(_row_to_patient(r), r.deleted_at) for r in rows if r.deleted_at is not None]

    def restore(self, patient_id: str, user_id: str, *, window_days: int = 30) -> Patient | None:
        """Reverse a soft-delete by clearing ``deleted_at``."""
        row = self._session.get(PatientRow, patient_id)
        if row is None or row.deleted_at is None:
            return None
        if not self._has_access(patient_id, user_id):
            return None
        cutoff = utc_now() - timedelta(days=window_days)
        if row.deleted_at <= cutoff:
            return None

        patient_stamp = row.deleted_at
        # Cascade: only undo rows whose deleted_at matches the patient's
        # tombstone — those are the ones the patient delete cascaded
        # onto. Earlier independent per-row soft-deletes stay tombstoned.
        self._session.execute(
            update(TherapySessionRow)
            .where(
                TherapySessionRow.patient_id == patient_id,
                TherapySessionRow.deleted_at == patient_stamp,
            )
            .values({TherapySessionRow.deleted_at: None})
            .execution_options(synchronize_session=False)
        )
        self._session.execute(
            update(NoteRow)
            .where(
                NoteRow.patient_id == patient_id,
                NoteRow.deleted_at == patient_stamp,
            )
            .values({NoteRow.deleted_at: None})
            .execution_options(synchronize_session=False)
        )
        row.deleted_at = None
        self._session.flush()
        return _row_to_patient(row)

    def close_chart(
        self, patient_id: str, user_id: str, closure_reason: str | None
    ) -> Patient | None:
        """Stamp ``chart_closed_at`` and store the closure reason (THERAPY-hek)."""
        row = self._session.get(PatientRow, patient_id)
        if row is None or row.deleted_at is not None:
            return None
        if not self._has_access(patient_id, user_id):
            return None
        now = utc_now()
        row.chart_closed_at = now
        row.chart_closure_reason = closure_reason
        row.updated_at = now
        self._session.flush()
        return _row_to_patient(row)

    def reopen_chart(self, patient_id: str, user_id: str) -> Patient | None:
        """Clear chart closure fields (THERAPY-hek)."""
        row = self._session.get(PatientRow, patient_id)
        if row is None or row.deleted_at is not None:
            return None
        if not self._has_access(patient_id, user_id):
            return None
        row.chart_closed_at = None
        row.chart_closure_reason = None
        row.updated_at = utc_now()
        self._session.flush()
        return _row_to_patient(row)

    # ─── Internal — purge cron only (THERAPY-cgy) ──────────────────────
    # Not HTTP-exposed. Bypasses has_patient_access because the cron
    # runs as a system identity that legitimately has no grant on any
    # patient. Callers outside ``backend/app/jobs/`` must not use this.

    def _physical_delete(self, patient_id: str) -> bool:
        row = self._session.get(PatientRow, patient_id)
        if row is None:
            return False
        # Mirror cascade order from soft-delete.
        self._session.execute(
            delete(NoteRow)
            .where(NoteRow.patient_id == patient_id)
            .execution_options(synchronize_session=False)
        )
        self._session.execute(
            delete(TherapySessionRow)
            .where(TherapySessionRow.patient_id == patient_id)
            .execution_options(synchronize_session=False)
        )
        # patient_clinicians has ON DELETE CASCADE so grants are removed
        # automatically when the patient row goes.
        self._session.delete(row)
        self._session.flush()
        return True


def _row_to_patient(row: PatientRow) -> Patient:
    return Patient(
        id=row.id,
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
        # DB column is native DATE; the API model carries an ISO string.
        date_of_birth=row.date_of_birth.isoformat() if row.date_of_birth else None,
        diagnosis=row.diagnosis,
        last_session_date=row.last_session_date,
        next_session_date=row.next_session_date,
        chart_closed_at=row.chart_closed_at,
        chart_closure_reason=row.chart_closure_reason,
    )


def _patient_to_row(patient: Patient, row: PatientRow) -> None:
    row.id = patient.id
    row.first_name = patient.first_name
    row.last_name = patient.last_name
    row.first_name_lower = patient.first_name_lower
    row.last_name_lower = patient.last_name_lower
    row.email = patient.email
    row.phone = patient.phone
    row.status = patient.status
    # ISO string (or "" / None) from the API -> native DATE (or NULL).
    row.date_of_birth = date.fromisoformat(patient.date_of_birth) if patient.date_of_birth else None
    row.diagnosis = patient.diagnosis
    row.session_count = patient.session_count
    row.last_session_date = patient.last_session_date
    row.next_session_date = patient.next_session_date
    row.chart_closed_at = patient.chart_closed_at
    row.chart_closure_reason = patient.chart_closure_reason
    row.created_at = patient.created_at
    row.updated_at = patient.updated_at
