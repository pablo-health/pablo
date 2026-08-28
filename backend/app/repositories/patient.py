# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient repository implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..utcnow import utc_now
from .session import InMemoryTherapySessionRepository, TherapySessionRepository

if TYPE_CHECKING:
    from ..models import Patient


class PatientRepository(ABC):
    """Abstract base class for patient data access."""

    @abstractmethod
    def get(self, patient_id: str, user_id: str) -> Patient | None:
        """Get patient by ID, ensuring it belongs to the user."""
        pass

    @abstractmethod
    def get_multiple(self, patient_ids: list[str], user_id: str) -> dict[str, Patient]:
        """Get multiple patients by IDs, ensuring they belong to the user."""
        pass

    def get_last_name(self, patient_id: str, user_id: str) -> str | None:
        """Last name only, same access gate as :meth:`get`.

        For callers that need a single demographic field (the audit
        reviewer's name-overlap check) — backends can answer this
        without materialising the full chart row. Default delegates
        to ``get``.
        """
        patient = self.get(patient_id, user_id)
        return patient.last_name if patient else None

    def has_live_grant(self, patient_id: str, user_id: str) -> bool:
        """Whether ``user_id`` currently holds a non-expired clinician grant
        on ``patient_id`` — independent of whether the patient row is live.

        Unlike :meth:`get`, this answers the *authorization* question
        ("is this user allowed to open this chart right now?") without
        conflating it with patient existence: a soft-deleted patient with
        a live grant still returns True. Used by the audit reviewer to
        flag accesses that are not grant-backed (approximate, current-
        state — ``patient_clinicians`` keeps no history, so a grant that
        existed at access time but has since been revoked cannot be
        recovered here). Default delegates to ``get``'s gate, which is
        exact for backends without soft-delete.
        """
        return self.get(patient_id, user_id) is not None

    def live_grant_pairs(self, pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
        """Batch form of :meth:`has_live_grant` — return the ``(user_id,
        patient_id)`` pairs (from ``pairs``) that currently have a live grant.

        Default checks each pair individually; the Postgres backend overrides
        this with a single query so the audit reviewer's grant check doesn't
        issue one query per distinct access pair.
        """
        return {(uid, pid) for (uid, pid) in pairs if self.has_live_grant(pid, uid)}

    @abstractmethod
    def find_by_email(self, email: str, user_id: str) -> Patient | None:
        """First live patient of ``user_id`` with this email (case-insensitive).

        Used by public booking to reuse an existing chart instead of
        creating a duplicate when a known client books again. ``None``
        when no match.
        """

    @abstractmethod
    def list_by_user(
        self,
        user_id: str,
        search: str | None = None,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Patient], int]:
        """List patients for a user with pagination.

        ``search`` is a case-insensitive substring matched against both
        first and last name. Returns (paginated_patients, total_count).
        """
        pass

    @abstractmethod
    def create(self, patient: Patient, user_id: str) -> Patient:
        """Create a new patient.

        ``user_id`` becomes the patient's ``role='primary'`` clinician
        in ``patient_clinicians`` — the creator owns the chart. Splitting
        the access grant onto a separate method would let a caller
        forget it, so the create path inserts both rows atomically.
        """
        pass

    @abstractmethod
    def update(self, patient: Patient) -> Patient:
        """Update an existing patient.

        Access is gated by RLS at the DB layer; callers always reach
        this method after a ``get()`` that already passed the access
        check.
        """
        pass

    @abstractmethod
    def delete(self, patient_id: str, user_id: str) -> bool:
        """Delete a patient and cascade to sessions. Returns True if deleted."""
        pass

    @abstractmethod
    def list_recently_deleted(
        self,
        user_id: str,
        *,
        window_days: int = 30,
    ) -> list[tuple[Patient, datetime]]:
        """List soft-deleted patients still inside the undo window.

        Returns ``(patient, deleted_at)`` pairs for patients whose
        ``deleted_at`` is non-NULL and within the last ``window_days``.
        Used by the ``include_deleted=recent`` listing path that powers
        the "Recently deleted" UI tab (THERAPY-yg2). After
        ``window_days`` rows remain on disk until the day-30 hard-purge
        cron (THERAPY-cgy) physically removes them, but they no longer
        appear in this listing. ``deleted_at`` is returned out-of-band
        because it does not live on the ``Patient`` dataclass — only
        the soft-delete-aware paths need it.
        """
        pass

    @abstractmethod
    def restore(self, patient_id: str, user_id: str, *, window_days: int = 30) -> Patient | None:
        """Undo a soft-delete by clearing ``deleted_at``.

        Returns the restored ``Patient`` on success, ``None`` if the
        patient is not soft-deleted, not owned by ``user_id``, or its
        ``deleted_at`` is already past the ``window_days`` cutoff (in
        which case the row is awaiting hard-purge and must not be
        revived).

        Cascade order mirrors ``delete()``: clears ``deleted_at`` on
        the patient and on its therapy sessions / notes that were
        cascaded by the original soft-delete. Session numbers are
        preserved — ``get_session_number_for_patient`` deliberately
        ignores ``deleted_at`` so numbering stays stable across the
        delete/restore cycle (THERAPY-nyb).
        """
        pass

    @abstractmethod
    def close_chart(
        self, patient_id: str, user_id: str, closure_reason: str | None
    ) -> Patient | None:
        """Close a patient's chart (THERAPY-hek).

        Sets ``chart_closed_at = NOW()`` and ``chart_closure_reason`` on
        the patient row. Orthogonal to soft-delete: chart closure does
        NOT advance the day-30 hard-purge clock and does NOT hide the
        row from list/get reads. Returns the updated patient, or
        ``None`` if not found / not owned / soft-deleted.
        """
        pass

    @abstractmethod
    def reopen_chart(self, patient_id: str, user_id: str) -> Patient | None:
        """Reopen a previously-closed chart (THERAPY-hek).

        Clears ``chart_closed_at`` and ``chart_closure_reason``. Returns
        the updated patient, or ``None`` if not found / not owned /
        soft-deleted.
        """
        pass


