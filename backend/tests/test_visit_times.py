# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Visit times on the note, and the psychotherapy window the clinician confirms."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

import pytest
from app.api_errors import BadRequestError, UnprocessableEntityError
from app.main import app
from app.models import Note, Patient, Transcript
from app.models.session import TherapySession
from app.models.visit_times import ConfirmPsychotherapyWindowRequest
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.client_present import TimedSegment
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.notes.visit_times import (
    SIGNALS_AGREE_SECONDS,
    StartCandidate,
    apply_confirmed_window,
    disagrees,
    parse_transcript_time,
    resolve_start_candidates,
    snap_to_turn,
    stated_clock_time,
    window_minutes,
    window_text,
)
from app.repositories.note import InMemoryNotesRepository
from app.routes.notes import get_appointment_repository
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.note_service import NoteService
from app.services.note_signing import NoteLockedError
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion
from app.services.visit_times_service import build_visit_times, confirm_psychotherapy_window

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories import InMemoryTherapySessionRepository
    from app.scheduling_engine.models.appointment import Appointment
    from fastapi.testclient import TestClient

_STARTED = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
_EASTERN = ZoneInfo("America/New_York")

# A 65-minute visit: a 12-minute medication check, then the therapy portion
# from the clinician's cue at 12:30, the client leaving at 65:00.
_VISIT = "\n".join(
    [
        "[00:00:05] Therapist: How has the medication been since the dose change?",
        "[00:03:00] Client: Better sleep, no side effects that I have noticed at all.",
        "[00:11:50] Therapist: Good, we will keep the dose where it is for now.",
        "[00:12:30] Therapist: Now let's get into the session work you wanted.",
        "[00:30:00] Client: I keep replaying the argument with my sister every night.",
        "[01:04:52] Client: Thank you, this really helped me today.",
        "[01:06:00] Therapist: Addendum. Client denies suicidal ideation.",
    ]
)


def _session(
    *, boundary: float | None = 3895.0, content: str = _VISIT, video: str | None = "zoom"
) -> TherapySession:
    return TherapySession(
        id=str(uuid.uuid4()),
        user_id="test-user-123",
        patient_id=str(uuid.uuid4()),
        session_date=_STARTED,
        session_number=1,
        status="pending_review",
        transcript=Transcript(format="txt", content=content),
        created_at=_STARTED,
        video_platform=video,
        started_at=_STARTED,
        ended_at=_STARTED + timedelta(minutes=67),
        client_present_end_seconds=boundary,
    )


def _note(session: TherapySession, *, time_field: str = "Not stated.", **extra: Any) -> Note:
    return Note(
        id=str(uuid.uuid4()),
        patient_id=session.patient_id,
        session_id=session.id,
        note_type="custom.follow_up",
        created_at=_STARTED,
        updated_at=_STARTED,
        content={
            "plan": {"follow_up": "Four weeks."},
            "psychotherapy": {"psychotherapy_time": time_field, "interventions": "CBT."},
        },
        **extra,
    )


class TestStartSignals:
    def test_the_clinicians_spoken_cue_wins(self) -> None:
        assert resolve_start_candidates(marked=750.0, cued=True, attributed=1800.0) == [
            StartCandidate(750.0, "spoken_cue")
        ]

    def test_agreeing_signals_offer_one_start(self) -> None:
        assert resolve_start_candidates(
            marked=750.0, cued=False, attributed=750.0 + SIGNALS_AGREE_SECONDS
        ) == [StartCandidate(750.0, "marked")]

    def test_disagreeing_signals_offer_both(self) -> None:
        assert resolve_start_candidates(
            marked=750.0, cued=False, attributed=750.0 + SIGNALS_AGREE_SECONDS + 1
        ) == [StartCandidate(750.0, "marked"), StartCandidate(871.0, "attributed")]

    def test_a_lone_signal_is_offered(self) -> None:
        assert resolve_start_candidates(marked=None, cued=False, attributed=30.0) == [
            StartCandidate(30.0, "attributed")
        ]

    def test_transcript_times_parse_as_written(self) -> None:
        assert parse_transcript_time("12:30") == 750.0
        assert parse_transcript_time("[00:12:30]") == 750.0
        assert parse_transcript_time("Stand-in draft for psychotherapy_start.") is None

    def test_a_stated_time_is_kept_only_when_it_names_a_time(self) -> None:
        assert stated_clock_time("around 10:15") == "around 10:15"
        assert stated_clock_time("Stand-in draft for psychotherapy_start.stated.") is None

    def test_a_mark_snaps_to_the_nearest_turn(self) -> None:
        turns = [
            TimedSegment("Therapist", "a", 700.0, 705.0),
            TimedSegment("Client", "b", 790, 800),
        ]
        assert snap_to_turn(740.0, turns) == 700.0


