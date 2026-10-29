# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Problem-list operations.

Every write ends by re-deriving ``patients.diagnosis`` from the list, so the
display line can never disagree with the record it is derived from.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..utcnow import utc_now
from .models import Problem, ProblemStatus

if TYPE_CHECKING:
    from ..repositories.patient_problem import PatientProblemRepository
    from .schemas import AddProblemRequest, UpdateProblemRequest


class ProblemNotFoundError(LookupError):
    """No live problem with that id on this patient's list."""


class DuplicateProblemError(ValueError):
    """The list already has this problem; ``existing`` is the row it matches."""

    def __init__(self, existing: Problem) -> None:
        super().__init__("problem already on the list")
        self.existing = existing


class InvalidProblemOrderError(ValueError):
    """A reorder that does not name every live problem exactly once."""


def _same_problem(problem: Problem, label: str, code: str | None) -> bool:
    if code is not None or problem.icd10_code is not None:
        return problem.icd10_code == code
    return problem.label.casefold() == label.casefold()


class ProblemService:
    def __init__(self, repo: PatientProblemRepository) -> None:
        self._repo = repo

    def problems(self, patient_id: str) -> list[Problem]:
        return self._repo.list_by_patient(patient_id)

    def visit_codes(self, patient_id: str) -> list[str]:
        """What a visit's diagnosis codes pre-fill with: the active problems' codes.

        Primary first, in list order. Only codes the bundled ICD-10-CM catalog
        knows, which is the check a visit's codes get when they are edited, so
        a pre-filled visit can always be saved back unchanged.
        """
        # Deferred for the reason ``validate_visit_diagnosis_codes`` gives.
        from ..diagnostics.catalog import known_icd10_codes  # noqa: PLC0415

        known = known_icd10_codes()
        codes: list[str] = []
        for p in self._repo.list_by_patient(patient_id):
            code = p.icd10_code
            if p.status == ProblemStatus.ACTIVE and code in known and code not in codes:
                codes.append(code)
        return codes

    def add(self, patient_id: str, user_id: str, req: AddProblemRequest) -> Problem:
        """Append a problem to the list.

        A problem already listed — the same code, or with no code on either
        side the same label — is refused with the row it matches, so adding
        the same diagnosis from a note twice does not list it twice.
        """
        existing = self._repo.list_by_patient(patient_id)
        for problem in existing:
            if _same_problem(problem, req.label, req.icd10_code):
                raise DuplicateProblemError(problem)
        now = utc_now()
        problem = Problem(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            label=req.label,
            icd10_code=req.icd10_code,
            status=req.status,
            position=max((p.position for p in existing), default=-1) + 1,
            added_at=now,
            updated_at=now,
            onset_date=req.onset_date,
            source_note_id=req.source_note_id,
            added_by=user_id,
            resolved_at=now if req.status == ProblemStatus.RESOLVED.value else None,
        )
        self._repo.save(problem)
        self._repo.sync_patient_diagnosis(patient_id)
        return problem

    def update(self, patient_id: str, problem_id: str, req: UpdateProblemRequest) -> Problem:
        problem = self._get(patient_id, problem_id)
        sent = req.model_fields_set
        if req.label is not None:
            problem.label = req.label
        if "icd10_code" in sent:
            problem.icd10_code = req.icd10_code
        if "onset_date" in sent:
            problem.onset_date = req.onset_date
        now = utc_now()
        if req.status is not None and req.status != problem.status:
            problem.status = req.status
            problem.resolved_at = now if req.status == ProblemStatus.RESOLVED.value else None
        problem.updated_at = now
        self._repo.save(problem)
        self._repo.sync_patient_diagnosis(patient_id)
        return problem

    def reorder(self, patient_id: str, problem_ids: list[str]) -> list[Problem]:
        live = {p.id for p in self._repo.list_by_patient(patient_id)}
        if len(problem_ids) != len(set(problem_ids)) or set(problem_ids) != live:
            raise InvalidProblemOrderError("name every problem on the list exactly once")
        self._repo.set_positions(patient_id, problem_ids)
        self._repo.sync_patient_diagnosis(patient_id)
        return self._repo.list_by_patient(patient_id)

    def remove(self, patient_id: str, problem_id: str) -> None:
        """Take a problem off the list: one entered in error, not one resolved."""
        problem = self._get(patient_id, problem_id)
        now = utc_now()
        problem.deleted_at = now
        problem.updated_at = now
        self._repo.save(problem)
        self._repo.sync_patient_diagnosis(patient_id)

    def _get(self, patient_id: str, problem_id: str) -> Problem:
        problem = self._repo.get(patient_id, problem_id)
        if problem is None:
            raise ProblemNotFoundError(problem_id)
        return problem
