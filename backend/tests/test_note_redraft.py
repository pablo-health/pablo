# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drafting a session's note again, with new inputs or more from the clinician.

One service serves both reasons. These tests pin what it promises: inputs
are validated like the appointment's, a signed note is never redrafted,
edits are never dropped without the clinician saying so, and a redraft that
fails leaves the note as it was.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from app.api_errors import BadRequestError
from app.main import app
from app.models import (
    Patient,
    SessionStatus,
    TherapySession,
    Transcript,
)
from app.models.enums import SessionSource
from app.models.notes import RedraftEdits
from app.models.soap_note import SOAPNote
from app.notes import NoteTypeDefinition, NoteTypeRegistry, register_builtin_note_types
from app.notes.client_present import DICTATED_HEADING
from app.notes.registry import NoteFieldDef, NoteInputDef, NoteSectionDef
from app.repositories import (
    InMemoryNotesRepository,
    InMemoryPatientRepository,
    InMemoryTherapySessionRepository,
)
from app.routes import note_redraft as note_redraft_routes
from app.routes.note_redraft import RedraftNoteJob
from app.routes.notes import get_note_generation_service
from app.services import note_redraft
from app.services.note_generation_service import (
    GeneratedNote,
    NoteGenerationService,
    RegistryNoteGenerationService,
    TransientNoteGenerationError,
)
from app.services.note_redraft import (
    NoteHasEditsError,
    NoteNotRedraftableError,
    NoteRedraftInProgressError,
    NoteRedraftService,
    RedraftNotPendingError,
    as_shown,
    merge_kept_edits,
)
from app.services.note_service import NoteService
from app.services.note_signing import NoteLockedError
from app.services.session_service import (
    SOAPGenerationFailedError,
    TransientSOAPGenerationError,
)
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion
from fastapi import HTTPException

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from app.notes.chart_context import ChartContext
    from fastapi.testclient import TestClient

# The client fixture's user, so service and route tests share one owner.
USER = "test-user-123"
VISIT = NoteTypeDefinition(
    key="visit_with_code",
    label="Visit",
    description="A visit note with a code supplied by the clinician.",
    sections=(
        NoteSectionDef(
            key="body",
            label="Body",
            fields=(
                NoteFieldDef(key="summary", label="Summary", kind="text"),
                NoteFieldDef(key="plan", label="Plan", kind="text"),
                NoteFieldDef(key="code_note", label="Code note", kind="text"),
            ),
        ),
    ),
    inputs=(
        NoteInputDef(
            key="visit_code",
            label="Visit code",
            kind="choice",
            options=("99213", "99214"),
            required=True,
        ),
    ),
)


