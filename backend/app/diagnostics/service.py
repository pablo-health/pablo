# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Business logic for the diagnostics API.

Loads the definition, runs the single metadata-driven evaluator, validates a
clinician-confirmed code against the definition's options, and persists the
determination. Reads recompute the suggested code + unmet reasons from the
snapshotted definition version.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, cast

from ..models.enums import OutcomeMeasureSource
from ..repositories.diagnostic_assessment import PatientDiagnosticAccessDeniedError
from ..utcnow import utc_now
from .evaluator import UnknownEvaluatorTypeError, evaluate
from .schemas import (
    CreateDiagnosticAssessmentRequest,
    CriterionGroupView,
    CriterionView,
    DiagnosticAssessmentResponse,
    DiagnosticDefinitionResponse,
    GateView,
    ICD10OptionView,
)

if TYPE_CHECKING:
    from ..repositories.diagnostic_assessment import DiagnosticAssessmentRepository
    from .definition_provider import DefinitionProvider
    from .definitions import DiagnosticDefinition


class UnknownDefinitionError(ValueError):
    """The requested definition code is not registered."""


class InvalidResponsesError(ValueError):
    """criterion_responses / gate_responses reference unknown keys."""


class InvalidCodeError(ValueError):
    """determined_icd10 is not one of the definition's options."""


class DiagnosticAssessmentNotFoundError(LookupError):
    """An assessment row cannot be found or is inaccessible."""


