# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Homework suggestions are made and kept current in the note's chart-update step.

What this catches, by name:

* a SOAP note (a type that proposes no chart updates) never getting homework
  suggestions, because the step only ran for chart-reading types;
* an edit before signing leaving a stale suggestion: an item reworded, or
  deleted, still offered as it first read;
* a decided suggestion (accepted, edited or discarded) offered again after
  an edit;
* the psychiatric prose read not running beside the draft, running on the
  transcript instead of the plan fields, or a suggestion surviving an edit
  that removed the words it quoted;
* a psychotherapy note being read at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.chart_proposals.families import HOMEWORK
from app.chart_proposals.models import DraftedProposal
from app.chart_proposals.step import ChartProposalStep, refreshes_on_edit
from app.homework.drafting import HomeworkDrafter
from app.homework.proposals import quote_of, refresh_homework, what_changed
from app.models import Note, Patient, Transcript
from app.notes import get_default_registry
from app.notes.chart_context import ChartContext
from app.repositories import InMemoryChartHistoryRepository, InMemoryChartProposalRepository
from app.services.note_generation_service import MockNoteGenerationService

TRANSCRIPT = Transcript(format="txt", content="[00:01] Therapist: How was the week?")


def _note(note_type: str) -> Note:
    now = datetime.now(UTC)
    return Note(
        id=str(uuid.uuid4()),
        patient_id=str(uuid.uuid4()),
        note_type=note_type,
        created_at=now,
        updated_at=now,
        content={},
    )


def _soap(*items: str) -> dict[str, Any]:
    return {"plan": {"homework_assignments": [{"text": t} for t in items]}}


def _pending(repo: InMemoryChartProposalRepository, note: Note) -> dict[str, str]:
    return {
        p.item_key: p.proposed_text
        for p in repo.list_for_note(note.id)
        if p.field_key == HOMEWORK and p.pending
    }


def _step(repo: InMemoryChartProposalRepository, drafter: HomeworkDrafter | None = None) -> Any:
    return ChartProposalStep(repo, InMemoryChartHistoryRepository(), homework_drafter=drafter)


def test_a_soap_note_is_suggested_its_homework_when_stored_and_after_an_edit() -> None:
    soap = get_default_registry().get("soap")
    assert refreshes_on_edit(soap), "a saved edit to a SOAP note recomputes its homework"
    repo = InMemoryChartProposalRepository()
    note = _note("soap")
    step = _step(repo)

    step.store(note, soap, None, None, _soap("Client will keep a worry log.", "Walk after dinner."))
    assert _pending(repo, note) == {
        "plan.homework_assignments#0": "Keep a worry log.",
        "plan.homework_assignments#1": "Walk after dinner.",
    }
    (first,) = [p for p in repo.list_for_note(note.id) if p.item_key.endswith("#0")]
    assert quote_of(first.what_changed) == "Client will keep a worry log."

    patient = Patient(
        id=note.patient_id,
        first_name="Pat",
        last_name="Anonymous",
        created_at=note.created_at,
        updated_at=note.created_at,
    )
    step.edited(note, soap, patient, _soap("Client will keep a worry log each evening."))
    assert _pending(repo, note) == {"plan.homework_assignments#0": "Keep a worry log each evening."}


def test_an_edited_soap_note_is_read_from_its_narrative_plan() -> None:
    """The editor saves a SOAP note one string per section; dropping its homework
    there would empty the review the moment the clinician touched the note."""
    soap = get_default_registry().get("soap")
    repo = InMemoryChartProposalRepository()
    note = _note("soap")
    refresh_homework(repo, note, soap, _soap("Client will keep a worry log.", "Walk after dinner."))
    narrative = {
        "plan": (
            "**Interventions Used:**\n- Cognitive restructuring.\n\n"
            "**Homework Assignments:**\n- Client will keep a worry log.\n- Walk after\n  dinner.\n"
            "- Call a friend this weekend.\n\n**Next Session:** Two weeks."
        )
    }
    refresh_homework(repo, note, soap, narrative)
    assert _pending(repo, note) == {
        "plan.homework_assignments#0": "Keep a worry log.",
        "plan.homework_assignments#1": "Walk after dinner.",
        "plan.homework_assignments#2": "Call a friend this weekend.",
    }


def test_a_dap_note_saved_as_section_strings_is_read_by_its_own_label() -> None:
    """Not a SOAP special case: any type's section string is read under the type's
    own field label."""
    dap = get_default_registry().get("dap")
    repo = InMemoryChartProposalRepository()
    note = _note("dap")
    saved = {
        "plan": (
            "**Interventions Used:**\n- Exposure planning.\n\n"
            "**Homework Assignments:**\n- Client will ride the elevator twice.\n\n"
            "**Next Session:** One week."
        )
    }
    refresh_homework(repo, note, dap, saved)
    assert _pending(repo, note) == {"plan.homework_assignments#0": "Ride the elevator twice."}


