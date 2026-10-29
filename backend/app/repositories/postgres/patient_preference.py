# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL patient-preference repository.

Runs inside the request's tenant-scoped session, so the row policy
(``has_patient_access``) applies to every statement here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import PatientPreferenceRow
from ...preferences.models import Preference
from ..patient_preference import PatientPreferenceRepository, sort_preferences

if TYPE_CHECKING:
    from collections.abc import Collection

    from sqlalchemy.orm import Session

_FIELDS = (
    "id",
    "patient_id",
    "domain",
    "kind",
    "value",
    "text",
    "strength",
    "source",
    "source_ref",
    "stated_at",
    "confirmed_at",
    "retired_at",
    "retired_by",
    "created_by",
    "created_at",
    "updated_at",
)


def _to_preference(row: PatientPreferenceRow) -> Preference:
    return Preference(**{name: getattr(row, name) for name in _FIELDS})


class PostgresPatientPreferenceRepository(PatientPreferenceRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_patient(self, patient_id: str, *, include_retired: bool) -> list[Preference]:
        query = select(PatientPreferenceRow).where(PatientPreferenceRow.patient_id == patient_id)
        if not include_retired:
            query = query.where(PatientPreferenceRow.retired_at.is_(None))
        return sort_preferences([_to_preference(r) for r in self._session.scalars(query)])

    def list_active(self, patient_id: str, kinds: Collection[str]) -> list[Preference]:
        return self.list_active_for_patients([patient_id], kinds)

    def list_active_for_patients(
        self, patient_ids: Collection[str], kinds: Collection[str]
    ) -> list[Preference]:
        rows = self._session.scalars(
            select(PatientPreferenceRow).where(
                PatientPreferenceRow.patient_id.in_(list(patient_ids)),
                PatientPreferenceRow.kind.in_(list(kinds)),
                PatientPreferenceRow.retired_at.is_(None),
            )
        )
        return sort_preferences([_to_preference(r) for r in rows])

    def get(self, patient_id: str, preference_id: str) -> Preference | None:
        row = self._session.scalars(
            select(PatientPreferenceRow).where(
                PatientPreferenceRow.id == preference_id,
                PatientPreferenceRow.patient_id == patient_id,
            )
        ).one_or_none()
        return _to_preference(row) if row else None

    def save(self, preference: Preference) -> Preference:
        row = self._session.get(PatientPreferenceRow, preference.id)
        if row is None:
            row = PatientPreferenceRow()
            self._session.add(row)
        for name in _FIELDS:
            setattr(row, name, getattr(preference, name))
        self._session.flush()
        return preference
