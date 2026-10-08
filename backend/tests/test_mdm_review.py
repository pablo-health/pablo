# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The medical decision making review beside a prescriber's note."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.models import Note
from app.notes import NoteTypeRegistry, get_default_registry, register_builtin_note_types
from app.notes.mdm import DATA_LEVELS, PROBLEMS_LEVELS, RISK_LEVELS
from app.notes.mdm_review import (
    CHOICE_INPUTS,
    choices_from,
    confirmed_minutes,
    dictated_codes,
    has_psychotherapy,
    offers_review,
    review,
    with_codes,
)

if TYPE_CHECKING:
    from app.notes import NoteTypeDefinition
    from app.repositories.note import InMemoryNotesRepository
    from fastapi.testclient import TestClient

_REGISTRY = NoteTypeRegistry()
register_builtin_note_types(_REGISTRY)
FOLLOW_UP = _REGISTRY.get("psychiatric_follow_up")
EVALUATION = _REGISTRY.get("psychiatric_evaluation")

MODERATE = {"mdm_problems": "moderate", "mdm_data": "limited", "mdm_risk": "moderate"}
THERAPY = {"psychotherapy": {"psychotherapy_time": "", "modality_interventions": "CBT."}}


def _content(visit_details: str = "Date of service: 2026-10-08.", **sections: Any) -> dict:
    return {
        "encounter": {"visit_details": visit_details},
        "mdm": {
            "problems_addressed": "Generalized anxiety, worse over two weeks; ADHD stable.",
            "data_reviewed": "GAD-7 reviewed.",
            "management_risk": "Prescription drug management: sertraline increased.",
        },
        **sections,
    }


class TestTheTypesThatOfferIt:
    @pytest.mark.parametrize("definition", [FOLLOW_UP, EVALUATION], ids=lambda d: d.key)
    def test_both_prescriber_types_offer_the_three_choices(
        self, definition: NoteTypeDefinition
    ) -> None:
        assert offers_review(definition)
        options = {i.key: i.options for i in definition.inputs}
        assert options[CHOICE_INPUTS["problems"]] == PROBLEMS_LEVELS
        assert options[CHOICE_INPUTS["data"]] == DATA_LEVELS
        assert options[CHOICE_INPUTS["risk"]] == RISK_LEVELS

    def test_a_type_without_the_choices_offers_nothing(self) -> None:
        assert not offers_review(_REGISTRY.get("soap"))

    def test_the_evaluation_is_a_new_patient_and_the_follow_up_established(self) -> None:
        assert choices_from(EVALUATION, {}).new_patient is True
        assert choices_from(FOLLOW_UP, {}).new_patient is False
        assert choices_from(FOLLOW_UP, {"new_patient": "new"}).new_patient is True

    def test_the_choices_never_reach_the_model(self) -> None:
        for definition in (FOLLOW_UP, EVALUATION):
            assert definition.user_template is not None
            for key in (*CHOICE_INPUTS.values(), "new_patient"):
                assert f"{{inputs.{key}}}" not in definition.user_template


