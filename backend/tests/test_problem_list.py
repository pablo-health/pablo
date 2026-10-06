# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The problem list: code shape, free-text carry-over, the derived line, and the API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.main import app
from app.models import Patient
from app.problems.models import (
    derived_diagnosis,
    normalize_icd10_code,
    split_free_text_diagnosis,
)
from app.problems.schemas import AddProblemRequest, UpdateProblemRequest
from app.problems.service import DuplicateProblemError, ProblemService
from app.repositories import InMemoryPatientProblemRepository, InMemoryPatientRepository
from app.repositories.diagnostic_assessment import InMemoryDiagnosticAssessmentRepository
from app.routes.patient_problems import get_problem_diagnostic_repository
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type
from pydantic import ValidationError


def _seed_patient(repo: InMemoryPatientRepository, user_id: str) -> Patient:
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()),
        first_name="Sam",
        last_name="Sample",
        created_at=now,
        updated_at=now,
    )
    return repo.create(patient, user_id)


# --- Code shape and free text ---------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "stored"),
    [("f41.1", "F41.1"), (" F33.1 ", "F33.1"), ("F43.10", "F43.10"), ("Z63", "Z63"), ("", None)],
)
def test_code_is_tidied_and_blank_clears(typed: str, stored: str | None) -> None:
    assert normalize_icd10_code(typed) == stored


@pytest.mark.parametrize("typed", ["anxiety", "41.1", "F4", "F41.12345", "FF1.1"])
def test_something_not_shaped_like_a_code_is_refused(typed: str) -> None:
    with pytest.raises(ValueError, match="not an ICD-10-CM code"):
        normalize_icd10_code(typed)


@pytest.mark.parametrize(
    ("text", "label", "code"),
    [
        ("F41.1 Generalized anxiety disorder", "Generalized anxiety disorder", "F41.1"),
        ("Generalized anxiety disorder (F41.1)", "Generalized anxiety disorder", "F41.1"),
        ("MDD - F33.1", "MDD", "F33.1"),
        ("Anxiety", "Anxiety", None),
        # Two codes: taking one would be a guess.
        ("F41.1, F33.1", "F41.1, F33.1", None),
        ("  adjustment   disorder ", "adjustment disorder", None),
    ],
)
def test_free_text_diagnosis_splits_only_a_single_code(text: str, label: str, code: str) -> None:
    assert split_free_text_diagnosis(text) == (label, code)


# --- Service ------------------------------------------------------------------


@pytest.fixture
def repo() -> InMemoryPatientProblemRepository:
    return InMemoryPatientProblemRepository()


def test_an_empty_list_derives_no_diagnosis(repo: InMemoryPatientProblemRepository) -> None:
    assert ProblemService(repo).problems("p1") == []
    assert derived_diagnosis([]) is None


def test_adding_resolving_and_reordering_rewrite_the_derived_line(
    repo: InMemoryPatientProblemRepository,
) -> None:
    service = ProblemService(repo)
    gad = service.add(
        "p1", "u1", AddProblemRequest(label="Generalized anxiety disorder", icd10_code="F41.1")
    )
    mdd = service.add(
        "p1", "u1", AddProblemRequest(label="Major depressive disorder", icd10_code="F33.1")
    )
    service.add("p1", "u1", AddProblemRequest(label="Bipolar II", status="rule_out"))
    assert repo.derived["p1"] == (
        "Generalized anxiety disorder (F41.1); Major depressive disorder (F33.1)"
    )

    service.reorder("p1", [mdd.id, gad.id, *[p.id for p in service.problems("p1")[2:]]])
    assert repo.derived["p1"] == (
        "Major depressive disorder (F33.1); Generalized anxiety disorder (F41.1)"
    )

    resolved = service.update("p1", gad.id, UpdateProblemRequest(status="resolved"))
    assert resolved.status == "resolved"
    assert resolved.resolved_at is not None
    assert repo.derived["p1"] == "Major depressive disorder (F33.1)"
    # Order reads active, then rule-out, then resolved.
    assert [p.status for p in service.problems("p1")] == ["active", "rule_out", "resolved"]

    reactivated = service.update("p1", gad.id, UpdateProblemRequest(status="active"))
    assert reactivated.resolved_at is None


def test_the_same_problem_is_not_listed_twice(repo: InMemoryPatientProblemRepository) -> None:
    service = ProblemService(repo)
    first = service.add("p1", "u1", AddProblemRequest(label="GAD", icd10_code="F41.1"))
    with pytest.raises(DuplicateProblemError) as exc:
        service.add("p1", "u1", AddProblemRequest(label="Generalized anxiety", icd10_code="f41.1"))
    assert exc.value.existing.id == first.id

    service.add("p1", "u1", AddProblemRequest(label="Insomnia"))
    with pytest.raises(DuplicateProblemError):
        service.add("p1", "u1", AddProblemRequest(label="insomnia"))


