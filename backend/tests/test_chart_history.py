# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chart history: the fields, the trail every write keeps, and the API."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.chart_history.dependencies import get_chart_history_repository
from app.chart_history.fields import HISTORY_GROUPS, HISTORY_KEYS, SUBSTANCE_KEYS
from app.chart_history.service import (
    ChartHistoryService,
    HistoryFieldEmptyError,
    UnknownHistoryFieldError,
)
from app.main import app
from app.models import Patient
from app.notes.spec_templates import TEMPLATES_DIR
from app.repositories import InMemoryChartHistoryRepository, InMemoryPatientRepository

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

TEMPLATES = TEMPLATES_DIR


def _seed_patient(repo: InMemoryPatientRepository, user_id: str) -> Patient:
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()), first_name="Sam", last_name="Sample", created_at=now, updated_at=now
    )
    return repo.create(patient, user_id)


# --- The fields ------------------------------------------------------------------


def test_every_key_is_listed_once() -> None:
    assert len(HISTORY_KEYS) == len(set(HISTORY_KEYS))


def _section_keys(template: str) -> dict[str, list[str]]:
    spec = json.loads((TEMPLATES / f"{template}.json").read_text())["spec"]
    return {s["key"]: [f["key"] for f in s["fields"]] for s in spec["sections"]}


@pytest.mark.parametrize("template", ["psychiatric_evaluation", "psychiatric_follow_up"])
def test_the_prescriber_templates_name_history_fields_as_the_chart_does(template: str) -> None:
    """A note field and the chart field it describes have the same key, in the same section."""
    sections = _section_keys(template)
    for group in HISTORY_GROUPS:
        assert sections.get(group.key) == [f.key for f in group.fields], group.key


def test_the_follow_up_reads_history_from_the_chart_and_screens_substances() -> None:
    spec = json.loads((TEMPLATES / "psychiatric_follow_up.json").read_text())["spec"]
    hints = {(s["key"], f["key"]): f["ai_hint"] for s in spec["sections"] for f in s["fields"]}
    history = [k for k in HISTORY_KEYS if k not in SUBSTANCE_KEYS]
    for group in HISTORY_GROUPS:
        for field in group.fields:
            hint = hints[(group.key, field.key)]
            if field.key in history:
                assert hint.startswith("From the chart, exactly as given, or 'Not recorded'.")
                assert '(stated this visit: "...")' in hint
            else:
                assert hint.startswith("The chart's substance use baseline for ")
                assert "(asked this visit: no change)" in hint
                assert '(stated this visit: "...")' in hint
                assert "(not asked this visit)" in hint


def test_the_follow_up_places_history_after_medications_and_before_risk() -> None:
    order = list(_section_keys("psychiatric_follow_up"))
    medications, risk = order.index("medications"), order.index("risk")
    history = [g.key for g in HISTORY_GROUPS if g.key != "substance_use"]
    assert order[medications + 1 : risk] == history


# --- The service -----------------------------------------------------------------


@pytest.fixture
def service() -> ChartHistoryService:
    return ChartHistoryService(InMemoryChartHistoryRepository())


def test_a_first_value_has_no_earlier_one(service: ChartHistoryService) -> None:
    entry = service.set("p1", "living_situation", "u1", "Lives alone.", source_note_id="n1")

    assert (entry.text, entry.updated_by, entry.source_note_id) == ("Lives alone.", "u1", "n1")
    assert service.revisions("p1") == []


def test_an_edit_keeps_the_value_it_replaced(service: ChartHistoryService) -> None:
    first = service.set("p1", "living_situation", "u1", "Lives with spouse.", source_note_id="n1")
    second = service.set("p1", "living_situation", "u2", "Separated; lives alone since August.")

    assert service.entries("p1")["living_situation"].text == second.text
    [earlier] = service.revisions("p1")
    assert (earlier.text, earlier.written_by, earlier.written_at, earlier.source_note_id) == (
        "Lives with spouse.",
        "u1",
        first.updated_at,
        "n1",
    )
    assert (earlier.replaced_by, earlier.replaced_at) == ("u2", second.updated_at)


def test_the_same_text_again_changes_nothing(service: ChartHistoryService) -> None:
    service.set("p1", "supports", "u1", "Sister nearby.")
    service.set("p1", "supports", "u2", "Sister nearby.")

    assert service.revisions("p1") == []
    assert service.entries("p1")["supports"].updated_by == "u1"


def test_removing_a_value_empties_the_field_and_keeps_it(service: ChartHistoryService) -> None:
    service.set("p1", "legal_custody", "u1", "Custody hearing pending.")
    service.remove("p1", "legal_custody", "u2")

    assert service.entries("p1")["legal_custody"].text is None
    [earlier] = service.revisions("p1")
    assert (earlier.text, earlier.replaced_by) == ("Custody hearing pending.", "u2")


