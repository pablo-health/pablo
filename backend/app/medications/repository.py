# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Medication repository — patient-access-scoped reads & writes.

Every method takes a ``user_id`` representing the clinician making the
request. Reads return ``None`` (or an empty list) when ``user_id`` has
no grant in ``patient_clinicians`` for the relevant patient; writes
raise :class:`PatientMedicationAccessDeniedError`. The Postgres
implementation delegates the check to the ``has_patient_access`` SQL
function — the same function used by the notes and outcome_measures
repositories.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..utcnow import utc_now


class PatientMedicationAccessDeniedError(Exception):
    """Raised when a write touches a patient the user has no grant for."""

    def __init__(self, patient_id: str, user_id: str) -> None:
        super().__init__(f"user {user_id!r} has no access grant for patient {patient_id!r}")
        self.patient_id = patient_id
        self.user_id = user_id


class MedicationRepository(ABC):
    """Abstract base class for medication data access.

    Rows are plain ``dict[str, object]`` matching the ``PatientMedicationRow``
    column layout (uuid columns as ``str``).

    * Reads return ``None`` / empty when access is denied — same shape as
      "row doesn't exist" so callers can't distinguish and leak an existence
      oracle.
    * Writes raise :class:`PatientMedicationAccessDeniedError` because
      silently no-op'ing a write would mask broken code.
    """

    @abstractmethod
    def get(self, medication_id: str, user_id: str) -> dict[str, object] | None:
        """Get a medication by ID, or ``None`` if absent or inaccessible."""

    @abstractmethod
    def list_by_patient(
        self,
        patient_id: str,
        user_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        """List medications for a patient (excluding soft-deleted).

        Returns ``[]`` when access is denied.  When ``status`` is provided,
        only rows with that status value are returned.

        Ordering: active first then discontinued/on_hold, within each group
        ordered by ``started_at`` descending (nulls last).
        """

    @abstractmethod
    def create(self, row: dict[str, object], user_id: str) -> dict[str, object]:
        """Insert a new row. Raises :class:`PatientMedicationAccessDeniedError` if blocked."""

    @abstractmethod
    def update(self, row: dict[str, object], user_id: str) -> dict[str, object]:
        """Update an existing row (full replacement).

        Raises :class:`PatientMedicationAccessDeniedError` if blocked.
        """

    @abstractmethod
    def soft_delete(self, medication_id: str, user_id: str) -> None:
        """Soft-delete a row by setting ``deleted_at``.

        Raises :class:`PatientMedicationAccessDeniedError` if the caller
        has no grant.  No-ops silently if the row is already deleted.
        """


_TEST_DEFAULT_USER = "__inmemory_test_default__"


class InMemoryMedicationRepository(MedicationRepository):
    """In-memory repository for unit tests.

    Access is governed by a ``(patient_id, user_id)`` set populated via
    :meth:`grant_access`.  Call :meth:`grant_all_access` to open the gate
    for tests that don't exercise access control.
    """

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, object]] = {}
        self._access: set[tuple[str, str]] = set()
        self._allow_all = False

    # --- test setup helpers ---

    def grant_access(self, patient_id: str, user_id: str) -> None:
        self._access.add((patient_id, user_id))

    def grant_all_access(self) -> None:
        self._allow_all = True

    def _can_access(self, patient_id: str, user_id: str) -> bool:
        return self._allow_all or (patient_id, user_id) in self._access

    # --- read methods ---

    def get(
        self,
        medication_id: str,
        user_id: str = _TEST_DEFAULT_USER,
    ) -> dict[str, object] | None:
        row = self._rows.get(medication_id)
        if row is None:
            return None
        if not self._can_access(str(row["patient_id"]), user_id):
            return None
        return dict(row)

    def list_by_patient(
        self,
        patient_id: str,
        user_id: str = _TEST_DEFAULT_USER,
        *,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        if not self._can_access(patient_id, user_id):
            return []
        rows = [
            dict(r)
            for r in self._rows.values()
            if str(r["patient_id"]) == patient_id
            and r.get("deleted_at") is None
            and (status is None or str(r["status"]) == status)
        ]

        # active first, then other statuses; within each group, started_at desc (nulls last)
        def _sort_key(r: dict[str, object]) -> tuple[int, object]:
            status_order = 0 if r.get("status") == "active" else 1
            started = r.get("started_at")
            # Negate for descending; None sorts after real dates
            if started is None:
                return (status_order, None)
            return (status_order, started)

        rows.sort(key=lambda r: (_sort_key(r)[0], _sort_key(r)[1] is None, _sort_key(r)[1]))
        return rows

    # --- write methods ---

    def create(
        self,
        row: dict[str, object],
        user_id: str = _TEST_DEFAULT_USER,
    ) -> dict[str, object]:
        patient_id = str(row["patient_id"])
        if not self._can_access(patient_id, user_id):
            raise PatientMedicationAccessDeniedError(patient_id, user_id)
        self._rows[str(row["id"])] = dict(row)
        return dict(row)

    def update(
        self,
        row: dict[str, object],
        user_id: str = _TEST_DEFAULT_USER,
    ) -> dict[str, object]:
        patient_id = str(row["patient_id"])
        if not self._can_access(patient_id, user_id):
            raise PatientMedicationAccessDeniedError(patient_id, user_id)
        self._rows[str(row["id"])] = dict(row)
        return dict(row)

    def soft_delete(
        self,
        medication_id: str,
        user_id: str = _TEST_DEFAULT_USER,
    ) -> None:
        row = self._rows.get(medication_id)
        if row is None:
            return
        patient_id = str(row["patient_id"])
        if not self._can_access(patient_id, user_id):
            raise PatientMedicationAccessDeniedError(patient_id, user_id)
        now = utc_now()
        row["deleted_at"] = now
        row["updated_at"] = now
