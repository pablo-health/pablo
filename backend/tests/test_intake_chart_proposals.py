# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What an accepted intake form proposes for the chart, and how it is decided.

The failures these guard against are the ones a clinician would not notice
until the chart was wrong: an answer written to the chart with nobody
deciding it, a written answer sent to the proposal call without the client's
agreement, a medication guessed from a line that names two, a re-run of the
same form stacking a second copy of every proposal, and a discarded update
coming back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.chart_history.service import ChartHistoryService
from app.chart_proposals.families import ChartWriters
from app.chart_proposals.intake import (
    form_answers,
    mapped_proposals,
    medication_line,
)
from app.chart_proposals.intake_step import AcceptedForm, IntakeProposalStep
from app.chart_proposals.models import ANSWERED_ON_INTAKE
from app.chart_proposals.service import (
    ChartProposalService,
    Choice,
    ProposalDecidedError,
)
from app.chart_proposals.step import ChartProposalStep
from app.intake.items import ItemConfigError, ItemDraft, validate_item_list
from app.intake.starters import CLIENT_DETAILS
from app.medications.repository import InMemoryMedicationRepository
from app.medications.service import MedicationService
from app.models import Patient
from app.notes.chart_context import ChartContext, ChartHistoryField, ChartMedication
from app.repositories import (
    InMemoryChartHistoryRepository,
    InMemoryChartProposalRepository,
    InMemoryPatientRepository,
)

_USER = "clinician-1"
_FORM = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_TODAY = datetime(2026, 10, 17, tzinfo=UTC).date()


def _item(
    position: int, key: str, item_type: str, label: str | None, config: dict[str, Any] | None = None
) -> dict[str, object]:
    return {
        "id": f"item-{key}",
        "key": key,
        "position": position,
        "item_type": item_type,
        "label": label,
        "config": config or {},
    }


#: The questions of the built-in "About you" starter, as a published form's rows,
#: after a "what brings you in" question and one more written answer.
_ITEMS = [
    _item(0, "reason", "reason", None),
    *(
        _item(i + 1, q.key, q.item_type, q.label, dict(q.config))
        for i, q in enumerate(CLIENT_DETAILS.questions)
    ),
    _item(6, "goals", "free_text", "What would you like to be different?"),
]

_ANSWERS: dict[str, dict[str, object]] = {
    "item-reason": {"text": "Panic attacks before every shift since the spring."},
    "item-preferred_name": {"text": "Sam"},
    "item-pronouns": {"text": "they/them"},
    "item-emergency_contact": {"name": "Jordan Lee", "relationship": "sister", "phone": "555-0100"},
    "item-current_medications": {
        "text": "Sertraline 50 mg daily\n- Trazodone 25mg at bedtime\nsome vitamins"
    },
    "item-prior_treatment": {"text": "Saw a counselor in college for a year. It helped."},
    "item-goals": {"text": "Sleep through the night."},
}


def _mapped(chart: ChartContext | None = None) -> Any:
    return mapped_proposals(form_answers(_ITEMS, _ANSWERS), chart or ChartContext())


