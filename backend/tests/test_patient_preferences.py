# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient preferences: the kind registry, the service, and the API."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.models import AuditAction
from app.preferences.dependencies import get_preference_repository
from app.preferences.kinds import (
    AVAILABILITY,
    HARD_LIMIT,
    PRACTICAL_NOTE,
    Domain,
    InvalidPreferenceError,
    KindSpec,
    Strength,
    TextRule,
    get_kind,
    normalize,
    register_kind,
)
from app.preferences.schemas import AddPreferenceRequest, UpdatePreferenceRequest
from app.preferences.service import (
    DuplicatePreferenceError,
    PreferenceNotFoundError,
    PreferenceService,
)
from app.repositories import InMemoryPatientPreferenceRepository, InMemoryPatientRepository
from pydantic import BaseModel, ConfigDict

from tests.patient_seeds import seed_patient

from .conftest import audited_entries

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.services import AuditService
    from fastapi.testclient import TestClient

AFTERNOONS = {"days": ["thu", "mon"], "times": ["afternoon"], "modality": "in_person"}
SAID = "Prefers afternoons because of school pickup"


# --- Kinds --------------------------------------------------------------------


def test_scheduling_value_is_stored_in_week_and_day_order() -> None:
    n = normalize(
        AVAILABILITY,
        {"days": ["fri", "mon", "mon"], "times": ["evening", "morning"], "modality": "either"},
        None,
        None,
    )
    assert n.domain is Domain.SCHEDULING
    assert n.value == {
        "days": ["mon", "fri"],
        "times": ["morning", "evening"],
        "modality": "either",
    }
    assert n.strength is Strength.SOFT


@pytest.mark.parametrize(
    ("kind", "value", "text", "strength"),
    [
        ("scheduling.favourite_colour", None, "Blue", None),
        (AVAILABILITY, {"days": [], "times": ["morning"], "modality": "either"}, None, None),
        (AVAILABILITY, {"days": ["mon"], "times": [], "modality": "either"}, None, None),
        (
            AVAILABILITY,
            {"days": ["monday"], "times": ["morning"], "modality": "either"},
            None,
            None,
        ),
        (AVAILABILITY, {"days": ["mon"], "times": ["noon"], "modality": "either"}, None, None),
        (AVAILABILITY, {"days": ["mon"], "times": ["morning"], "modality": "phone"}, None, None),
        (AVAILABILITY, {"days": ["mon"], "times": ["morning"]}, None, None),
        (AVAILABILITY, {**AFTERNOONS, "rooms": ["2"]}, None, None),
        (AVAILABILITY, None, "afternoons", None),
        (AVAILABILITY, AFTERNOONS, None, "firm"),
        (HARD_LIMIT, AFTERNOONS, None, "soft"),
        (PRACTICAL_NOTE, None, None, None),
        (PRACTICAL_NOTE, None, "   ", None),
        (PRACTICAL_NOTE, None, "x" * 281, None),
        (PRACTICAL_NOTE, AFTERNOONS, "Parking at the back", None),
        (PRACTICAL_NOTE, None, "Parking at the back", "hard"),
    ],
)
def test_a_write_that_does_not_fit_its_kind_is_refused(
    kind: str, value: dict[str, Any] | None, text: str | None, strength: str | None
) -> None:
    with pytest.raises(InvalidPreferenceError):
        normalize(kind, value, text, strength)


def test_a_hard_limit_is_always_hard() -> None:
    assert normalize(HARD_LIMIT, AFTERNOONS, "Work", None).strength is Strength.HARD


class _Interpreter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str


@pytest.fixture
def interpreter_kind() -> Iterator[str]:
    kind = "communication.interpreter"
    register_kind(
        KindSpec(
            kind=kind,
            domain=Domain.COMMUNICATION,
            label="Interpreter",
            value_model=_Interpreter,
            summarize=lambda v: f"Needs a {v['language']} interpreter",
        )
    )
    yield kind
    from app.preferences import kinds  # noqa: PLC0415

    kinds._REGISTRY.pop(kind, None)