class TestWindow:
    def test_minutes_round_down(self) -> None:
        assert window_minutes(750.0, 3895.0) == 52
        assert window_minutes(750.0, 750.0 + 53 * 60 - 1) == 52

    def test_the_window_reads_as_clock_times_and_minutes(self) -> None:
        text = window_text(
            52,
            start_at=_STARTED + timedelta(seconds=750),
            end_at=_STARTED + timedelta(seconds=3895),
            zone=_EASTERN,
        )
        assert text == "11:12 AM to 12:04 PM, 52 minutes"

    def test_a_dictated_time_disagrees_unless_the_minutes_match(self) -> None:
        confirmed = {"window_text": "11:12 AM to 12:04 PM, 52 minutes", "minutes": 52}
        assert disagrees("11:15 to 12:00, 45 minutes", confirmed)
        assert not disagrees("Psychotherapy 52 minutes", confirmed)
        assert not disagrees(None, confirmed)
        assert not disagrees("45 minutes", {**confirmed, "keep_dictated": True})

    def test_the_window_fills_only_an_unstated_field(self) -> None:
        window = {"confirmed": {"window_text": "52 minutes", "minutes": 52}}
        unstated = {"psychotherapy": {"psychotherapy_time": "Not stated."}}
        dictated = {"psychotherapy": {"psychotherapy_time": "45 minutes"}}

        filled = apply_confirmed_window(unstated, window)
        assert filled is not None
        assert filled["psychotherapy"]["psychotherapy_time"] == "52 minutes"
        assert apply_confirmed_window(dictated, window) is dictated


def _appointment(started: datetime, ended: datetime) -> Appointment:
    return cast(
        "Appointment", SimpleNamespace(telehealth_started_at=started, telehealth_ended_at=ended)
    )


class TestVisitTimes:
    def test_start_end_and_minutes_come_from_the_session(self) -> None:
        session = _session()
        times = build_visit_times(session, _note(session), None)

        assert times.started_at == session.started_at
        assert times.ended_at == session.ended_at
        assert times.total_minutes == 67

    def test_the_calls_own_times_win_when_the_platform_reported_them(self) -> None:
        session = _session()
        call = _appointment(_STARTED + timedelta(minutes=1), _STARTED + timedelta(minutes=60))

        times = build_visit_times(session, _note(session), call)

        assert times.started_at == _STARTED + timedelta(minutes=1)
        assert times.total_minutes == 59

    def test_a_psychotherapy_note_offers_the_window_and_no_time_with_documentation(self) -> None:
        session = _session()
        times = build_visit_times(session, _note(session), None)

        assert times.psychotherapy is not None
        assert times.psychotherapy.offered
        assert times.psychotherapy.end_seconds == 3895.0
        assert [t.seconds for t in times.psychotherapy.turns][-1] == 3892.0
        assert times.total_with_documentation_minutes is None

    def test_a_note_without_psychotherapy_shows_time_with_documentation(self) -> None:
        session = _session()
        note = _note(session)
        note.content = {"plan": {"follow_up": "Four weeks."}}

        times = build_visit_times(session, note, None)

        assert times.psychotherapy is None
        assert times.total_with_documentation_minutes == 67

    def test_a_dictation_offers_no_psychotherapy(self) -> None:
        session = _session(boundary=0.0)
        times = build_visit_times(session, _note(session), None)

        assert times.psychotherapy is not None
        assert not times.psychotherapy.offered


@pytest.fixture
def notes() -> InMemoryNotesRepository:
    repo = InMemoryNotesRepository()
    repo.grant_all_access()
    return repo


def _confirm(
    session: TherapySession, note: Note, notes: InMemoryNotesRepository, **body: Any
) -> Note:
    request = ConfirmPsychotherapyWindowRequest(time_zone="America/New_York", **body)
    return confirm_psychotherapy_window(session, note, request, NoteService(notes), "u1")