class TestReview:
    def test_the_choices_give_the_level_and_the_code(self) -> None:
        result = review(FOLLOW_UP, MODERATE, _content(), None)

        assert result.level == "moderate"
        assert result.em_code == "99214"
        assert result.evidence["data"] == "GAD-7 reviewed."

    def test_a_new_patient_gets_the_new_patient_code(self) -> None:
        assert review(EVALUATION, MODERATE, _content(), None).em_code == "99204"

    def test_nothing_is_computed_until_all_three_are_chosen(self) -> None:
        result = review(FOLLOW_UP, {"mdm_problems": "moderate"}, _content(), None)

        assert result.level is None
        assert result.em_code is None
        assert result.rationale == ()
        assert result.visit_details_with_codes is None

    def test_the_rationale_leads_with_problems_and_risk(self) -> None:
        result = review(FOLLOW_UP, MODERATE, _content(), None)

        assert [r.element for r in result.rationale] == ["problems", "risk", "data"]
        data = result.rationale[2]
        assert (data.chosen, data.required, data.meets) == ("limited", "moderate", False)

    def test_time_is_offered_only_without_a_psychotherapy_portion(self) -> None:
        without = review(FOLLOW_UP, MODERATE, _content(), None)
        with_therapy = review(FOLLOW_UP, MODERATE, _content(**THERAPY), 20)

        assert without.billing_methods == ("mdm", "time")
        assert with_therapy.billing_methods == ("mdm",)

    def test_the_add_on_follows_the_confirmed_minutes(self) -> None:
        result = review(FOLLOW_UP, MODERATE, _content(**THERAPY), 20)

        assert result.has_psychotherapy
        assert result.add_on == "90833"
        assert result.psychotherapy_minutes == 20

    def test_the_add_on_waits_for_confirmed_minutes(self) -> None:
        result = review(
            FOLLOW_UP, MODERATE, _content("Psychotherapy add-on code: 90836.", **THERAPY), None
        )

        assert not result.add_on_known
        assert result.add_on is None
        assert not result.add_on_disagrees

    def test_a_dictated_add_on_the_minutes_do_not_support_is_flagged_not_changed(self) -> None:
        details = "E/M code: 99214. Psychotherapy add-on code: 90833."
        content = _content(details, **THERAPY)

        result = review(FOLLOW_UP, MODERATE, content, 12)

        assert result.dictated_add_on == "90833"
        assert result.add_on is None
        assert result.add_on_disagrees
        assert not result.em_disagrees
        assert content["encounter"]["visit_details"] == details
        assert (
            result.visit_details_with_codes == "E/M code: 99214. Psychotherapy add-on code: none."
        )

    def test_a_dictated_em_code_that_disagrees_is_flagged(self) -> None:
        result = review(FOLLOW_UP, MODERATE, _content("Billing 99213."), None)

        assert result.dictated_em_code == "99213"
        assert result.em_disagrees
        assert result.visit_details_with_codes == "Billing 99214."

    def test_an_add_on_without_psychotherapy_is_flagged(self) -> None:
        result = review(FOLLOW_UP, MODERATE, _content("Billing 99214 plus 90836."), None)

        assert not result.has_psychotherapy
        assert result.add_on_disagrees

    def test_agreeing_codes_offer_nothing_to_apply(self) -> None:
        details = "E/M code: 99214. Psychotherapy add-on code: 90833."
        result = review(FOLLOW_UP, MODERATE, _content(details, **THERAPY), 20)

        assert not result.em_disagrees
        assert not result.add_on_disagrees
        assert result.visit_details_with_codes is None


class TestWithCodes:
    def test_a_not_stated_code_is_filled_in(self) -> None:
        details = (
            "Date of service: 2026-10-08. E/M code: Not stated. "
            "Psychotherapy add-on code: Not stated."
        )

        assert with_codes(details, "99214", "90833", add_on_known=True) == (
            "Date of service: 2026-10-08. E/M code: 99214. Psychotherapy add-on code: 90833."
        )

    def test_a_code_not_mentioned_gets_its_own_line(self) -> None:
        assert with_codes("Date of service: 2026-10-08.", "99214", None, add_on_known=True) == (
            "Date of service: 2026-10-08.\nE/M code: 99214"
        )

    def test_an_unknown_add_on_is_left_alone(self) -> None:
        details = "Add-on: 90836."
        assert with_codes(details, None, None, add_on_known=False) == details

    def test_dictated_codes_are_read_from_the_visit_details(self) -> None:
        assert dictated_codes("Billing 99214 plus 90836.") == ("99214", "90836")
        assert dictated_codes("Not stated.") == (None, None)


class TestPsychotherapy:
    def test_a_section_with_nothing_written_is_no_psychotherapy(self) -> None:
        assert not has_psychotherapy({"psychotherapy": {"psychotherapy_time": "", "goal": []}})
        assert not has_psychotherapy({"plan": {}})
        assert has_psychotherapy(THERAPY)

    def test_the_confirmed_minutes_are_read_from_the_window(self) -> None:
        assert confirmed_minutes({"confirmed": {"minutes": 20}}) == 20
        assert confirmed_minutes({"proposal": {}}) is None
        assert confirmed_minutes(None) is None