def test_a_kind_registered_at_startup_is_validated_by_its_own_schema(
    interpreter_kind: str,
) -> None:
    assert normalize(interpreter_kind, {"language": "Spanish"}, None, None).value == {
        "language": "Spanish"
    }
    with pytest.raises(InvalidPreferenceError):
        normalize(interpreter_kind, {"lang": "Spanish"}, None, None)


def test_a_kind_with_no_value_must_require_text() -> None:
    with pytest.raises(ValueError, match="must require text"):
        register_kind(
            KindSpec(kind="practical.x", domain=Domain.PRACTICAL, label="X", text=TextRule.OPTIONAL)
        )
    assert get_kind("practical.x") is None


# --- Service ------------------------------------------------------------------


@pytest.fixture
def repo() -> InMemoryPatientPreferenceRepository:
    return InMemoryPatientPreferenceRepository()


def test_one_active_row_for_a_single_active_kind(repo: InMemoryPatientPreferenceRepository) -> None:
    service = PreferenceService(repo)
    first = service.add("p1", "u1", AddPreferenceRequest(kind=AVAILABILITY, value=AFTERNOONS))
    with pytest.raises(DuplicatePreferenceError) as exc:
        service.add("p1", "u1", AddPreferenceRequest(kind=AVAILABILITY, value=AFTERNOONS))
    assert exc.value.existing.id == first.id

    service.retire("p1", first.id, "u1")
    again = service.add("p1", "u1", AddPreferenceRequest(kind=AVAILABILITY, value=AFTERNOONS))
    assert [p.id for p in service.preferences("p1")] == [again.id]
    assert {p.id for p in service.preferences("p1", include_retired=True)} == {first.id, again.id}


def test_hard_limits_may_be_many(repo: InMemoryPatientPreferenceRepository) -> None:
    service = PreferenceService(repo)
    for days in (["mon"], ["fri"]):
        service.add(
            "p1",
            "u1",
            AddPreferenceRequest(
                kind=HARD_LIMIT, value={"days": days, "times": ["morning"], "modality": "either"}
            ),
        )
    assert len(service.preferences("p1")) == 2


def test_editing_restates_and_keeps_what_was_not_sent(
    repo: InMemoryPatientPreferenceRepository,
) -> None:
    service = PreferenceService(repo)
    added = service.add(
        "p1", "u1", AddPreferenceRequest(kind=AVAILABILITY, value=AFTERNOONS, text=SAID)
    )
    edited = service.update("p1", added.id, UpdatePreferenceRequest(strength="hard"))
    assert (edited.value, edited.text, edited.strength) == (
        {"days": ["mon", "thu"], "times": ["afternoon"], "modality": "in_person"},
        SAID,
        "hard",
    )
    assert edited.confirmed_at is not None
    assert edited.stated_at >= added.stated_at

    cleared = service.update("p1", added.id, UpdatePreferenceRequest(text=None))
    assert cleared.text is None


def test_a_retired_preference_cannot_be_edited_or_retired_again(
    repo: InMemoryPatientPreferenceRepository,
) -> None:
    service = PreferenceService(repo)
    added = service.add("p1", "u1", AddPreferenceRequest(kind=PRACTICAL_NOTE, text="Parking"))
    retired = service.retire("p1", added.id, "u2")
    assert (retired.retired_by, retired.retired_at is not None) == ("u2", True)
    with pytest.raises(PreferenceNotFoundError):
        service.update("p1", added.id, UpdatePreferenceRequest(text="Back door"))
    with pytest.raises(PreferenceNotFoundError):
        service.retire("p1", added.id, "u2")
    with pytest.raises(PreferenceNotFoundError):
        service.retire("p2", added.id, "u2")


def test_many_clients_are_read_by_kind_in_one_call(
    repo: InMemoryPatientPreferenceRepository,
) -> None:
    service = PreferenceService(repo)
    service.add("p1", "u1", AddPreferenceRequest(kind=AVAILABILITY, value=AFTERNOONS))
    service.add("p2", "u1", AddPreferenceRequest(kind=HARD_LIMIT, value=AFTERNOONS))
    service.add("p2", "u1", AddPreferenceRequest(kind=PRACTICAL_NOTE, text="Parking"))
    service.add("p3", "u1", AddPreferenceRequest(kind=AVAILABILITY, value=AFTERNOONS))

    rows = service.active_for_patients(["p1", "p2"], [AVAILABILITY, HARD_LIMIT])
    assert sorted((p.patient_id, p.kind) for p in rows) == [
        ("p1", AVAILABILITY),
        ("p2", HARD_LIMIT),
    ]
    assert service.active_for_patients([], [AVAILABILITY]) == []


