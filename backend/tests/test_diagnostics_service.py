# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Service-level tests for the diagnostics engine (PABLO-6xj.1).

DB-free: an in-memory assessment repository plus the baseline (bundled)
definition provider. Covers the create→evaluate→persist path, code
validation, response-key validation, and the read/delete round-trip.
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

_PATIENT = "11111111-1111-1111-1111-111111111111"
_USER = "clinician-1"
_WHEN = datetime(2026, 6, 2, 15, 0, tzinfo=UTC)


def _service() -> tuple[DiagnosticService, InMemoryDiagnosticAssessmentRepository]:
    repo = InMemoryDiagnosticAssessmentRepository()
    repo.grant_all_access()
    return DiagnosticService(repo, BaselineDefinitionProvider()), repo


def _mdd_met_request(**overrides) -> CreateDiagnosticAssessmentRequest:
    data = {
        "instrument": "mdd",
        "source": OutcomeMeasureSource.MANUAL,
        "assessed_at": _WHEN,
        "criterion_responses": {
            "A1": True,
            "A3": True,
            "A4": True,
            "A6": True,
            "A8": True,
        },
        "gate_responses": {
            "duration": True,
            "impairment": True,
            "not_substance_medical": True,
            "not_psychotic": True,
            "no_mania_history": True,
        },
        "determined_icd10": "F32.9",
    }
    data.update(overrides)
    return CreateDiagnosticAssessmentRequest(**data)


def test_create_meets_criteria_and_persists():
    service, _ = _service()
    resp = service.create(_PATIENT, _mdd_met_request(), _USER)

    assert resp.meets_criteria is True
    assert resp.suggested_icd10 == "F32.9"
    assert resp.determined_icd10 == "F32.9"
    assert resp.diagnosis_label == "Major Depressive Disorder"
    assert resp.definition_version == 1
    assert resp.confirmed_at is not None  # manual entry is confirmed
    assert resp.unmet_reasons == []
    # Persisted and readable back.
    assert service.get(resp.id, _USER).id == resp.id


def test_below_threshold_records_not_met():
    service, _ = _service()
    resp = service.create(
        _PATIENT,
        _mdd_met_request(
            criterion_responses={"A1": True, "A3": True},
            determined_icd10=None,
        ),
        _USER,
    )
    assert resp.meets_criteria is False
    assert resp.suggested_icd10 is None
    assert any("at least 5" in r for r in resp.unmet_reasons)


def test_unknown_instrument_raises():
    service, _ = _service()
    with pytest.raises(UnknownDefinitionError):
        service.create(_PATIENT, _mdd_met_request(instrument="nope"), _USER)


def test_unknown_criterion_key_raises():
    service, _ = _service()
    with pytest.raises(InvalidResponsesError):
        service.create(
            _PATIENT,
            _mdd_met_request(criterion_responses={"ZZ": True}),
            _USER,
        )


def test_code_not_in_definition_options_raises():
    service, _ = _service()
    with pytest.raises(InvalidCodeError):
        service.create(_PATIENT, _mdd_met_request(determined_icd10="F99.9"), _USER)


def test_list_and_soft_delete_roundtrip():
    service, _ = _service()
    resp = service.create(_PATIENT, _mdd_met_request(), _USER)
    assert len(service.list_for_patient(_PATIENT, _USER)) == 1
    assert len(service.list_for_patient(_PATIENT, _USER, instrument="mdd")) == 1
    assert len(service.list_for_patient(_PATIENT, _USER, instrument="gad")) == 0

    service.soft_delete(resp.id, _USER)
    assert service.list_for_patient(_PATIENT, _USER) == []
    with pytest.raises(DiagnosticAssessmentNotFoundError):
        service.get(resp.id, _USER)


def test_access_denied_surfaces_as_not_found():
    repo = InMemoryDiagnosticAssessmentRepository()  # no grant
    service = DiagnosticService(repo, BaselineDefinitionProvider())
    with pytest.raises(DiagnosticAssessmentNotFoundError):
        service.create(_PATIENT, _mdd_met_request(), _USER)


def test_list_definitions_exposes_baseline():
    service, _ = _service()
    defs = service.list_definitions()
    codes = {d.code for d in defs}
    assert {"mdd", "gad"} <= codes
    mdd = next(d for d in defs if d.code == "mdd")
    assert mdd.criterion_groups[0].min_met == 5
    assert any(g.require_cardinal for g in mdd.criterion_groups)