class TestMapping:
    def test_each_answer_the_form_places_is_proposed_to_its_field(self) -> None:
        proposals = {(p.field_key, p.item_key): p for p in _mapped().proposals}
        assert {key: p.proposed_text for key, p in proposals.items()} == {
            ("preferred_name", ""): "Sam",
            ("pronouns", ""): "they/them",
            ("emergency_contact", ""): "Jordan Lee (sister), 555-0100",
            ("medications", "sertraline"): "sertraline 50 mg, daily",
            ("medications", "trazodone"): "trazodone 25mg, at bedtime",
            ("psychotherapy_history", ""): "Saw a counselor in college for a year. It helped.",
        }
        assert all(p.origin == "intake" for p in proposals.values())

    def test_each_proposal_points_at_its_question_and_answer(self) -> None:
        contact = next(p for p in _mapped().proposals if p.field_key == "emergency_contact")
        (evidence,) = contact.evidence
        assert evidence.item_id == "item-emergency_contact"
        assert evidence.question == "Who should we contact in an emergency?"
        assert evidence.segment_id == 3
        assert evidence.text == "Jordan Lee (sister), 555-0100"
        assert contact.what_changed == ANSWERED_ON_INTAKE

    def test_a_medication_is_one_the_client_takes_with_the_dose_as_written(self) -> None:
        sertraline = next(p for p in _mapped().proposals if p.item_key == "sertraline")
        assert sertraline.change is not None
        assert sertraline.change.action == "add"
        assert sertraline.change.dose == "50 mg"
        assert sertraline.change.frequency == "daily"
        assert sertraline.evidence[0].text == "Sertraline 50 mg daily"

    def test_unplaced_written_answers_are_left_for_the_call_and_never_mapped(self) -> None:
        mapped = _mapped()
        # The reason and the goals answer, and the medications answer whole,
        # because one of its lines named no medication.
        assert [a.item_id for a in mapped.to_read] == [
            "item-reason",
            "item-current_medications",
            "item-goals",
        ]
        assert not any(p.evidence[0].item_id == "item-reason" for p in mapped.proposals)

    def test_a_history_answer_is_kept_after_what_the_field_says(self) -> None:
        chart = ChartContext(
            history=(ChartHistoryField("psychotherapy_history", "CBT in 2019.", _TODAY),)
        )
        (history,) = [p for p in _mapped(chart).proposals if p.field_key == "psychotherapy_history"]
        assert history.proposed_text == (
            "CBT in 2019.\nSaw a counselor in college for a year. It helped."
        )

    def test_what_the_chart_already_says_is_not_proposed(self) -> None:
        chart = ChartContext(
            profile=(("pronouns", "they/them"),),
            medications=(ChartMedication(name="sertraline", dose="50 mg", frequency="daily"),),
        )
        keys = {(p.field_key, p.item_key) for p in _mapped(chart).proposals}
        assert ("pronouns", "") not in keys
        assert ("medications", "sertraline") not in keys
        assert ("medications", "trazodone") in keys

    def test_an_unanswered_optional_question_proposes_nothing(self) -> None:
        answers = {k: v for k, v in _ANSWERS.items() if k != "item-pronouns"}
        mapped = mapped_proposals(form_answers(_ITEMS, answers), ChartContext())
        assert "pronouns" not in {p.field_key for p in mapped.proposals}


class TestMedicationLines:
    @pytest.mark.parametrize(
        ("line", "name", "dose", "frequency"),
        [
            ("Lexapro 10mg every morning", "Lexapro", "10mg", "every morning"),
            ("1. bupropion XL 150 mg once a day", "bupropion", "150 mg", "XL 150 mg once a day"),
            ("I take lithium 300 mg twice a day", "lithium", "300 mg", None),
            ("Sertraline", "Sertraline", None, None),
        ],
    )
    def test_a_line_that_names_one_medication_is_read(
        self, line: str, name: str, dose: str | None, frequency: str | None
    ) -> None:
        change = medication_line(line)
        assert change is not None
        assert (change.drug_name.lower(), change.dose) == (name.lower(), dose)
        if frequency is not None and name != "bupropion":
            assert change.frequency == frequency

    @pytest.mark.parametrize(
        "line",
        ["sertraline and trazodone, I forget the doses", "a multivitamin", "none", "N/A"],
    )
    def test_a_line_that_names_none_or_two_is_not_guessed(self, line: str) -> None:
        assert medication_line(line) is None


class TestQuestionConfig:
    def test_a_question_may_name_a_chart_field(self) -> None:
        validate_item_list(list(CLIENT_DETAILS.questions))

    def test_a_field_the_chart_does_not_have_is_refused(self) -> None:
        item = ItemDraft(
            key="shoe", item_type="free_text", label="Shoe size?", config={"chart_field": "shoe"}
        )
        with pytest.raises(ItemConfigError, match="not a chart field"):
            validate_item_list([item])

    def test_two_questions_filling_one_field_are_refused(self) -> None:
        items = [
            ItemDraft(
                key=key,
                item_type="free_text",
                label="Pronouns?",
                config={"chart_field": "pronouns"},
            )
            for key in ("pronouns", "pronouns_again")
        ]
        with pytest.raises(ItemConfigError, match="One question per chart field"):
            validate_item_list(items)


