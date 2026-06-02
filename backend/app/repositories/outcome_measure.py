# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Outcome measure repository — patient-access-scoped reads & writes.

Every method takes a ``user_id`` representing the clinician making the
request. Reads return ``None`` (or an empty list) when ``user_id`` has
no grant in ``patient_clinicians`` for the relevant patient; writes
raise :class:`PatientOutcomeAccessDeniedError`. The Postgres
implementation delegates the check to the ``has_patient_access`` SQL
function — the same function used by the notes repository — so
application-layer and database-layer authorisation stay in lockstep.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class PatientOutcomeAccessDeniedError(Exception):
    """Raised when a write touches a patient the user has no grant for."""

    def __init__(self, patient_id: str, user_id: str) -> None:
        super().__init__(f"user {user_id!r} has no access grant for patient {patient_id!r}")
        self.patient_id = patient_id
        self.user_id = user_id


class OutcomeMeasureRepository(ABC):
    """Abstract base class for outcome measure data access.

    Rows are plain ``dict[str, object]`` matching the ``OutcomeMeasureRow``
    column layout (uuid columns as ``str``, JSONB as ``dict | None``).

    * Reads return ``None`` / empty when access is denied — same shape as
      "row doesn't exist" so callers can't distinguish and leak an existence
      oracle.
    * Writes raise :class:`PatientOutcomeAccessDeniedError` because silently
      no-op'ing a write would mask broken code.
    """

    @abstractmethod
    def get(self, measure_id: str, user_id: str) -> dict[str, object] | None:
        """Get a measure by ID, or ``None`` if absent or inaccessible."""

    @abstractmethod
    def list_by_patient(
        self,
        patient_id: str,
        user_id: str,
        *,
        instrument: str | None = None,
    ) -> list[dict[str, object]]:
        """List measures for a patient (including soft-deleted).

        The service layer filters ``deleted_at IS NULL`` so both live and
        deleted rows are returned here — callers decide what to show.
        Returns ``[]`` when access is denied.
        """

    @abstractmethod
    def add(self, row: dict[str, object], user_id: str) -> dict[str, object]:
        """Insert a new row. Raises :class:`PatientOutcomeAccessDeniedError` if blocked."""

    @abstractmethod
    def update(self, row: dict[str, object], user_id: str) -> dict[str, object]:
        """Update an existing row (full replacement).

        Raises :class:`PatientOutcomeAccessDeniedError` if blocked.
        """


_TEST_DEFAULT_USER = "__inmemory_test_default__"


class InMemoryOutcomeMeasureRepository(OutcomeMeasureRepository):
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

    def get(self, measure_id: str, user_id: str = _TEST_DEFAULT_USER) -> dict[str, object] | None:
        row = self._rows.get(measure_id)
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
        instrument: str | None = None,
    ) -> list[dict[str, object]]:
        if not self._can_access(patient_id, user_id):
            return []
        rows = [
            dict(r)
            for r in self._rows.values()
            if str(r["patient_id"]) == patient_id
            and (instrument is None or str(r["instrument"]) == instrument)
        ]
        return rows

    # --- write methods ---

    def add(
        self,
        row: dict[str, object],
        user_id: str = _TEST_DEFAULT_USER,
    ) -> dict[str, object]:
        patient_id = str(row["patient_id"])
        if not self._can_access(patient_id, user_id):
            raise PatientOutcomeAccessDeniedError(patient_id, user_id)
        self._rows[str(row["id"])] = dict(row)
        return dict(row)

    def update(
        self,
        row: dict[str, object],
        user_id: str = _TEST_DEFAULT_USER,
    ) -> dict[str, object]:
        patient_id = str(row["patient_id"])
        if not self._can_access(patient_id, user_id):
            raise PatientOutcomeAccessDeniedError(patient_id, user_id)
        self._rows[str(row["id"])] = dict(row)
        return dict(row)