def _note(content: dict[str, Any], **extra: Any) -> Note:
    now = datetime(2026, 10, 8, tzinfo=UTC)
    return Note(
        id=str(uuid.uuid4()),
        patient_id=str(uuid.uuid4()),
        session_id=None,
        note_type="psychiatric_follow_up",
        created_at=now,
        updated_at=now,
        content=content,
        **extra,
    )


@pytest.fixture
def builtins() -> None:
    register_builtin_note_types(get_default_registry())


@pytest.mark.usefixtures("builtins")
class TestRoutes:
    def test_choosing_levels_stores_them_and_returns_the_code(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = mock_notes_repo.add(
            _note(_content(**THERAPY), note_inputs={"place_of_service": "In office"})
        )

        before = client.get(f"/api/notes/{note.id}/mdm")
        assert before.status_code == 200
        assert before.json()["em_code"] is None
        assert [e["element"] for e in before.json()["elements"]] == ["problems", "risk", "data"]

        chosen = client.put(
            f"/api/notes/{note.id}/mdm",
            json={"problems": "moderate", "data": "limited", "risk": "moderate"},
        )
        assert chosen.status_code == 200
        assert chosen.json()["em_code"] == "99214"
        assert chosen.json()["billing_methods"] == ["mdm"]
        saved = mock_notes_repo.get(note.id, "test-user-123")
        assert saved is not None
        assert saved.note_inputs == {
            "place_of_service": "In office",
            "mdm_problems": "moderate",
            "mdm_data": "limited",
            "mdm_risk": "moderate",
        }
        # The draft is untouched: choosing a level drafts nothing again.
        assert saved.content == note.content
        assert saved.content_edited is None

    def test_the_add_on_comes_from_the_confirmed_window(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = mock_notes_repo.add(
            _note(
                _content(**THERAPY),
                note_inputs=MODERATE,
                psychotherapy_window={"confirmed": {"minutes": 20}},
            )
        )

        body = client.get(f"/api/notes/{note.id}/mdm").json()

        assert body["add_on"] == "90833"
        assert body["psychotherapy_minutes"] == 20

    def test_a_level_that_is_not_an_option_is_refused(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = mock_notes_repo.add(_note(_content()))

        response = client.put(f"/api/notes/{note.id}/mdm", json={"problems": "severe"})

        assert response.status_code == 400

    def test_a_signed_note_keeps_its_choices(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = mock_notes_repo.add(
            _note(_content(), finalized_at=datetime(2026, 10, 8, tzinfo=UTC))
        )

        response = client.put(f"/api/notes/{note.id}/mdm", json={"problems": "low"})

        assert response.status_code == 409

    def test_a_type_without_mdm_has_no_review(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = mock_notes_repo.add(
            Note(
                id=str(uuid.uuid4()),
                patient_id=str(uuid.uuid4()),
                note_type="narrative",
                created_at=datetime(2026, 10, 8, tzinfo=UTC),
                updated_at=datetime(2026, 10, 8, tzinfo=UTC),
                content={"note": {"body": "Visit."}},
            )
        )

        assert client.get(f"/api/notes/{note.id}/mdm").status_code == 404

    def test_applying_puts_the_codes_in_the_visit_details_as_an_edit(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        details = "Date of service: 2026-10-08. E/M code: 99213. Psychotherapy add-on code: 90836."
        note = mock_notes_repo.add(
            _note(
                _content(details, **THERAPY),
                note_inputs=MODERATE,
                psychotherapy_window={"confirmed": {"minutes": 20}},
            )
        )

        applied = client.post(f"/api/notes/{note.id}/mdm/apply")

        assert applied.status_code == 200
        assert applied.json()["em_disagrees"] is False
        assert applied.json()["add_on_disagrees"] is False
        saved = mock_notes_repo.get(note.id, "test-user-123")
        assert saved is not None
        assert saved.content_edited is not None
        assert saved.content_edited["encounter"]["visit_details"] == (
            "Date of service: 2026-10-08. E/M code: 99214. Psychotherapy add-on code: 90833."
        )
        assert saved.content_edited["psychotherapy"] == THERAPY["psychotherapy"]
        # The draft itself keeps what was dictated.
        assert saved.content == note.content
        assert client.post(f"/api/notes/{note.id}/mdm/apply").status_code == 409
