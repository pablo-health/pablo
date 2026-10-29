# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL problem-list repository.

Runs inside the request's tenant-scoped session, so the row policy
(``has_patient_access``) applies to every statement here.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import PatientProblemRow, PatientRow
from ...problems.models import (
    Problem,
    ProblemStatus,
    derived_diagnosis,
    sort_problems,
    split_free_text_diagnosis,
)
from ...utcnow import utc_now
from ..patient_problem import PatientProblemRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _to_problem(row: PatientProblemRow) -> Problem:
    return Problem(
        id=row.id,
        patient_id=row.patient_id,
        label=row.label,
        icd10_code=row.icd10_code,
        status=row.status,
        position=row.position,
        added_at=row.added_at,
        updated_at=row.updated_at,
        onset_date=row.onset_date,
        source_note_id=row.source_note_id,
        added_by=row.added_by,
        resolved_at=row.resolved_at,
        deleted_at=row.deleted_at,
    )


def _write(problem: Problem, row: PatientProblemRow) -> None:
    row.id = problem.id
    row.patient_id = problem.patient_id
    row.label = problem.label
    row.icd10_code = problem.icd10_code
    row.status = problem.status
    row.position = problem.position
    row.added_at = problem.added_at
    row.updated_at = problem.updated_at
    row.onset_date = problem.onset_date
    row.source_note_id = problem.source_note_id
    row.added_by = problem.added_by
    row.resolved_at = problem.resolved_at
    row.deleted_at = problem.deleted_at


class PostgresPatientProblemRepository(PatientProblemRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_patient(self, patient_id: str) -> list[Problem]:
        rows = self._session.scalars(
            select(PatientProblemRow).where(
                PatientProblemRow.patient_id == patient_id,
                PatientProblemRow.deleted_at.is_(None),
            )
        ).all()
        return sort_problems(_to_problem(r) for r in rows)

    def get(self, patient_id: str, problem_id: str) -> Problem | None:
        row = self._session.scalars(
            select(PatientProblemRow).where(
                PatientProblemRow.id == problem_id,
                PatientProblemRow.patient_id == patient_id,
                PatientProblemRow.deleted_at.is_(None),
            )
        ).one_or_none()
        return _to_problem(row) if row else None

    def save(self, problem: Problem) -> Problem:
        row = self._session.get(PatientProblemRow, problem.id)
        if row is None:
            row = PatientProblemRow()
            self._session.add(row)
        _write(problem, row)
        self._session.flush()
        return problem

    def set_positions(self, patient_id: str, ordered_ids: list[str]) -> None:
        rows = {
            r.id: r
            for r in self._session.scalars(
                select(PatientProblemRow).where(
                    PatientProblemRow.patient_id == patient_id,
                    PatientProblemRow.id.in_(ordered_ids),
                )
            )
        }
        now = utc_now()
        for position, problem_id in enumerate(ordered_ids):
            rows[problem_id].position = position
            rows[problem_id].updated_at = now
        self._session.flush()

    def sync_patient_diagnosis(self, patient_id: str) -> None:
        patient = self._session.get(PatientRow, patient_id)
        if patient is None:
            return
        patient.diagnosis = derived_diagnosis(self.list_by_patient(patient_id))
        self._session.flush()


def seed_problem_from_free_text(
    session: Session, patient_id: str, text: str | None, added_by: str
) -> None:
    """Start a new chart's problem list from a diagnosis typed as text.

    The way a diagnosis arrives with a new patient — the create form of an
    older client, an imported client list. It becomes one active problem
    (with its code when the text names exactly one), and the diagnosis line
    is derived from the list like every other time.
    """
    if text is None or not text.strip():
        return
    label, code = split_free_text_diagnosis(text)
    now = utc_now()
    repo = PostgresPatientProblemRepository(session)
    repo.save(
        Problem(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            label=label[:255],
            icd10_code=code,
            status=ProblemStatus.ACTIVE,
            position=0,
            added_at=now,
            updated_at=now,
            added_by=added_by,
        )
    )
    repo.sync_patient_diagnosis(patient_id)
