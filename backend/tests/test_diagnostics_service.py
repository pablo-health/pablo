# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Service-level tests for the diagnostics engine (PABLO-6xj.1).

DB-free: an in-memory assessment repository plus a definition provider over a
synthetic, non-clinical fixture. Covers the create->evaluate->persist path,
code validation, response-key validation, and the read/delete round-trip.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.diagnostics.definition_provider import BaselineDefinitionProvider
from app.diagnostics.schemas import CreateDiagnosticAssessmentRequest
from app.diagnostics.service import (
    DiagnosticAssessmentNotFoundError,
    DiagnosticService,
    InvalidCodeError,
    InvalidResponsesError,
    UnknownDefinitionError,
)
from app.models.enums import OutcomeMeasureSource
from app.repositories.diagnostic_assessment import InMemoryDiagnosticAssessmentRepository

from .diagnostics_fixtures import (
    SYNTHETIC_ALL_GATES,
    SYNTHETIC_DEFINITIONS,
    SYNTHETIC_MET_CRITERIA,
)

_PATIENT = "11111111-1111-1111-1111-111111111111"
_USER = "clinician-1"
_WHEN = datetime(2026, 6, 2, 15, 0, tzinfo=UTC)


def _service() -> tuple[DiagnosticService, InMemoryDiagnosticAssessmentRepository]:
    repo = InMemoryDiagnosticAssessmentRepository()
    repo.grant_all_access()
    return DiagnosticService(repo, BaselineDefinitionProvider(SYNTHETIC_DEFINITIONS)), repo


def _met_request(**overrides) -> CreateDiagnosticAssessmentRequest:
    data = {
        "instrument": "synthetic",
        "source": OutcomeMeasureSource.MANUAL,
        "assessed_at": _WHEN,
        "criterion_responses": dict(SYNTHETIC_MET_CRITERIA),
        "gate_responses": dict(SYNTHETIC_ALL_GATES),
        "determined_icd10": "T00.1",
    }
    data.update(overrides)
    return CreateDiagnosticAssessmentRequest(**data)


def test_create_meets_criteria_and_persists():
    service, _ = _service()
    resp = service.create(_PATIENT, _met_request(), _USER)

    assert resp.meets_criteria is True
    assert resp.suggested_icd10 == "T00.1"
    assert resp.determined_icd10 == "T00.1"
    assert resp.diagnosis_label == "Synthetic Screen"
    assert resp.definition_version == 1
    assert resp.confirmed_at is not None  # manual entry is confirmed
    assert resp.unmet_reasons == []
    # Persisted and readable back.
    assert service.get(resp.id, _USER).id == resp.id


def test_below_threshold_records_not_met():
    service, _ = _service()
    resp = service.create(
        _PATIENT,
        _met_request(
            criterion_responses={"A1": True, "B1": True},  # Group A short
            determined_icd10=None,
        ),
        _USER,
    )
    assert resp.meets_criteria is False
    assert resp.suggested_icd10 is None
    assert any("at least 2" in r for r in resp.unmet_reasons)


def test_checklist_persists_no_verdict_and_suggests_no_code():
    service, _ = _service()
    resp = service.create(
        _PATIENT,
        _met_request(instrument="synthetic_checklist"),
        _USER,
    )
    # No algorithmic verdict: must persist and read back as None, not False.
    assert resp.meets_criteria is None
    # No code is suggested for a checklist; the clinician confirmed it explicitly
    # (determined_icd10 came from the request, not from any engine suggestion).
    assert resp.suggested_icd10 is None
    assert resp.determined_icd10 == "T00.1"
    assert resp.unmet_reasons == []
    # The None round-trips through the repository read path.
    assert service.get(resp.id, _USER).meets_criteria is None


def test_unknown_instrument_raises():
    service, _ = _service()
    with pytest.raises(UnknownDefinitionError):
        service.create(_PATIENT, _met_request(instrument="nope"), _USER)


def test_unknown_criterion_key_raises():
    service, _ = _service()
    with pytest.raises(InvalidResponsesError):
        service.create(
            _PATIENT,
            _met_request(criterion_responses={"ZZ": True}),
            _USER,
        )


def test_code_not_in_definition_options_raises():
    service, _ = _service()
    with pytest.raises(InvalidCodeError):
        service.create(_PATIENT, _met_request(determined_icd10="F99.9"), _USER)


def test_list_and_soft_delete_roundtrip():
    service, _ = _service()
    resp = service.create(_PATIENT, _met_request(), _USER)
    assert len(service.list_for_patient(_PATIENT, _USER)) == 1
    assert len(service.list_for_patient(_PATIENT, _USER, instrument="synthetic")) == 1
    assert len(service.list_for_patient(_PATIENT, _USER, instrument="synthetic2")) == 0

    service.soft_delete(resp.id, _USER)
    assert service.list_for_patient(_PATIENT, _USER) == []
    with pytest.raises(DiagnosticAssessmentNotFoundError):
        service.get(resp.id, _USER)


def test_access_denied_surfaces_as_not_found():
    repo = InMemoryDiagnosticAssessmentRepository()  # no grant
    service = DiagnosticService(repo, BaselineDefinitionProvider(SYNTHETIC_DEFINITIONS))
    with pytest.raises(DiagnosticAssessmentNotFoundError):
        service.create(_PATIENT, _met_request(), _USER)


def test_list_definitions_exposes_provider_content():
    service, _ = _service()
    defs = service.list_definitions()
    codes = {d.code for d in defs}
    assert {"synthetic", "synthetic2"} <= codes
    synthetic = next(d for d in defs if d.code == "synthetic")
    assert synthetic.criterion_groups[0].min_met == 2
    assert any(g.require_cardinal for g in synthetic.criterion_groups)