def test_psychiatric_prose_saved_as_a_section_string_is_read_and_kept_while_quoted() -> None:
    follow_up = get_default_registry().get("psychiatric_follow_up")
    repo = InMemoryChartProposalRepository()
    note = _note("psychiatric_follow_up")
    saved = {
        "plan": "**Medications:**\n- Increase sertraline to 100 mg.",
        "psychotherapy": (
            "**Goal and plan:** Continue CBT; client will log panic episodes daily.\n\n"
            "**Therapy plan and cadence:** Weekly."
        ),
    }
    drafter = _Drafter([{"text": "Log panic episodes daily", "quote": "log panic episodes daily"}])
    drafted = _step(repo, drafter).draft(_Generator(), follow_up, ChartContext(), TRANSCRIPT, saved)
    assert drafted is not None
    (prompt,) = drafter.prompts
    assert "log panic episodes daily" in prompt
    assert "sertraline" not in prompt
    refresh_homework(repo, note, follow_up, saved, drafted.homework)
    assert list(_pending(repo, note)) == ["psychotherapy.goal_plan#0"]

    edited = {**saved, "psychotherapy": "**Goal and plan:** Continue CBT."}
    refresh_homework(repo, note, follow_up, edited)
    assert _pending(repo, note) == {}


def test_a_decided_suggestion_is_never_offered_again() -> None:
    soap = get_default_registry().get("soap")
    repo = InMemoryChartProposalRepository()
    note = _note("soap")
    refresh_homework(repo, note, soap, _soap("Read chapter 2."))
    (proposal,) = repo.list_for_note(note.id)
    repo.decide(proposal.id, "discarded", None, "u1", datetime.now(UTC))

    refresh_homework(repo, note, soap, _soap("Read chapter 2 and 3."))
    assert _pending(repo, note) == {}
    assert len(repo.list_for_note(note.id)) == 1


class _Drafter(HomeworkDrafter):
    def __init__(self, answer: list[dict[str, str]]) -> None:
        super().__init__(complete=self._complete)
        self.answer = answer
        self.prompts: list[str] = []

    def _complete(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(user)
        return {"assignments": self.answer}


class _Generator(MockNoteGenerationService):
    def chart_proposal_completion(self) -> Any:
        return lambda _system, _user, _schema: {"proposals": []}


_PSYCH = {
    "plan": {"medication_plan": ["Increase sertraline to 100 mg."]},
    "psychotherapy": {"goal_plan": "Continue CBT; client will log panic episodes daily."},
}


def test_the_prose_plan_is_read_beside_the_draft_and_kept_only_while_quoted() -> None:
    follow_up = get_default_registry().get("psychiatric_follow_up")
    repo = InMemoryChartProposalRepository()
    note = _note("psychiatric_follow_up")
    drafter = _Drafter([{"text": "Log panic episodes daily", "quote": "log panic episodes daily"}])
    step = _step(repo, drafter)

    drafted = step.draft(_Generator(), follow_up, ChartContext(), TRANSCRIPT, _PSYCH)
    assert drafted is not None
    step.store(note, follow_up, ChartContext(), drafted, _PSYCH)
    assert _pending(repo, note) == {"psychotherapy.goal_plan#0": "Log panic episodes daily"}
    (prompt,) = drafter.prompts
    assert "log panic episodes daily" in prompt
    assert "sertraline" not in prompt, "only the homework fields are read"
    assert "How was the week" not in prompt, "never the transcript"

    # An edit that keeps the words keeps the suggestion; one that drops them drops it.
    kept = {**_PSYCH, "psychotherapy": {"goal_plan": "CBT. Client will log panic episodes daily!"}}
    refresh_homework(repo, note, follow_up, kept)
    assert list(_pending(repo, note)) == ["psychotherapy.goal_plan#0"]
    gone = {**_PSYCH, "psychotherapy": {"goal_plan": "Continue CBT."}}
    refresh_homework(repo, note, follow_up, gone)
    assert _pending(repo, note) == {}


def _prose(text: str, quote: str) -> DraftedProposal:
    return DraftedProposal(
        field_key=HOMEWORK,
        item_key="psychotherapy.goal_plan#0",
        proposed_text=text,
        what_changed=what_changed(quote),
        evidence=(),
        origin="homework",
    )


def test_a_redraft_replaces_the_prose_suggestions_still_pending() -> None:
    follow_up = get_default_registry().get("psychiatric_follow_up")
    repo = InMemoryChartProposalRepository()
    note = _note("psychiatric_follow_up")
    first = _prose("Log panic episodes daily", "log panic episodes daily")
    refresh_homework(repo, note, follow_up, _PSYCH, (first,))
    second = _prose("Write down each panic episode", "log panic episodes daily")
    refresh_homework(repo, note, follow_up, _PSYCH, (second,))
    assert _pending(repo, note) == {"psychotherapy.goal_plan#0": "Write down each panic episode"}


def test_a_psychotherapy_note_is_never_read() -> None:
    psychotherapy = get_default_registry().get("psychotherapy")
    assert not refreshes_on_edit(psychotherapy)
    repo = InMemoryChartProposalRepository()
    note = _note("psychotherapy")
    drafter = _Drafter([{"text": "Anything", "quote": "anything"}])
    _step(repo, drafter).store(note, psychotherapy, None, None, _soap("Keep a log."))
    assert repo.list_for_note(note.id) == []
    assert drafter.prompts == []