# ---------------------------------------------------------------------------
# Storing, superseding and deciding
# ---------------------------------------------------------------------------


@pytest.fixture
def patients() -> InMemoryPatientRepository:
    repo = InMemoryPatientRepository()
    now = datetime.now(UTC)
    repo.create(
        Patient(id="p-1", first_name="Samantha", last_name="Ortiz", created_at=now, updated_at=now),
        _USER,
    )
    return repo


@pytest.fixture
def repo() -> InMemoryChartProposalRepository:
    return InMemoryChartProposalRepository()


@pytest.fixture
def history() -> InMemoryChartHistoryRepository:
    return InMemoryChartHistoryRepository()


@pytest.fixture
def medications() -> InMemoryMedicationRepository:
    meds = InMemoryMedicationRepository()
    meds.grant_all_access()
    return meds


def _service(
    repo: InMemoryChartProposalRepository,
    history: InMemoryChartHistoryRepository,
    patients: InMemoryPatientRepository,
    medications: InMemoryMedicationRepository,
) -> ChartProposalService:
    return ChartProposalService(
        repo,
        ChartWriters(ChartHistoryService(history), patients, MedicationService(medications)),
    )


class _Never:
    """A model that must not be asked."""

    def chart_proposal_completion(self) -> Any:
        def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("the written answers were read without consent")

        return complete


