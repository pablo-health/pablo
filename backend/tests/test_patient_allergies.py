# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Allergies on the patient: three states, and the derived diagnosis stays derived."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.models import Patient
from app.models.patient import UpdateAllergiesRequest
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type
from pydantic import ValidationError

if TYPE_CHECKING:
    from app.repositories import InMemoryPatientRepository


def _seed_patient(repo: InMemoryPatientRepository, user_id: str) -> Patient:
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()), first_name="Sam", last_name="Sample", created_at=now, updated_at=now
    )
    return repo.create(patient, user_id)


def test_a_new_patient_reads_not_recorded(
    client: TestClient, mock_repo: InMemoryPatientRepository, mock_user_id: str
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)

    body = client.get(f"/api/patients/{patient.id}").json()

    assert body["allergy_status"] == "not_recorded"
    assert body["allergies"] == []


def test_recording_nkda_then_a_list_then_clearing(
    client: TestClient, mock_repo: InMemoryPatientRepository, mock_user_id: str
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)
    url = f"/api/patients/{patient.id}/allergies"

    nkda = client.put(url, json={"status": "nkda"})
    assert nkda.status_code == 200, nkda.text
    assert nkda.json()["allergy_status"] == "nkda"
    assert nkda.json()["allergies"] == []

    listed = client.put(
        url,
        json={
            "status": "recorded",
            "allergies": [
                {"substance": "Penicillin", "reaction": "Hives", "severity": "moderate"},
                {"substance": "Sulfa"},
            ],
        },
    )
    assert listed.status_code == 200, listed.text
    unnoted = {"note": None, "source_note_id": None}
    assert listed.json()["allergies"] == [
        {"substance": "Penicillin", "reaction": "Hives", "severity": "moderate", **unnoted},
        {"substance": "Sulfa", "reaction": None, "severity": None, **unnoted},
    ]
    stored = mock_repo.get(patient.id, mock_user_id)
    assert stored is not None
    assert stored.allergies == [
        {"substance": "Penicillin", "reaction": "Hives", "severity": "moderate"},
        {"substance": "Sulfa"},
    ]

    cleared = client.put(url, json={"status": "not_recorded"})
    assert cleared.json()["allergy_status"] == "not_recorded"


@pytest.mark.parametrize(
    "body",
    [
        {"status": "recorded", "allergies": []},
        {"status": "nkda", "allergies": [{"substance": "Latex"}]},
        {"status": "not_recorded", "allergies": [{"substance": "Latex"}]},
        {"status": "recorded", "allergies": [{"substance": ""}]},
        {"status": "unknown"},
    ],
)
def test_an_empty_list_never_stands_in_for_nkda(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        UpdateAllergiesRequest.model_validate(body)


def test_allergies_for_a_patient_the_caller_cannot_see_is_404(client: TestClient) -> None:
    response = client.put(f"/api/patients/{uuid.uuid4()}/allergies", json={"status": "nkda"})
    assert response.status_code == 404


def test_the_diagnosis_is_kept_on_the_problem_list_not_patched(
    client: TestClient, mock_repo: InMemoryPatientRepository, mock_user_id: str
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)

    response = client.patch(f"/api/patients/{patient.id}", json={"diagnosis": "Anxiety"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DIAGNOSIS_FROM_PROBLEM_LIST"