class RecordingGenerator(NoteGenerationService):
    """Answers with the next queued content and records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.current_notes: list[Mapping[str, Any] | None] = []
        self.next_content: dict[str, Any] = {}
        self.error: Exception | None = None

    def generate_note(
        self,
        note_type: str,
        transcript: Transcript,
        patient: Patient,
        session_date: datetime,
        inputs: Mapping[str, str] | None = None,
        definition: NoteTypeDefinition | None = None,
        client_present_end_seconds: float | None = None,
        chart: ChartContext | None = None,
        current_note: Mapping[str, Any] | None = None,
    ) -> GeneratedNote:
        self.current_notes.append(current_note)
        self.calls.append(
            {
                "note_type": note_type,
                "transcript": transcript.content,
                "inputs": dict(inputs or {}),
                "client_present_end_seconds": client_present_end_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        return GeneratedNote(note_type=note_type, content=self.next_content)


@pytest.fixture(autouse=True)
def registry(monkeypatch: pytest.MonkeyPatch) -> NoteTypeRegistry:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    registry.register(VISIT)
    monkeypatch.setattr(note_redraft, "get_default_registry", lambda: registry)
    return registry


@pytest.fixture
def session_repo() -> InMemoryTherapySessionRepository:
    return InMemoryTherapySessionRepository()


@pytest.fixture
def patient_repo(session_repo: InMemoryTherapySessionRepository) -> InMemoryPatientRepository:
    return InMemoryPatientRepository(session_repo=session_repo)


@pytest.fixture
def notes_repo() -> InMemoryNotesRepository:
    repo = InMemoryNotesRepository()
    repo.grant_all_access()
    return repo


@pytest.fixture
def generator() -> RecordingGenerator:
    return RecordingGenerator()


@pytest.fixture
def service(
    session_repo: InMemoryTherapySessionRepository,
    patient_repo: InMemoryPatientRepository,
    notes_repo: InMemoryNotesRepository,
    generator: RecordingGenerator,
) -> NoteRedraftService:
    return NoteRedraftService(session_repo, patient_repo, NoteService(notes_repo), generator)


def _visit(summary: str, plan: str = "", code_note: str = "") -> dict[str, Any]:
    return {"body": {"summary": summary, "plan": plan, "code_note": code_note}}


def _drafted_session(
    session_repo: InMemoryTherapySessionRepository,
    patient_repo: InMemoryPatientRepository,
    notes_repo: InMemoryNotesRepository,
    *,
    note_type: str = VISIT.key,
    content: dict[str, Any] | None = None,
    note_inputs: dict[str, str] | None = None,
    status: SessionStatus = SessionStatus.PENDING_REVIEW,
    source: SessionSource = SessionSource.COMPANION,
) -> tuple[TherapySession, str]:
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()),
        first_name="Test",
        last_name="Client",
        created_at=now,
        updated_at=now,
    )
    patient_repo.create(patient, USER)
    session = session_repo.create(
        TherapySession(
            id=str(uuid.uuid4()),
            user_id=USER,
            patient_id=patient.id,
            session_date=now,
            session_number=1,
            status=status,
            source=source,
            transcript=Transcript(format="txt", content="[00:01] Therapist: Hello"),
            created_at=now,
        )
    )
    note = NoteService(notes_repo).create_or_update_for_session(
        session_id=session.id,
        patient_id=patient.id,
        note_type=note_type,
        content=content if content is not None else _visit("First draft."),
        user_id=USER,
        note_inputs=note_inputs,
    )
    return session, note.id


@pytest.fixture
def drafted(
    session_repo: InMemoryTherapySessionRepository,
    patient_repo: InMemoryPatientRepository,
    notes_repo: InMemoryNotesRepository,
) -> tuple[TherapySession, str]:
    return _drafted_session(
        session_repo, patient_repo, notes_repo, note_inputs={"visit_code": "99213"}
    )


# --- Keeping the clinician's edits ---


class TestMergeKeptEdits:
    def test_a_changed_field_keeps_the_clinicians_text(self) -> None:
        kept = merge_kept_edits(
            VISIT.key,
            _visit("Drafted summary.", "Drafted plan."),
            _visit("My summary.", "Drafted plan."),
            _visit("New summary.", "New plan."),
        )
        assert kept == _visit("My summary.", "New plan.")

    def test_a_field_left_empty_takes_the_new_draft(self) -> None:
        kept = merge_kept_edits(
            VISIT.key,
            _visit("Drafted.", "Drafted plan."),
            _visit("Mine.", "   "),
            _visit("New.", "New plan.", "From the dictation."),
        )
        assert kept == _visit("Mine.", "New plan.", "From the dictation.")

    def test_nothing_kept_means_no_edits(self) -> None:
        assert (
            merge_kept_edits(
                VISIT.key, _visit("Drafted."), _visit("Drafted."), _visit("New draft.")
            )
            is None
        )

    def test_no_edits_means_no_edits(self) -> None:
        assert merge_kept_edits(VISIT.key, _visit("a"), None, _visit("b")) is None

    def test_soap_is_compared_in_the_narrative_the_clinician_edits(self) -> None:
        def soap(complaint: str, plan_next: str) -> dict[str, Any]:
            return SOAPNote.from_dict(
                {
                    "subjective": {"chief_complaint": complaint},
                    "plan": {"next_session": plan_next},
                }
            ).to_dict()

        previous = soap("Low mood.", "One week.")
        redrafted = soap("Low mood.", "Two weeks.")
        edited = SOAPNote.from_dict(previous).to_narrative()
        edited["subjective"] = "**Chief Complaint:** Low mood, worse on Mondays."

        kept = merge_kept_edits("soap", previous, edited, redrafted)

        assert kept is not None
        assert kept["subjective"] == "**Chief Complaint:** Low mood, worse on Mondays."
        assert kept["plan"] == SOAPNote.from_dict(redrafted).to_narrative()["plan"]

    def test_an_edit_to_one_soap_field_keeps_the_redraft_of_the_others(self) -> None:
        def soap(next_steps: str, next_session: str = "") -> dict[str, Any]:
            return SOAPNote.from_dict(
                {"plan": {"next_steps": [next_steps], "next_session": next_session}}
            ).to_dict()

        previous = soap("Practice breathing.")
        # The clinician dictated the next session; the redraft put it in its field.
        redrafted = soap("Practice breathing daily.", "Two weeks from today.")
        edited = SOAPNote.from_dict(previous).to_narrative()
        edited["plan"] = "**Next Steps:**\n- Walk every morning."

        kept = merge_kept_edits("soap", previous, edited, redrafted)

        assert kept is not None
        assert kept["plan"] == (
            "**Next Steps:**\n- Walk every morning.\n\n**Next Session:** Two weeks from today."
        )

    def test_a_soap_field_the_clinician_left_alone_takes_the_redraft(self) -> None:
        def soap(complaint: str, mood: str) -> dict[str, Any]:
            return SOAPNote.from_dict(
                {"subjective": {"chief_complaint": complaint, "mood_affect": mood}}
            ).to_dict()

        previous = soap("Low mood.", "Flat.")
        redrafted = soap("Low mood since the move.", "Flat, tearful.")
        edited = SOAPNote.from_dict(previous).to_narrative()
        edited["subjective"] = edited["subjective"].replace("Flat.", "Flat, brightened at the end.")

        kept = merge_kept_edits("soap", previous, edited, redrafted)

        assert kept is not None
        assert kept["subjective"] == (
            "**Chief Complaint:** Low mood since the move.\n\n"
            "**Mood/Affect:** Flat, brightened at the end."
        )

    def test_a_soap_field_the_clinician_removed_stays_removed(self) -> None:
        def soap(complaint: str, mood: str) -> dict[str, Any]:
            return SOAPNote.from_dict(
                {"subjective": {"chief_complaint": complaint, "mood_affect": mood}}
            ).to_dict()

        previous = soap("Low mood.", "Flat.")
        edited = SOAPNote.from_dict(previous).to_narrative()
        edited["subjective"] = "**Chief Complaint:** Low mood, worse on Mondays."

        kept = merge_kept_edits("soap", previous, edited, soap("Low mood.", "Tearful."))

        assert kept is not None
        assert kept["subjective"] == "**Chief Complaint:** Low mood, worse on Mondays."

    def test_a_soap_section_rewritten_as_free_text_is_kept_whole(self) -> None:
        previous = SOAPNote.from_dict({"plan": {"next_session": "One week."}}).to_dict()
        redrafted = SOAPNote.from_dict({"plan": {"next_session": "Two weeks."}}).to_dict()
        edited = SOAPNote.from_dict(previous).to_narrative()
        edited["plan"] = "See again in a week; call if worse."

        kept = merge_kept_edits("soap", previous, edited, redrafted)

        assert kept is not None
        assert kept["plan"] == "See again in a week; call if worse."


class TestAsShown:
    def test_the_draft_with_the_clinicians_edits_over_it(self) -> None:
        shown = as_shown(VISIT.key, _visit("Drafted.", "Drafted plan."), _visit("Mine."))
        assert shown == _visit("Mine.")

    def test_a_partial_edit_leaves_the_rest_of_the_draft(self) -> None:
        drafted = _visit("Drafted.", "Drafted plan.")
        shown = as_shown(VISIT.key, drafted, {"body": {"plan": "My plan."}})
        assert shown == _visit("Drafted.", "My plan.")

    def test_soap_is_shown_as_the_narrative_the_clinician_edits(self) -> None:
        drafted = SOAPNote.from_dict({"plan": {"next_session": "One week."}}).to_dict()
        shown = as_shown("soap", drafted, {"plan": "**Next Session:** Two weeks."})
        assert shown["plan"] == "**Next Session:** Two weeks."
        assert set(shown) == {"subjective", "objective", "assessment", "plan"}


# A psychiatric follow-up as the model drafted it, and the line dictated
# afterwards (synthetic visit, captured from the drafting pipeline).
_CAPTURED = json.loads(
    (Path(__file__).parent / "fixtures" / "notes" / "psychiatric_follow_up_drafts.json").read_text()
)


class TestRedraftPrompt:
    def _prompt(self, current_note: dict[str, Any] | None) -> str:
        gateway = FakeStructuredLLMGateway(
            default_response=StructuredCompletion(data=_CAPTURED["redrafted"])
        )
        now = datetime.now(UTC)
        patient = Patient(id="p1", first_name="A", last_name="B", created_at=now, updated_at=now)
        dictated = "\n\n".join(
            ["Therapist: Plan, continue.", DICTATED_HEADING, *_CAPTURED["dictated"]]
        )
        RegistryNoteGenerationService(llm_gateway=gateway, model="test").generate_note(
            VISIT.key,
            Transcript(format="txt", content=dictated),
            patient,
            now,
            inputs={"visit_code": "99213"},
            definition=VISIT,
            current_note=current_note,
        )
        prompt: str = gateway.calls[0]["user_prompt"]
        return prompt

    def test_the_note_the_clinician_has_goes_to_the_model_after_the_dictation(self) -> None:
        prompt = self._prompt(_CAPTURED["drafted"])

        dictation, current = prompt.split("Current note:", 1)
        assert "send me a home. Blood pressure reading in two weeks" in dictation
        assert "never drop one" in current
        assert "Home blood pressure 124/78." in current
        assert "30 day supply sent to the usual pharmacy." in current

    def test_a_first_draft_has_no_current_note(self) -> None:
        assert "Current note:" not in self._prompt(None)


# --- Starting a redraft ---


class TestStart:
    def test_new_inputs_are_stored_and_the_note_is_marked_redrafting(
        self,
        service: NoteRedraftService,
        notes_repo: InMemoryNotesRepository,
        drafted: tuple[TherapySession, str],
    ) -> None:
        session, note_id = drafted

        note, keep = service.start(session.id, USER, note_inputs={"visit_code": "99214"})

        assert note.note_inputs == {"visit_code": "99214"}
        assert note.status == "processing"
        assert keep is False
        assert notes_repo.get(note_id, USER).status == "processing"  # type: ignore[union-attr]  # just written

    def test_new_inputs_leave_the_medical_decision_making_choices_alone(
        self,
        service: NoteRedraftService,
        session_repo: InMemoryTherapySessionRepository,
        patient_repo: InMemoryPatientRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        session, _ = _drafted_session(
            session_repo,
            patient_repo,
            notes_repo,
            note_type="psychiatric_follow_up",
            content={"plan": {"follow_up": "Four weeks."}},
            note_inputs={"place_of_service": "In office", "mdm_problems": "moderate"},
        )

        note, _keep = service.start(
            session.id, USER, note_inputs={"place_of_service": "Telehealth", "mdm_problems": "low"}
        )

        assert note.note_inputs == {"place_of_service": "Telehealth", "mdm_problems": "moderate"}

    def test_an_input_the_type_refuses_is_refused(
        self, service: NoteRedraftService, drafted: tuple[TherapySession, str]
    ) -> None:
        with pytest.raises(BadRequestError) as exc:
            service.start(drafted[0].id, USER, note_inputs={"visit_code": "00000"})
        assert exc.value.code == "INVALID_NOTE_INPUTS"

    def test_a_signed_note_is_never_redrafted(
        self,
        service: NoteRedraftService,
        notes_repo: InMemoryNotesRepository,
        drafted: tuple[TherapySession, str],
    ) -> None:
        session, note_id = drafted
        note = notes_repo.get(note_id, USER)
        assert note is not None
        note.finalized_at = datetime.now(UTC)
        notes_repo.update(note, USER)

        with pytest.raises(NoteLockedError):
            service.start(session.id, USER, note_inputs={"visit_code": "99214"})
        assert notes_repo.get(note_id, USER).note_inputs == {"visit_code": "99213"}  # type: ignore[union-attr]  # exists

    def test_edits_need_an_answer_before_a_redraft(
        self,
        service: NoteRedraftService,
        notes_repo: InMemoryNotesRepository,
        drafted: tuple[TherapySession, str],
    ) -> None:
        session, note_id = drafted
        NoteService(notes_repo).update_note_edits(note_id, _visit("Mine."), USER)

        with pytest.raises(NoteHasEditsError):
            service.start(session.id, USER, note_inputs={"visit_code": "99214"})
        _, keep = service.start(session.id, USER, edits=RedraftEdits.KEEP)
        assert keep is True

    def test_one_redraft_at_a_time(
        self, service: NoteRedraftService, drafted: tuple[TherapySession, str]
    ) -> None:
        service.start(drafted[0].id, USER)
        with pytest.raises(NoteRedraftInProgressError):
            service.start(drafted[0].id, USER)

    def test_an_imported_note_is_not_redrafted(
        self,
        service: NoteRedraftService,
        session_repo: InMemoryTherapySessionRepository,
        patient_repo: InMemoryPatientRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        session, _ = _drafted_session(
            session_repo, patient_repo, notes_repo, source=SessionSource.IMPORTED
        )
        with pytest.raises(NoteNotRedraftableError):
            service.start(session.id, USER)


# --- Running it ---


class TestRun:
    def test_the_new_draft_is_written_with_the_new_inputs(
        self,
        service: NoteRedraftService,
        generator: RecordingGenerator,
        drafted: tuple[TherapySession, str],
    ) -> None:
        session, _ = drafted
        service.start(session.id, USER, note_inputs={"visit_code": "99214"})
        generator.next_content = _visit("Redrafted.", code_note="Billed as 99214.")

        _, _, note = service.run(session.id, USER, keep_edits=False)

        assert generator.calls == [
            {
                "note_type": VISIT.key,
                "transcript": "[00:01] Therapist: Hello",
                "inputs": {"visit_code": "99214"},
                "client_present_end_seconds": None,
            }
        ]
        assert note.content == _visit("Redrafted.", code_note="Billed as 99214.")
        assert note.status == "complete"
        # Drafted again from the note the clinician already has.
        assert generator.current_notes == [_visit("First draft.")]

    def test_a_redraft_keeps_the_dictated_tail_out_of_session_time(
        self,
        service: NoteRedraftService,
        generator: RecordingGenerator,
        session_repo: InMemoryTherapySessionRepository,
        drafted: tuple[TherapySession, str],
    ) -> None:
        session, _ = drafted
        session.client_present_end_seconds = 2158.4
        session_repo.update(session)
        service.start(session.id, USER)

        service.run(session.id, USER, keep_edits=False)

        assert generator.calls[0]["client_present_end_seconds"] == 2158.4

    def test_kept_edits_survive_and_the_rest_is_redrafted(
        self,
        service: NoteRedraftService,
        generator: RecordingGenerator,
        notes_repo: InMemoryNotesRepository,
        drafted: tuple[TherapySession, str],
    ) -> None:
        session, note_id = drafted
        NoteService(notes_repo).update_note_edits(note_id, _visit("Mine."), USER)
        service.start(session.id, USER, edits=RedraftEdits.KEEP)
        generator.next_content = _visit("Redrafted.", "New plan.")

        _, _, note = service.run(session.id, USER, keep_edits=True)

        assert note.content == _visit("Redrafted.", "New plan.")
        assert note.content_edited == _visit("Mine.", "New plan.")
        assert generator.current_notes == [_visit("Mine.")]

    def test_redraft_everything_drops_the_edits(
        self,
        service: NoteRedraftService,
        generator: RecordingGenerator,
        notes_repo: InMemoryNotesRepository,
        drafted: tuple[TherapySession, str],
    ) -> None:
        session, note_id = drafted
        NoteService(notes_repo).update_note_edits(note_id, _visit("Mine."), USER)
        service.start(session.id, USER, edits=RedraftEdits.REPLACE)
        generator.next_content = _visit("Redrafted.")

        _, _, note = service.run(session.id, USER, keep_edits=False)

        assert note.content_edited is None
        # Edits the clinician discarded don't reach the model either.
        assert generator.current_notes == [_visit("First draft.")]

    def test_a_failed_redraft_leaves_the_note_as_it_was(
        self,
        service: NoteRedraftService,
        generator: RecordingGenerator,
        notes_repo: InMemoryNotesRepository,
        drafted: tuple[TherapySession, str],
    ) -> None:
        session, note_id = drafted
        service.start(session.id, USER)
        generator.error = ValueError("model said no")

        with pytest.raises(SOAPGenerationFailedError):
            service.run(session.id, USER, keep_edits=False)

        note = notes_repo.get(note_id, USER)
        assert note is not None
        assert note.content == _visit("First draft.")
        assert note.status == "failed"

    def test_a_transient_failure_is_retried_until_the_last_attempt(
        self,
        service: NoteRedraftService,
        generator: RecordingGenerator,
        notes_repo: InMemoryNotesRepository,
        drafted: tuple[TherapySession, str],
    ) -> None:
        session, note_id = drafted
        service.start(session.id, USER)
        generator.error = TransientNoteGenerationError("429")

        with pytest.raises(TransientSOAPGenerationError):
            service.run(session.id, USER, keep_edits=False)
        assert notes_repo.get(note_id, USER).status == "processing"  # type: ignore[union-attr]  # exists

        with pytest.raises(SOAPGenerationFailedError):
            service.run(session.id, USER, keep_edits=False, transient_is_terminal=True)
        assert notes_repo.get(note_id, USER).status == "failed"  # type: ignore[union-attr]  # exists

    def test_a_note_signed_during_the_redraft_keeps_its_signed_body(
        self,
        service: NoteRedraftService,
        generator: RecordingGenerator,
        notes_repo: InMemoryNotesRepository,
        drafted: tuple[TherapySession, str],
    ) -> None:
        session, note_id = drafted
        service.start(session.id, USER)
        generator.next_content = _visit("Redrafted.")
        original_generate = generator.generate_note

        def sign_then_generate(*args: Any, **kwargs: Any) -> GeneratedNote:
            note = notes_repo.get(note_id, USER)
            assert note is not None
            note.finalized_at = datetime.now(UTC)
            notes_repo.update(note, USER)
            return original_generate(*args, **kwargs)

        generator.generate_note = sign_then_generate  # type: ignore[method-assign]  # sign mid-call

        _, _, note = service.run(session.id, USER, keep_edits=False)

        assert note.content == _visit("First draft.")
        assert note.status == "complete"

    def test_a_duplicate_job_finds_nothing_to_do(
        self, service: NoteRedraftService, drafted: tuple[TherapySession, str]
    ) -> None:
        with pytest.raises(RedraftNotPendingError):
            service.run(drafted[0].id, USER, keep_edits=False)


# --- The route ---


@pytest.fixture
def route_generator() -> RecordingGenerator:
    return RecordingGenerator()


@pytest.fixture
def redraft_client(client: TestClient, route_generator: RecordingGenerator) -> Iterator[TestClient]:
    app.dependency_overrides[get_note_generation_service] = lambda: route_generator
    yield client
    app.dependency_overrides.pop(get_note_generation_service, None)


@pytest.fixture
def route_session(
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> tuple[TherapySession, str]:
    assert mock_user_id == USER
    return _drafted_session(
        mock_session_repo, mock_repo, mock_notes_repo, note_inputs={"visit_code": "99213"}
    )


class TestRoute:
    def test_a_redraft_starts_and_is_audited(
        self,
        redraft_client: TestClient,
        route_session: tuple[TherapySession, str],
        monkeypatch: pytest.MonkeyPatch,
        mock_audit_service: Any,
    ) -> None:
        enqueued: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "app.routes.note_redraft.enqueue",
            lambda _q, _p, payload, **_k: enqueued.append(payload),
        )
        session, note_id = route_session

        response = redraft_client.post(
            f"/api/sessions/{session.id}/note/redraft",
            json={"note_inputs": {"visit_code": "99214"}},
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["id"] == note_id
        assert body["status"] == "processing"
        assert body["note_inputs"] == {"visit_code": "99214"}
        assert enqueued == [{"session_id": session.id, "user_id": USER, "keep_edits": False}]
        actions = [call.args[0].action for call in mock_audit_service._repo.append.call_args_list]
        assert "note_redraft_requested" in actions

    def test_edits_without_an_answer_are_refused(
        self,
        redraft_client: TestClient,
        route_session: tuple[TherapySession, str],
        mock_notes_repo: InMemoryNotesRepository,
    ) -> None:
        session, note_id = route_session
        NoteService(mock_notes_repo).update_note_edits(note_id, _visit("Mine."), USER)

        response = redraft_client.post(f"/api/sessions/{session.id}/note/redraft", json={})

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "NOTE_HAS_EDITS"

    def test_a_signed_note_is_locked(
        self,
        redraft_client: TestClient,
        route_session: tuple[TherapySession, str],
        mock_notes_repo: InMemoryNotesRepository,
    ) -> None:
        session, note_id = route_session
        note = mock_notes_repo.get(note_id, USER)
        assert note is not None
        note.finalized_at = datetime.now(UTC)
        mock_notes_repo.update(note, USER)

        response = redraft_client.post(
            f"/api/sessions/{session.id}/note/redraft",
            json={"note_inputs": {"visit_code": "99214"}},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "NOTE_LOCKED"


class TestJob:
    """The queue worker: scoped to the tenant, answering the queue like generate-soap."""

    @pytest.fixture(autouse=True)
    def scoped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            note_redraft_routes, "resolve_tenant_schema_for_user", lambda _uid: "practice_x"
        )
        monkeypatch.setattr(note_redraft_routes, "get_db_session", MagicMock)
        monkeypatch.setattr(note_redraft_routes, "set_tenant_schema", MagicMock())
        monkeypatch.setattr(note_redraft_routes, "arm_current_user_id", MagicMock())

    def _run(self, service: NoteRedraftService, session_id: str) -> dict[str, str]:
        return note_redraft_routes.redraft_note_job(
            RedraftNoteJob(session_id=session_id, user_id=USER, keep_edits=False),
            MagicMock(headers={}),
            None,
            service,
            MagicMock(),
            MagicMock(),
        )

    def test_the_job_writes_the_redraft(
        self,
        service: NoteRedraftService,
        generator: RecordingGenerator,
        drafted: tuple[TherapySession, str],
    ) -> None:
        service.start(drafted[0].id, USER)
        generator.next_content = _visit("Redrafted.")
        assert self._run(service, drafted[0].id) == {"status": "ok"}

    def test_a_transient_failure_asks_the_queue_to_retry(
        self,
        service: NoteRedraftService,
        generator: RecordingGenerator,
        drafted: tuple[TherapySession, str],
    ) -> None:
        service.start(drafted[0].id, USER)
        generator.error = TransientNoteGenerationError("429")
        with pytest.raises(HTTPException) as exc:
            self._run(service, drafted[0].id)
        assert exc.value.status_code == 503

    def test_a_duplicate_delivery_is_dropped(
        self, service: NoteRedraftService, drafted: tuple[TherapySession, str]
    ) -> None:
        assert self._run(service, drafted[0].id) == {"status": "not_found"}