class InMemoryPatientRepository(PatientRepository):
    """In-memory implementation of PatientRepository.

    Maintains a per-(patient_id, user_id) access set so the contract
    matches PostgresPatientRepository — tests that exercise the access
    boundary fail here for the same reason they'd fail in production
    rather than silently passing against a looser test double.

    The ``create()`` path automatically inserts a 'primary' grant for
    the caller-supplied ``user_id``, mirroring the Postgres impl.
    """

    def __init__(self, session_repo: TherapySessionRepository | None = None) -> None:
        self._patients: dict[str, Patient] = {}
        self._access: set[tuple[str, str]] = set()  # (patient_id, user_id)
        # THERAPY-yg2: track soft-delete timestamps in a parallel map so
        # the in-memory repo (used in API tests) can model the same
        # tombstone-then-purge lifecycle as PostgresPatientRepository
        # without adding a deleted_at field to the Patient dataclass.
        self._deleted_at: dict[str, datetime] = {}
        self._session_repo = session_repo

    # --- access helpers (mirror has_patient_access semantics) ---

    def grant_access(self, patient_id: str, user_id: str) -> None:
        """Record that ``user_id`` may read/write ``patient_id``."""
        self._access.add((patient_id, user_id))

    def _can_access(self, patient_id: str, user_id: str) -> bool:
        return (patient_id, user_id) in self._access

    def get(self, patient_id: str, user_id: str) -> Patient | None:
        """Get patient by ID; ``None`` if absent, soft-deleted, or inaccessible."""
        patient = self._patients.get(patient_id)
        if patient is None or patient_id in self._deleted_at:
            return None
        if not self._can_access(patient_id, user_id):
            return None
        return patient

    def get_multiple(self, patient_ids: list[str], user_id: str) -> dict[str, Patient]:
        return {
            p.id: p
            for p in self._patients.values()
            if p.id in patient_ids
            and p.id not in self._deleted_at
            and self._can_access(p.id, user_id)
        }

    def find_by_email(self, email: str, user_id: str) -> Patient | None:
        matches = [
            p
            for p in self._patients.values()
            if (p.email or "").lower() == email.lower()
            and p.id not in self._deleted_at
            and p.status != "pending"
            and self._can_access(p.id, user_id)
        ]
        return min(matches, key=lambda p: p.created_at) if matches else None

    def list_by_user(
        self,
        user_id: str,
        search: str | None = None,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Patient], int]:
        """List patients the user has a grant for, with pagination."""
        patients = [
            p
            for p in self._patients.values()
            if p.id not in self._deleted_at
            and p.status != "pending"
            and self._can_access(p.id, user_id)
        ]

        if search:
            search_lower = search.lower()
            patients = [
                p
                for p in patients
                if search_lower in p.first_name_lower or search_lower in p.last_name_lower
            ]

        # Sort by last name, then first name (clinical standard)
        patients.sort(key=lambda p: (p.last_name_lower, p.first_name_lower))
        total = len(patients)
        offset = (page - 1) * page_size
        return patients[offset : offset + page_size], total

    def create(self, patient: Patient, user_id: str) -> Patient:
        """Create the patient and auto-grant the creator primary access."""
        self._patients[patient.id] = patient
        self._access.add((patient.id, user_id))
        return patient

    def update(self, patient: Patient) -> Patient:
        """Update an existing patient. Access is enforced at the read site."""
        patient.updated_at = utc_now()
        patient.first_name_lower = patient.first_name.lower()
        patient.last_name_lower = patient.last_name.lower()
        self._patients[patient.id] = patient
        return patient

    def delete(self, patient_id: str, user_id: str) -> bool:
        """Soft-delete a patient and cascade to sessions. Returns True if deleted."""
        patient = self.get(patient_id, user_id)
        if not patient:
            return False

        # Cascade: delete associated therapy sessions. The in-memory
        # session repo does not track soft-delete state, so this matches
        # the legacy hard-delete semantics for tests; the Postgres repo
        # is the source of truth for the soft-delete cascade contract.
        if self._session_repo is not None and isinstance(
            self._session_repo, InMemoryTherapySessionRepository
        ):
            session_ids = [
                sid for sid, s in self._session_repo._sessions.items() if s.patient_id == patient_id
            ]
            for sid in session_ids:
                del self._session_repo._sessions[sid]

        self._deleted_at[patient_id] = utc_now()
        return True

    def list_recently_deleted(
        self,
        user_id: str,
        *,
        window_days: int = 30,
    ) -> list[tuple[Patient, datetime]]:
        """List soft-deleted patients still inside the undo window."""
        cutoff = utc_now() - timedelta(days=window_days)
        rows = [
            (self._patients[pid], stamp)
            for pid, stamp in self._deleted_at.items()
            if pid in self._patients
            and self._patients[pid].status != "pending"
            and self._can_access(pid, user_id)
            and stamp > cutoff
        ]
        rows.sort(key=lambda pair: (pair[0].last_name_lower, pair[0].first_name_lower))
        return rows

    def restore(self, patient_id: str, user_id: str, *, window_days: int = 30) -> Patient | None:
        """Undo a soft-delete if still inside the undo window."""
        patient = self._patients.get(patient_id)
        if patient is None or not self._can_access(patient_id, user_id):
            return None
        stamp = self._deleted_at.get(patient_id)
        if stamp is None:
            return None
        cutoff = utc_now() - timedelta(days=window_days)
        if stamp <= cutoff:
            return None
        del self._deleted_at[patient_id]
        return patient

    def close_chart(
        self, patient_id: str, user_id: str, closure_reason: str | None
    ) -> Patient | None:
        """Close a chart by stamping ``chart_closed_at`` (THERAPY-hek)."""
        patient = self.get(patient_id, user_id)
        if patient is None:
            return None
        patient.chart_closed_at = utc_now()
        patient.chart_closure_reason = closure_reason
        patient.updated_at = patient.chart_closed_at
        self._patients[patient.id] = patient
        return patient

    def reopen_chart(self, patient_id: str, user_id: str) -> Patient | None:
        """Reopen a previously-closed chart (THERAPY-hek)."""
        patient = self.get(patient_id, user_id)
        if patient is None:
            return None
        patient.chart_closed_at = None
        patient.chart_closure_reason = None
        patient.updated_at = utc_now()
        self._patients[patient.id] = patient
        return patient