class _Reads:
    """A model that proposes from the reason answer, and records what it was given."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def chart_proposal_completion(self) -> Any:
        def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.prompts.append(user)
            return {
                "proposals": [
                    {
                        "field_key": "work_school",
                        "proposed_text": "Works shifts.",
                        "what_changed": "Works shifts",
                        "evidence_segment_ids": [0],
                    }
                ]
            }

        return complete


class _Fails:
    def chart_proposal_completion(self) -> Any:
        def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            raise TimeoutError("deadline exceeded")

        return complete


def _propose(
    repo: InMemoryChartProposalRepository,
    history: InMemoryChartHistoryRepository,
    patients: InMemoryPatientRepository,
    medications: InMemoryMedicationRepository,
    generator: Any,
    *,
    consented: bool,
    answers: dict[str, dict[str, object]] | None = None,
) -> Any:
    patient = patients.get("p-1", _USER)
    assert patient is not None
    form = AcceptedForm(
        assignment_id=_FORM,
        patient=patient,
        items=_ITEMS,
        answers=answers or _ANSWERS,
        ai_consented=consented,
    )
    step = IntakeProposalStep(repo, ChartProposalStep(repo, history, medications), generator)
    return step.propose(form, _USER)


class TestAcceptedForm:
    def test_without_consent_the_written_answers_are_not_read(
        self,
        repo: InMemoryChartProposalRepository,
        history: InMemoryChartHistoryRepository,
        patients: InMemoryPatientRepository,
        medications: InMemoryMedicationRepository,
    ) -> None:
        outcome = _propose(repo, history, patients, medications, _Never(), consented=False)
        assert outcome.read == "no_consent"
        assert outcome.proposed == 6
        assert "work_school" not in {p.field_key for p in repo.list_for_intake(_FORM)}

    def test_with_consent_they_are_read_and_point_at_the_answer(
        self,
        repo: InMemoryChartProposalRepository,
        history: InMemoryChartHistoryRepository,
        patients: InMemoryPatientRepository,
        medications: InMemoryMedicationRepository,
    ) -> None:
        model = _Reads()
        outcome = _propose(repo, history, patients, medications, model, consented=True)
        assert outcome.read == "consent"
        (prompt,) = model.prompts
        # Only what the form did not place is sent, each under its question.
        assert "[S0] What brings you in" in prompt
        assert "Sleep through the night." in prompt
        assert "Jordan Lee" not in prompt
        assert "What pronouns do you use?" not in prompt
        work = next(p for p in repo.list_for_intake(_FORM) if p.field_key == "work_school")
        (evidence,) = work.evidence
        assert evidence.item_id == "item-reason"
        assert evidence.text == "Panic attacks before every shift since the spring."

    def test_a_failed_call_keeps_the_mapped_proposals(
        self,
        repo: InMemoryChartProposalRepository,
        history: InMemoryChartHistoryRepository,
        patients: InMemoryPatientRepository,
        medications: InMemoryMedicationRepository,
    ) -> None:
        outcome = _propose(repo, history, patients, medications, _Fails(), consented=True)
        assert outcome.read == "failed"
        assert outcome.proposed == 6

    def test_nothing_reaches_the_chart_until_it_is_decided(
        self,
        repo: InMemoryChartProposalRepository,
        history: InMemoryChartHistoryRepository,
        patients: InMemoryPatientRepository,
        medications: InMemoryMedicationRepository,
    ) -> None:
        _propose(repo, history, patients, medications, _Never(), consented=False)
        patient = patients.get("p-1", _USER)
        assert patient is not None
        assert (patient.preferred_name, patient.pronouns, patient.emergency_contact) == (
            None,
            None,
            None,
        )
        assert history.entries("p-1") == []
        assert medications.list_by_patient("p-1", _USER) == []

    def test_proposing_again_replaces_what_is_pending_and_never_reoffers_a_decision(
        self,
        repo: InMemoryChartProposalRepository,
        history: InMemoryChartHistoryRepository,
        patients: InMemoryPatientRepository,
        medications: InMemoryMedicationRepository,
    ) -> None:
        _propose(repo, history, patients, medications, _Never(), consented=False)
        service = _service(repo, history, patients, medications)
        patient = patients.get("p-1", _USER)
        assert patient is not None
        pronouns = next(p for p in repo.list_for_intake(_FORM) if p.field_key == "pronouns")
        service.decide_for_intake(_FORM, patient, pronouns.id, Choice("discard"), _USER)

        corrected = {**_ANSWERS, "item-preferred_name": {"text": "Sammy"}}
        _propose(repo, history, patients, medications, _Never(), consented=False, answers=corrected)
        listed = repo.list_for_intake(_FORM)
        assert len([p for p in listed if p.field_key == "pronouns"]) == 1
        assert next(p for p in listed if p.field_key == "pronouns").decision == "discarded"
        (name,) = [p for p in listed if p.field_key == "preferred_name"]
        assert (name.proposed_text, name.decision) == ("Sammy", "pending")
        assert len(listed) == 6

    def test_accepting_and_editing_write_the_chart(
        self,
        repo: InMemoryChartProposalRepository,
        history: InMemoryChartHistoryRepository,
        patients: InMemoryPatientRepository,
        medications: InMemoryMedicationRepository,
    ) -> None:
        _propose(repo, history, patients, medications, _Never(), consented=False)
        service = _service(repo, history, patients, medications)
        patient = patients.get("p-1", _USER)
        assert patient is not None
        by_field = {(p.field_key, p.item_key): p for p in repo.list_for_intake(_FORM)}

        service.decide_for_intake(
            _FORM, patient, by_field[("emergency_contact", "")].id, Choice("accept"), _USER
        )
        service.decide_for_intake(
            _FORM, patient, by_field[("pronouns", "")].id, Choice("edit", "they/he"), _USER
        )
        service.decide_for_intake(
            _FORM, patient, by_field[("psychotherapy_history", "")].id, Choice("accept"), _USER
        )
        service.decide_for_intake(
            _FORM, patient, by_field[("medications", "sertraline")].id, Choice("accept"), _USER
        )

        saved = patients.get("p-1", _USER)
        assert saved is not None
        assert saved.emergency_contact == "Jordan Lee (sister), 555-0100"
        assert saved.pronouns == "they/he"
        (entry,) = history.entries("p-1")
        assert entry.field_key == "psychotherapy_history"
        assert entry.source_note_id is None
        (row,) = medications.list_by_patient("p-1", _USER)
        assert (row["drug_name"], row["dose"], row["frequency"]) == (
            "sertraline",
            "50 mg",
            "daily",
        )
        with pytest.raises(ProposalDecidedError):
            service.decide_for_intake(
                _FORM, patient, by_field[("pronouns", "")].id, Choice("accept"), _USER
            )