def test_an_empty_field_has_nothing_to_remove(service: ChartHistoryService) -> None:
    with pytest.raises(HistoryFieldEmptyError):
        service.remove("p1", "legal_custody", "u1")


def test_an_unknown_key_is_refused(service: ChartHistoryService) -> None:
    with pytest.raises(UnknownHistoryFieldError):
        service.set("p1", "favourite_colour", "u1", "Blue.")


def test_one_clients_history_is_not_anothers(service: ChartHistoryService) -> None:
    service.set("p1", "trauma_history", "u1", "Denies.")
    assert service.entries("p2") == {}


# --- The API -----------------------------------------------------------------------


@pytest.fixture
def history_repo() -> Iterator[InMemoryChartHistoryRepository]:
    repo = InMemoryChartHistoryRepository()
    app.dependency_overrides[get_chart_history_repository] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_chart_history_repository, None)


def _field(body: dict[str, object], key: str) -> dict[str, object]:
    groups = body["groups"]
    assert isinstance(groups, list)
    return next(f for g in groups for f in g["fields"] if f["key"] == key)


def test_every_field_is_listed_and_an_empty_one_reads_none(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
    history_repo: InMemoryChartHistoryRepository,
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)

    response = client.get(f"/api/patients/{patient.id}/chart-history")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [f["key"] for g in body["groups"] for f in g["fields"]] == list(HISTORY_KEYS)
    assert _field(body, "trauma_history")["text"] is None
    assert _field(body, "trauma_history")["earlier"] == []


def test_editing_a_field_records_who_and_when_and_keeps_the_prior_text(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
    history_repo: InMemoryChartHistoryRepository,
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)
    url = f"/api/patients/{patient.id}/chart-history/work_school"

    first = client.put(url, json={"text": "  Teacher, full time.  "})
    second = client.put(url, json={"text": "On leave from teaching since September."})

    assert first.status_code == 200, first.text
    assert first.json()["text"] == "Teacher, full time."
    field = second.json()
    assert field["text"] == "On leave from teaching since September."
    assert field["updated_by"] == mock_user_id
    assert field["updated_at"] is not None
    assert [(e["text"], e["written_by"]) for e in field["earlier"]] == [
        ("Teacher, full time.", mock_user_id)
    ]
    listed = _field(client.get(f"/api/patients/{patient.id}/chart-history").json(), "work_school")
    assert listed == field


def test_removing_a_field_keeps_it_in_the_history(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
    history_repo: InMemoryChartHistoryRepository,
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)
    url = f"/api/patients/{patient.id}/chart-history/family_medical"
    client.put(url, json={"text": "Father: type 2 diabetes."})

    assert client.delete(url).status_code == 204
    assert client.delete(url).status_code == 404

    field = _field(client.get(f"/api/patients/{patient.id}/chart-history").json(), "family_medical")
    assert field["text"] is None
    assert [e["text"] for e in field["earlier"]] == ["Father: type 2 diabetes."]


@pytest.mark.parametrize(
    ("key", "body", "status"),
    [
        ("favourite_colour", {"text": "Blue."}, 404),
        ("supports", {"text": "   "}, 422),
    ],
)
def test_an_unknown_key_or_blank_text_is_refused(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
    history_repo: InMemoryChartHistoryRepository,
    key: str,
    body: dict[str, str],
    status: int,
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)
    response = client.put(f"/api/patients/{patient.id}/chart-history/{key}", json=body)
    assert response.status_code == status, response.text


def test_a_patient_the_caller_cannot_see_is_not_found(
    client: TestClient, history_repo: InMemoryChartHistoryRepository
) -> None:
    patient_id = str(uuid.uuid4())
    assert client.get(f"/api/patients/{patient_id}/chart-history").status_code == 404
    response = client.put(
        f"/api/patients/{patient_id}/chart-history/supports", json={"text": "Sister."}
    )
    assert response.status_code == 404


def test_a_telehealth_location_is_not_where_the_client_lives() -> None:
    spec = json.loads((TEMPLATES / "psychiatric_follow_up.json").read_text())["spec"]
    hints = {(s["key"], f["key"]): f["ai_hint"] for s in spec["sections"] for f in s["fields"]}
    hint = hints[("social_history", "living_situation")]
    assert "location during a telehealth visit" in hint
    assert "never changes this field" in hint


@pytest.mark.parametrize("template", ["psychiatric_evaluation", "psychiatric_follow_up"])
def test_the_prescriber_templates_never_gender_the_client(template: str) -> None:
    spec = json.loads((TEMPLATES / f"{template}.json").read_text())["spec"]
    assert "never he, she, his or her" in spec["system_prompt"]
    assert "they/them" in spec["system_prompt"]