class DiagnosticService:
    def __init__(
        self,
        repo: DiagnosticAssessmentRepository,
        definitions: DefinitionProvider,
    ) -> None:
        self._repo = repo
        self._definitions = definitions

    # ------------------------------------------------------------------
    # Definitions (for rendering the form)
    # ------------------------------------------------------------------

    def list_definitions(self) -> list[DiagnosticDefinitionResponse]:
        return [_definition_response(d) for d in self._definitions.list_active()]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create(
        self,
        patient_id: str,
        request: CreateDiagnosticAssessmentRequest,
        user_id: str,
    ) -> DiagnosticAssessmentResponse:
        defn = self._definitions.get(request.instrument)
        if defn is None:
            raise UnknownDefinitionError(f"Unknown definition {request.instrument!r}.")

        unknown_criteria = set(request.criterion_responses) - set(defn.criterion_keys)
        unknown_gates = set(request.gate_responses) - set(defn.gate_keys)
        if unknown_criteria or unknown_gates:
            raise InvalidResponsesError(
                f"Unknown keys for {defn.code!r}: "
                f"criteria={sorted(unknown_criteria)} gates={sorted(unknown_gates)}"
            )

        try:
            outcome = evaluate(defn, request.criterion_responses, request.gate_responses)
        except UnknownEvaluatorTypeError as exc:
            raise UnknownDefinitionError(str(exc)) from exc

        code = request.determined_icd10
        if code is not None and code not in defn.icd10_codes:
            raise InvalidCodeError(f"Code {code!r} is not an option for {defn.code!r}.")

        now = utc_now()
        confirmed = request.source == OutcomeMeasureSource.MANUAL
        row: dict[str, object] = {
            "id": str(uuid.uuid4()),
            "patient_id": patient_id,
            "session_id": request.session_id,
            "appointment_id": request.appointment_id,
            "instrument": defn.code,
            "definition_version": defn.version,
            "criterion_responses": dict(request.criterion_responses),
            "gate_responses": dict(request.gate_responses),
            "meets_criteria": outcome.meets_criteria,
            "determined_icd10": request.determined_icd10,
            "diagnosis_label": request.diagnosis_label or defn.display_name,
            "criterion_citations": None,
            "source": request.source.value,
            "confirmed_at": now if confirmed else None,
            "assessed_at": request.assessed_at,
            "created_by": user_id,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        try:
            saved = self._repo.add(row, user_id)
        except PatientDiagnosticAccessDeniedError as exc:
            raise DiagnosticAssessmentNotFoundError(patient_id) from exc
        return self._build_response(saved)

    def soft_delete(self, assessment_id: str, user_id: str) -> None:
        existing = self._repo.get(assessment_id, user_id)
        if existing is None or existing.get("deleted_at") is not None:
            raise DiagnosticAssessmentNotFoundError(assessment_id)
        now = utc_now()
        existing["deleted_at"] = now
        existing["updated_at"] = now
        self._repo.update(existing, user_id)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, assessment_id: str, user_id: str) -> DiagnosticAssessmentResponse:
        row = self._repo.get(assessment_id, user_id)
        if row is None or row.get("deleted_at") is not None:
            raise DiagnosticAssessmentNotFoundError(assessment_id)
        return self._build_response(row)

    def list_for_patient(
        self,
        patient_id: str,
        user_id: str,
        instrument: str | None = None,
    ) -> list[DiagnosticAssessmentResponse]:
        rows = self._repo.list_by_patient(patient_id, user_id, instrument=instrument)
        live = [r for r in rows if r.get("deleted_at") is None]
        live.sort(key=lambda r: r["assessed_at"])  # type: ignore[arg-type, return-value]
        return [self._build_response(r) for r in live]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_response(self, row: dict[str, object]) -> DiagnosticAssessmentResponse:
        code = str(row["instrument"])
        version = int(cast("int", row["definition_version"]))
        criterion_responses = cast("dict[str, bool]", row.get("criterion_responses") or {})
        gate_responses = cast("dict[str, bool]", row.get("gate_responses") or {})

        suggested: str | None = None
        unmet: list[str] = []
        defn = self._definitions.get(code, version=version) or self._definitions.get(code)
        if defn is not None:
            outcome = evaluate(defn, criterion_responses, gate_responses)
            suggested = outcome.suggested_icd10
            unmet = list(outcome.unmet_reasons)

        determined = str(row["determined_icd10"]) if row.get("determined_icd10") else None
        label = str(row["diagnosis_label"]) if row.get("diagnosis_label") else None
        return DiagnosticAssessmentResponse(
            id=str(row["id"]),
            patient_id=str(row["patient_id"]),
            session_id=str(row["session_id"]) if row.get("session_id") else None,
            appointment_id=(str(row["appointment_id"]) if row.get("appointment_id") else None),
            instrument=code,
            definition_version=version,
            criterion_responses=criterion_responses,
            gate_responses=gate_responses,
            meets_criteria=(None if (mc := row.get("meets_criteria")) is None else bool(mc)),
            determined_icd10=determined,
            diagnosis_label=label,
            source=str(row["source"]),
            confirmed_at=row.get("confirmed_at"),  # type: ignore[arg-type]
            assessed_at=row["assessed_at"],  # type: ignore[arg-type]
            created_by=str(row["created_by"]),
            created_at=row["created_at"],  # type: ignore[arg-type]
            updated_at=row["updated_at"],  # type: ignore[arg-type]
            suggested_icd10=suggested,
            unmet_reasons=unmet,
        )


def _definition_response(defn: DiagnosticDefinition) -> DiagnosticDefinitionResponse:
    return DiagnosticDefinitionResponse(
        code=defn.code,
        version=defn.version,
        display_name=defn.display_name,
        evaluator_type=defn.evaluator_type,
        criterion_groups=[
            CriterionGroupView(
                key=g.key,
                label=g.label,
                min_met=g.min_met,
                require_cardinal=g.require_cardinal,
                criteria=[
                    CriterionView(key=c.key, label=c.label, cardinal=c.cardinal) for c in g.criteria
                ],
            )
            for g in defn.criterion_groups
        ],
        gates=[GateView(key=g.key, label=g.label) for g in defn.gates],
        icd10_options=[ICD10OptionView(code=o.code, label=o.label) for o in defn.icd10_options],
        suggested_icd10=defn.suggested_icd10,
    )