def test_removal_takes_a_problem_off_the_list(repo: InMemoryPatientProblemRepository) -> None:
    service = ProblemService(repo)
    entry = service.add("p1", "u1", AddProblemRequest(label="GAD", icd10_code="F41.1"))
    service.remove("p1", entry.id)
    assert service.problems("p1") == []
    assert repo.derived["p1"] is None


def test_a_label_typed_as_a_code_is_refused_at_entry() -> None:
    with pytest.raises(ValidationError):
        AddProblemRequest(label="Anxiety", icd10_code="anxiety")


# --- API ----------------------------------------------------------------------


@pytest.fixture
def assessments() -> InMemoryDiagnosticAssessmentRepository:
    repo = InMemoryDiagnosticAssessmentRepository()
    repo.grant_all_access()
    app.dependency_overrides[get_problem_diagnostic_repository] = lambda: repo
    return repo


def test_api_add_list_edit_reorder_remove(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_problem_repo: InMemoryPatientProblemRepository,
    mock_user_id: str,
    assessments: InMemoryDiagnosticAssessmentRepository,
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)
    base = f"/api/patients/{patient.id}/problems"

    assert client.get(base).json() == {"data": [], "total": 0}

    added = client.post(
        base,
        json={
            "label": "Generalized anxiety disorder",
            "icd10_code": "f41.1",
            "source_note_id": "note-1",
        },
    )
    assert added.status_code == 201, added.text
    body = added.json()
    assert body["icd10_code"] == "F41.1"
    assert body["status"] == "active"
    assert body["source_note_id"] == "note-1"
    assert body["added_by"] == mock_user_id

    uncoded = client.post(base, json={"label": "Insomnia", "status": "rule_out"}).json()
    assert uncoded["icd10_code"] is None

    duplicate = client.post(base, json={"label": "GAD", "icd10_code": "F41.1"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PROBLEM_ALREADY_LISTED"
    assert duplicate.json()["error"]["details"]["problem_id"] == body["id"]

    bad_code = client.post(base, json={"label": "Anxiety", "icd10_code": "anxiety"})
    assert bad_code.status_code == 422

    resolved = client.patch(f"{base}/{body['id']}", json={"status": "resolved"})
    assert resolved.status_code == 200
    assert resolved.json()["resolved_at"] is not None

    cleared = client.patch(f"{base}/{uncoded['id']}", json={"icd10_code": None, "label": "Sleep"})
    assert cleared.json()["label"] == "Sleep"

    reordered = client.put(f"{base}/order", json={"problem_ids": [uncoded["id"], body["id"]]})
    assert reordered.status_code == 200
    assert [p["id"] for p in reordered.json()["data"]] == [uncoded["id"], body["id"]]

    incomplete = client.put(f"{base}/order", json={"problem_ids": [body["id"]]})
    assert incomplete.status_code == 400

    assert client.delete(f"{base}/{uncoded['id']}").status_code == 204
    assert client.delete(f"{base}/{uncoded['id']}").status_code == 404
    assert [p["id"] for p in client.get(base).json()["data"]] == [body["id"]]


def test_api_links_the_diagnostic_worksheet_to_the_new_problem(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
    assessments: InMemoryDiagnosticAssessmentRepository,
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)
    now = datetime.now(UTC)
    assessments.add(
        {
            "id": "da-1",
            "patient_id": patient.id,
            "instrument": "gad",
            "determined_icd10": "F41.1",
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        },
        mock_user_id,
    )

    added = client.post(
        f"/api/patients/{patient.id}/problems",
        json={"label": "GAD", "icd10_code": "F41.1", "diagnostic_assessment_id": "da-1"},
    )

    assert added.status_code == 201, added.text
    linked = assessments.get("da-1", mock_user_id)
    assert linked is not None
    assert linked["problem_id"] == added.json()["id"]

    missing = client.post(
        f"/api/patients/{patient.id}/problems",
        json={"label": "Other", "diagnostic_assessment_id": "nope"},
    )
    assert missing.status_code == 404


def test_api_answers_404_for_a_patient_the_caller_cannot_see(
    client: TestClient, assessments: InMemoryDiagnosticAssessmentRepository
) -> None:
    unknown = f"/api/patients/{uuid.uuid4()}/problems"
    assert client.get(unknown).status_code == 404
    assert client.post(unknown, json={"label": "GAD"}).status_code == 404