class TestConfirm:
    def test_confirming_a_start_writes_the_window_into_the_note(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        note = notes.add(_note(session))

        saved = _confirm(session, note, notes, start_seconds=750.0)

        assert saved.psychotherapy_window is not None
        assert saved.psychotherapy_window["confirmed"]["minutes"] == 52
        assert saved.content_edited is not None
        assert (
            saved.content_edited["psychotherapy"]["psychotherapy_time"]
            == "11:12 AM to 12:04 PM, 52 minutes"
        )

    def test_typed_minutes_are_kept_without_clock_times(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        note = notes.add(_note(session))

        saved = _confirm(session, note, notes, minutes=40)

        assert saved.content_edited is not None
        assert saved.content_edited["psychotherapy"]["psychotherapy_time"] == "40 minutes"

    def test_minutes_above_the_client_present_span_are_rejected(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        note = notes.add(_note(session))

        with pytest.raises(UnprocessableEntityError):
            _confirm(session, note, notes, minutes=65)  # the client left at 64:55
        with pytest.raises(UnprocessableEntityError):
            _confirm(session, note, notes, start_seconds=3895.0)

    def test_a_dictation_has_no_window_to_confirm(self, notes: InMemoryNotesRepository) -> None:
        session = _session(boundary=0.0)
        note = notes.add(_note(session))

        with pytest.raises(BadRequestError):
            _confirm(session, note, notes, minutes=10)

    def test_a_dictated_time_that_disagrees_stays_until_the_clinician_picks(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        note = notes.add(_note(session, time_field="11:15 to 12:00, 45 minutes"))

        saved = _confirm(session, note, notes, start_seconds=750.0)
        times = build_visit_times(session, saved, None)
        assert times.psychotherapy is not None
        assert times.psychotherapy.disagrees
        assert times.psychotherapy.dictated_time == "11:15 to 12:00, 45 minutes"

        chosen = _confirm(session, saved, notes, start_seconds=750.0, resolution="use_confirmed")
        assert chosen.content_edited is not None
        assert chosen.content_edited["psychotherapy"]["psychotherapy_time"].endswith("52 minutes")
        resolved = build_visit_times(session, chosen, None).psychotherapy
        assert resolved is not None
        assert not resolved.disagrees

    def test_keeping_the_dictated_time_settles_the_disagreement(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        note = notes.add(_note(session, time_field="45 minutes"))

        saved = _confirm(session, note, notes, start_seconds=750.0, resolution="keep_dictated")

        psychotherapy = build_visit_times(session, saved, None).psychotherapy
        assert psychotherapy is not None
        assert not psychotherapy.disagrees
        assert saved.content_edited is None

    def test_a_signed_note_is_not_changed(self, notes: InMemoryNotesRepository) -> None:
        session = _session()
        note = notes.add(_note(session, finalized_at=_STARTED))

        with pytest.raises(NoteLockedError):
            _confirm(session, note, notes, minutes=30)

    def test_a_confirmed_window_survives_a_redraft(self, notes: InMemoryNotesRepository) -> None:
        session = _session()
        service = NoteService(notes)
        note = notes.add(_note(session))
        _confirm(session, note, notes, start_seconds=750.0)

        redrafted = service.create_or_update_for_session(
            session_id=session.id,
            patient_id=session.patient_id,
            note_type="custom.follow_up",
            content={"psychotherapy": {"psychotherapy_time": "Not stated.", "interventions": ""}},
            user_id="u1",
            psychotherapy_start={"candidates": [], "stated_clock_time": None},
        )

        assert redrafted.psychotherapy_window is not None
        assert redrafted.psychotherapy_window["confirmed"]["minutes"] == 52
        assert redrafted.content is not None
        assert redrafted.content["psychotherapy"]["psychotherapy_time"].endswith("52 minutes")

    def test_a_confirmed_window_survives_redrafting_the_note(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        service = NoteService(notes)
        confirmed = _confirm(session, notes.add(_note(session)), notes, minutes=40)

        redrafted = service.complete_redraft(
            confirmed,
            content={"psychotherapy": {"psychotherapy_time": "", "interventions": "CBT."}},
            content_edited=None,
            note_type_version=None,
            user_id="u1",
            psychotherapy_start={"candidates": [{"seconds": 750.0, "source": "marked"}]},
        )

        assert redrafted.psychotherapy_window is not None
        assert redrafted.psychotherapy_window["confirmed"]["minutes"] == 40
        assert redrafted.psychotherapy_window["proposal"]["candidates"][0]["seconds"] == 750.0
        assert redrafted.content is not None
        assert redrafted.content["psychotherapy"]["psychotherapy_time"] == "40 minutes"


_FOLLOW_UP = PracticeNoteTypeSpec.model_validate(
    {
        "label": "Follow-up",
        "system_prompt": "Draft the visit.",
        "user_template": "{fields}\n\nTranscript:\n{transcript}",
        "sections": [
            {
                "key": "plan",
                "label": "Plan",
                "fields": [{"key": "follow_up", "label": "Follow up"}],
            },
            {
                "key": "psychotherapy",
                "label": "Psychotherapy",
                "fields": [
                    {"key": "psychotherapy_time", "label": "Time"},
                    {"key": "modality_interventions", "label": "Interventions"},
                ],
            },
        ],
    }
)


class TestDraftProposesAStart:
    def _draft(self, *responses: dict[str, Any]) -> tuple[Any, FakeStructuredLLMGateway]:
        registry = NoteTypeRegistry()
        register_builtin_note_types(registry)
        gateway = FakeStructuredLLMGateway(
            responses=[StructuredCompletion(data=r) for r in responses]
        )
        definition = to_definition("custom.follow_up", 1, _FOLLOW_UP)
        patient = Patient(
            id="p1", first_name="A", last_name="B", created_at=_STARTED, updated_at=_STARTED
        )
        result = RegistryNoteGenerationService(
            registry=registry, llm_gateway=gateway
        ).generate_note(
            definition.key,
            Transcript(format="txt", content=_VISIT),
            patient,
            _STARTED,
            definition=definition,
            client_present_end_seconds=3895.0,
        )
        return result, gateway

    def test_the_draft_marks_a_start_after_the_medication_check(self) -> None:
        drafted = {
            "plan": {"follow_up": "Four weeks."},
            "psychotherapy": {
                "psychotherapy_time": "Not stated.",
                "modality_interventions": "Cognitive restructuring of the replayed argument.",
            },
            "psychotherapy_start": {
                "transcript_time": "12:30",
                "cued_by_clinician": False,
                "stated_clock_time": "",
            },
        }
        attribution = {"attributions": [{"claim": 1, "segments": [4]}]}

        result, gateway = self._draft(drafted, attribution)

        assert "psychotherapy_start" in gateway.calls[0]["response_schema"]["properties"]
        assert "psychotherapy_start" not in result.content
        # The mark (12:30) and the attributed turn (30:00) disagree: both offered,
        # neither the client-present span's start.
        assert result.psychotherapy_start == {
            "candidates": [
                {"seconds": 750.0, "source": "marked"},
                {"seconds": 1800.0, "source": "attributed"},
            ],
            "stated_clock_time": None,
        }

    def test_an_unreadable_mark_and_a_failed_attribution_propose_nothing(self) -> None:
        drafted = {
            "psychotherapy": {"modality_interventions": ""},
            "psychotherapy_start": {"transcript_time": "later on", "stated_clock_time": ""},
        }

        result, _gateway = self._draft(drafted)

        assert result.psychotherapy_start is None


@pytest.fixture
def appointments() -> Iterator[InMemoryAppointmentRepository]:
    repo = InMemoryAppointmentRepository()
    app.dependency_overrides[get_appointment_repository] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_appointment_repository, None)


class TestRoutes:
    def test_the_note_shows_times_and_a_confirm_is_validated(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        mock_notes_repo: InMemoryNotesRepository,
        appointments: InMemoryAppointmentRepository,
    ) -> None:
        del appointments
        session = mock_session_repo.create(_session())
        mock_notes_repo.add(_note(session))

        times = client.get(f"/api/sessions/{session.id}/visit-times")
        assert times.status_code == 200
        assert times.json()["total_minutes"] == 67
        assert times.json()["psychotherapy"]["offered"] is True

        too_long = client.put(
            f"/api/sessions/{session.id}/psychotherapy-window",
            json={"minutes": 70, "time_zone": "America/New_York"},
        )
        assert too_long.status_code == 422

        confirmed = client.put(
            f"/api/sessions/{session.id}/psychotherapy-window",
            json={"start_seconds": 750, "time_zone": "America/New_York"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["psychotherapy"]["confirmed_minutes"] == 52

    def test_another_clinicians_session_is_not_found(
        self,
        client: TestClient,
        mock_session_repo: InMemoryTherapySessionRepository,
        appointments: InMemoryAppointmentRepository,
    ) -> None:
        del appointments
        other = _session()
        other.user_id = "someone-else"
        session = mock_session_repo.create(other)

        assert client.get(f"/api/sessions/{session.id}/visit-times").status_code == 404