# --- API ----------------------------------------------------------------------


@pytest.fixture
def api(client: TestClient, repo: InMemoryPatientPreferenceRepository) -> TestClient:
    app.dependency_overrides[get_preference_repository] = lambda: repo
    return client


def test_api_add_list_edit_retire(
    api: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
    mock_audit_service: AuditService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    patient = seed_patient(mock_repo, mock_user_id)
    base = f"/api/patients/{patient.id}/preferences"

    assert api.get(base).json() == {"data": [], "total": 0}

    added = api.post(base, json={"kind": AVAILABILITY, "value": AFTERNOONS, "text": SAID})
    assert added.status_code == 201, added.text
    body = added.json()
    assert body["domain"] == "scheduling"
    assert body["kind_label"] == "Times that suit"
    assert body["value"]["days"] == ["mon", "thu"]
    assert (body["strength"], body["source"]) == ("soft", "clinician")

    duplicate = api.post(base, json={"kind": AVAILABILITY, "value": AFTERNOONS})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PREFERENCE_ALREADY_SET"
    assert duplicate.json()["error"]["details"]["preference_id"] == body["id"]

    invalid = api.post(base, json={"kind": HARD_LIMIT, "value": AFTERNOONS, "strength": "soft"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_PREFERENCE"
    assert api.post(base, json={"kind": "practical.anything", "text": "x"}).status_code == 422
    assert api.post(base, json={"kind": PRACTICAL_NOTE, "text": "x" * 281}).status_code == 422
    assert api.post(base, json={"kind": PRACTICAL_NOTE, "source": "learned"}).status_code == 422

    edited = api.patch(f"{base}/{body['id']}", json={"strength": "hard"})
    assert edited.status_code == 200, edited.text
    assert (edited.json()["strength"], edited.json()["text"]) == ("hard", SAID)

    retired = api.post(f"{base}/{body['id']}/retire")
    assert retired.status_code == 200
    assert retired.json()["retired_at"] is not None
    assert api.post(f"{base}/{body['id']}/retire").status_code == 404
    assert api.patch(f"{base}/{body['id']}", json={"text": "x"}).status_code == 404
    assert api.get(base).json()["total"] == 0
    assert api.get(f"{base}?include_retired=true").json()["total"] == 1

    entries = [e for e in audited_entries(mock_audit_service) if e.patient_id == patient.id]
    actions = [(e.action, (e.changes or {}).get("preferences")) for e in entries]
    assert (AuditAction.PATIENT_UPDATED, "added") in actions
    assert (AuditAction.PATIENT_UPDATED, "updated") in actions
    assert (AuditAction.PATIENT_UPDATED, "retired") in actions
    assert any(e.action == AuditAction.PATIENT_VIEWED for e in entries)
    for entry in entries:
        recorded = repr(entry.changes)
        assert "afternoon" not in recorded
        assert "school" not in recorded

    assert "school pickup" not in caplog.text


def test_api_answers_404_for_a_patient_the_caller_cannot_see(api: TestClient) -> None:
    unknown = f"/api/patients/{uuid.uuid4()}/preferences"
    assert api.get(unknown).status_code == 404
    assert api.post(unknown, json={"kind": PRACTICAL_NOTE, "text": "Parking"}).status_code == 404
    assert api.post(f"{unknown}/{uuid.uuid4()}/retire").status_code == 404


def test_api_lists_a_registered_kind_with_its_summary(
    api: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
    interpreter_kind: str,
) -> None:
    patient = seed_patient(mock_repo, mock_user_id)
    base = f"/api/patients/{patient.id}/preferences"
    added = api.post(base, json={"kind": interpreter_kind, "value": {"language": "Spanish"}})
    assert added.status_code == 201, added.text
    assert added.json()["summary"] == "Needs a Spanish interpreter"
    assert added.json()["domain"] == "communication"
