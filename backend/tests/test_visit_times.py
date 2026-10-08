# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Visit times on the note, and the psychotherapy time the clinician confirms."""

from __future__ import annotations

import itertools
import json
import random
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

import pytest
from app.api_errors import BadRequestError, UnprocessableEntityError
from app.main import app
from app.models import Note, Patient, Transcript
from app.models.session import TherapySession
from app.models.session_dictation import SessionDictation
from app.models.visit_times import ConfirmPsychotherapyWindowRequest
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.client_present import TimedSegment
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.notes.visit_times import (
    TURN_LABELS,
    DictatedTime,
    Run,
    TurnLabel,
    apply_confirmed_window,
    dictated_time_text,
    disagrees,
    drafted_time,
    interleaved_text,
    layout,
    therapy_minutes,
    therapy_seconds,
    window_minutes,
    window_text,
)
from app.repositories.note import InMemoryNotesRepository
from app.repositories.session_dictation import InMemorySessionDictationRepository
from app.routes.notes import get_appointment_repository
from app.routes.session_dictations import get_dictation_repository
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.note_redraft import has_edits
from app.services.note_service import NoteService
from app.services.note_signing import NoteLockedError
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion
from app.services.therapy_labels import parse_labels
from app.services.visit_times_service import build_visit_times, confirm_psychotherapy_window

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.session_dictation import DictationStatus
    from app.repositories import InMemoryTherapySessionRepository
    from app.scheduling_engine.models.appointment import Appointment
    from fastapi.testclient import TestClient

# A psychiatric follow-up drafted by the model, redrafted after a dictation,
# and the window its clinician confirmed (synthetic visit).
_CAPTURED = json.loads(
    (Path(__file__).parent / "fixtures" / "notes" / "psychiatric_follow_up_drafts.json").read_text()
)

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

# The turns of that visit while the client was present, labeled two ways.
_CONTIGUOUS: list[dict[str, Any]] = [
    {"seconds": 5.0, "label": "medication_management"},
    {"seconds": 180.0, "label": "medication_management"},
    {"seconds": 710.0, "label": "medication_management"},
    {"seconds": 750.0, "label": "therapy"},
    {"seconds": 1800.0, "label": "therapy"},
    {"seconds": 3892.0, "label": "therapy"},
]
# Therapy 3:00-11:50 and 12:30-30:00, a risk screen to the end: 1580 s, 26 minutes.
_INTERLEAVED: list[dict[str, Any]] = [
    {"seconds": 5.0, "label": "admin"},
    {"seconds": 180.0, "label": "therapy"},
    {"seconds": 710.0, "label": "medication_management"},
    {"seconds": 750.0, "label": "therapy"},
    {"seconds": 1800.0, "label": "screening_risk"},
    {"seconds": 3892.0, "label": "screening_risk"},
]


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


def _note(
    session: TherapySession,
    *,
    dictated: DictatedTime | None = None,
    labels: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> Note:
    """A drafted note; the time field holds the dictated time as a draft renders it."""
    proposal = {"dictated": dictated.to_dict() if dictated else None, "labels": labels or []}
    return Note(
        id=str(uuid.uuid4()),
        patient_id=session.patient_id,
        session_id=session.id,
        note_type="custom.follow_up",
        created_at=_STARTED,
        updated_at=_STARTED,
        content={
            "plan": {"follow_up": "Four weeks."},
            "psychotherapy": {
                "psychotherapy_time": dictated_time_text(dictated),
                "interventions": "CBT.",
            },
        },
        psychotherapy_window={"proposal": proposal},
        **extra,
    )


def _turns(*starts: float) -> list[TimedSegment]:
    return [TimedSegment("Therapist", "", s, s + 1) for s in starts]


class TestDictatedTime:
    def test_a_reply_states_a_time_only_with_minutes_or_a_clock_time(self) -> None:
        assert DictatedTime.from_reply({"as_dictated": "", "minutes": 0}) is None
        assert DictatedTime.from_reply({"start": " ", "end": ""}) is None
        assert DictatedTime.from_reply("41 minutes") is None
        assert DictatedTime.from_reply({"minutes": 41}) == DictatedTime(minutes=41)
        assert DictatedTime.from_reply({"start": "around 10:15"}) == DictatedTime(
            start="around 10:15"
        )

    def test_it_renders_clock_times_then_minutes(self) -> None:
        assert dictated_time_text(DictatedTime("10:14", "10:55", 41)) == (
            "10:14 to 10:55, 41 minutes"
        )
        assert dictated_time_text(DictatedTime(minutes=18)) == "18 minutes"
        assert dictated_time_text(DictatedTime(start="10:15")) == "Started 10:15"
        assert dictated_time_text(None) == ""


class TestTherapyMinutes:
    def test_a_turn_runs_to_the_next_and_the_last_to_where_the_client_left(self) -> None:
        turns = _turns(0, 60, 200)
        labels: dict[float, TurnLabel] = {60.0: "therapy", 200.0: "therapy"}

        assert therapy_seconds(labels, turns, 300.0) == 240.0
        assert therapy_minutes(labels, turns, 300.0) == 4

    def test_runs_merge_and_an_unlabeled_turn_is_unattributed(self) -> None:
        turns = _turns(0, 60, 120, 180)
        labels: dict[float, TurnLabel] = {0.0: "admin", 60.0: "therapy", 120.0: "therapy"}

        assert layout(labels, turns, 240.0) == [
            Run("admin", 0.0, 60.0),
            Run("therapy", 60.0, 180.0),
            Run("unattributed", 180.0, 240.0),
        ]

    def test_the_tail_is_never_counted(self) -> None:
        turns = _turns(0, 60, 400, 500)  # the client left at 300
        labels: dict[float, TurnLabel] = {s.start: "therapy" for s in turns}

        assert therapy_seconds(labels, turns, 300.0) == 300.0

    @pytest.mark.parametrize("seed", range(200))
    def test_random_layouts_never_exceed_the_span_or_count_the_tail(self, seed: int) -> None:
        rng = random.Random(seed)  # noqa: S311 — deterministic layouts, not crypto
        starts = sorted(rng.sample(range(0, 4000), rng.randint(0, 40)))
        turns = _turns(*map(float, starts))
        end = float(rng.randint(1, 4200))
        labels: dict[float, TurnLabel] = {
            s.start: rng.choice(TURN_LABELS) for s in turns if rng.random() < 0.9
        }

        seconds = therapy_seconds(labels, turns, end)
        assert 0 <= seconds <= end
        assert therapy_minutes(labels, turns, end) * 60 <= end
        # Relabeling the tail changes nothing.
        tail: dict[float, TurnLabel] = {s.start: "therapy" for s in turns if s.start >= end}
        assert therapy_seconds({**labels, **tail}, turns, end) == seconds
        # The runs tile the client-present span exactly.
        runs = layout(labels, turns, end)
        assert all(a.end == b.start for a, b in itertools.pairwise(runs))
        if runs:
            assert runs[-1].end == end
        # Labeling every turn therapy counts from the first turn to where the client left.
        everything: dict[float, TurnLabel] = {s.start: "therapy" for s in turns}
        present = [s for s in starts if s < end]
        assert therapy_seconds(everything, turns, end) == (end - present[0] if present else 0)


class TestLabels:
    def test_every_client_present_turn_gets_a_label(self) -> None:
        turns = _turns(0, 60, 120, 180)
        reply = {
            "runs": [
                {"first_segment": 0, "last_segment": 0, "label": "admin"},
                {"first_segment": 1, "last_segment": 2, "label": "therapy"},
                {"first_segment": 3, "last_segment": 9, "label": "screening_risk"},
                {"first_segment": 1, "last_segment": 1, "label": "admin"},
                {"first_segment": 0, "last_segment": 3, "label": "billable"},
            ],
            "cue_segment": 1,
        }

        labels, cue = parse_labels(reply, turns)

        # A run past the last turn is cut at it; the first run to claim a turn wins.
        assert labels == {
            0.0: "admin",
            60.0: "therapy",
            120.0: "therapy",
            180.0: "screening_risk",
        }
        assert cue == 60.0

    def test_no_cue_and_no_runs_label_nothing(self) -> None:
        assert parse_labels({"runs": [], "cue_segment": -1}, _turns(0, 60)) == ({}, None)


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

    def test_interleaved_minutes_say_so(self) -> None:
        assert interleaved_text(26) == (
            "26 minutes (interleaved with medication management; time accounted separately)"
        )

    def test_a_dictated_time_disagrees_unless_the_minutes_match(self) -> None:
        confirmed = {"window_text": "11:12 AM to 12:04 PM, 52 minutes", "minutes": 52}

        def window(dictated: DictatedTime) -> tuple[dict[str, Any], dict[str, Any]]:
            content = {"psychotherapy": {"psychotherapy_time": dictated_time_text(dictated)}}
            return content, {"proposal": {"dictated": dictated.to_dict()}, "confirmed": confirmed}

        assert disagrees(*window(DictatedTime("11:15", "12:00", 45)))
        assert not disagrees(*window(DictatedTime(minutes=52)))
        assert not disagrees(
            {"psychotherapy": {"psychotherapy_time": ""}}, {"confirmed": confirmed}
        )
        content, kept = window(DictatedTime(minutes=45))
        assert not disagrees(content, {**kept, "confirmed": {**confirmed, "keep_dictated": True}})

    def test_text_the_clinician_typed_disagrees_until_they_pick(self) -> None:
        confirmed = {"window_text": "52 minutes", "minutes": 52}
        typed = {"psychotherapy": {"psychotherapy_time": "about half an hour"}}

        assert drafted_time(typed) == "about half an hour"
        assert disagrees(typed, {"confirmed": confirmed})
        assert apply_confirmed_window(typed, {"confirmed": confirmed}) is typed

    def test_the_time_fills_only_an_empty_field(self) -> None:
        window = {
            "proposal": {"dictated": {"minutes": 45}},
            "confirmed": {"window_text": "52 minutes", "minutes": 52},
        }
        empty = {"psychotherapy": {"psychotherapy_time": ""}}
        dictated = {"psychotherapy": {"psychotherapy_time": "45 minutes"}}

        filled = apply_confirmed_window(empty, window)
        assert filled is not None
        assert filled["psychotherapy"]["psychotherapy_time"] == "52 minutes"
        assert apply_confirmed_window(dictated, window) is dictated

    @pytest.mark.parametrize("draft", ["drafted", "redrafted"])
    def test_the_window_completes_a_draft_that_states_only_the_minutes(self, draft: str) -> None:
        # Drafted by the model: the clinician dictated 18 minutes and no clock times.
        content = {"psychotherapy": {**_CAPTURED[draft]["psychotherapy"]}}
        content["psychotherapy"]["psychotherapy_time"] = "18 minutes"
        window = {"proposal": {"dictated": {"minutes": 18}}, "confirmed": _CAPTURED["confirmed"]}

        filled = apply_confirmed_window(content, window)

        assert not disagrees(content, window)
        assert filled is not None
        assert filled["psychotherapy"]["psychotherapy_time"] == "3:08 PM to 3:26 PM, 18 minutes"

    @pytest.mark.parametrize(
        "dictated",
        [
            DictatedTime(start="3:10 PM", minutes=18),
            DictatedTime(start="3 pm", end="3:18", minutes=18),
            DictatedTime(minutes=25),
        ],
    )
    def test_a_dictated_time_the_window_would_change_is_kept(self, dictated: DictatedTime) -> None:
        window = {"proposal": {"dictated": dictated.to_dict()}, "confirmed": _CAPTURED["confirmed"]}
        content = {"psychotherapy": {"psychotherapy_time": dictated_time_text(dictated)}}
        assert apply_confirmed_window(content, window) is content

    def test_matching_minutes_the_clinician_chose_to_keep_stay(self) -> None:
        content = {"psychotherapy": {"psychotherapy_time": "18 minutes"}}
        window = {
            "proposal": {"dictated": {"minutes": 18}},
            "confirmed": {**_CAPTURED["confirmed"], "keep_dictated": True},
        }
        assert apply_confirmed_window(content, window) is content


def _dictation(session: TherapySession, status: str, seconds: int | None) -> SessionDictation:
    return SessionDictation(
        id=str(uuid.uuid4()),
        session_id=session.id,
        note_id="n1",
        patient_id=session.patient_id,
        author_user_id="u1",
        audio_path="dictations/clip.webm",
        content_type="audio/webm",
        status=cast("DictationStatus", status),
        created_at=_STARTED,
        duration_seconds=seconds,
    )


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

    def test_time_with_documentation_adds_transcribed_dictations(self) -> None:
        session = _session()
        note = _note(session)
        note.content = {"plan": {"follow_up": "Four weeks."}}
        dictations = [
            _dictation(session, "transcribed", 150),
            _dictation(session, "transcribed", None),
            _dictation(session, "failed", 600),
        ]

        times = build_visit_times(session, note, None, dictations)

        # 67 minutes recorded plus 2.5 dictated: 69 whole minutes.
        assert times.total_minutes == 67
        assert times.total_with_documentation_minutes == 69

    def test_dictations_never_add_time_beside_psychotherapy(self) -> None:
        session = _session()
        times = build_visit_times(
            session, _note(session), None, [_dictation(session, "transcribed", 600)]
        )

        assert times.total_with_documentation_minutes is None

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
        assert saved.content is not None
        assert (
            saved.content["psychotherapy"]["psychotherapy_time"]
            == "11:12 AM to 12:04 PM, 52 minutes"
        )

    def test_confirming_is_not_an_edit_and_a_redraft_does_not_ask_about_edits(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        note = notes.add(_note(session))

        saved = _confirm(session, note, notes, start_seconds=750.0)

        assert saved.content_edited is None
        assert not has_edits(saved)
        # What the redraft writes still gets the window (see the redraft tests below).

    def test_on_an_edited_note_the_window_goes_into_the_edits_it_shows(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        note = notes.add(_note(session))
        edits = {"psychotherapy": {"psychotherapy_time": "", "interventions": "Mine."}}
        edited = NoteService(notes).update_note_edits(note.id, edits, "u1")

        saved = _confirm(session, edited, notes, start_seconds=750.0)

        assert saved.content_edited is not None
        assert saved.content_edited["psychotherapy"] == {
            "psychotherapy_time": "11:12 AM to 12:04 PM, 52 minutes",
            "interventions": "Mine.",
        }

    def test_typed_minutes_are_kept_without_clock_times(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        note = notes.add(_note(session))

        saved = _confirm(session, note, notes, minutes=40)

        assert saved.content is not None
        assert saved.content["psychotherapy"]["psychotherapy_time"] == "40 minutes"

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
        note = notes.add(_note(session, dictated=DictatedTime("11:15", "12:00", 45)))

        saved = _confirm(session, note, notes, start_seconds=750.0)
        times = build_visit_times(session, saved, None)
        assert times.psychotherapy is not None
        assert times.psychotherapy.disagrees
        assert times.psychotherapy.dictated_time == "11:15 to 12:00, 45 minutes"

        chosen = _confirm(session, saved, notes, start_seconds=750.0, resolution="use_confirmed")
        assert chosen.content is not None
        assert chosen.content["psychotherapy"]["psychotherapy_time"].endswith("52 minutes")
        resolved = build_visit_times(session, chosen, None).psychotherapy
        assert resolved is not None
        assert not resolved.disagrees

    def test_choosing_the_window_over_a_dictated_time_survives_a_redraft(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        dictated = DictatedTime("11:15", "12:00", 45)
        note = notes.add(_note(session, dictated=dictated))
        chosen = _confirm(session, note, notes, start_seconds=750.0, resolution="use_confirmed")

        # The redraft drafts the dictated time again; the clinician's choice holds.
        redrafted = NoteService(notes).complete_redraft(
            chosen,
            content={"psychotherapy": {"psychotherapy_time": dictated_time_text(dictated)}},
            content_edited=None,
            note_type_version=None,
            user_id="u1",
            psychotherapy_proposal={"dictated": dictated.to_dict()},
        )

        assert redrafted.content is not None
        assert redrafted.content["psychotherapy"]["psychotherapy_time"].endswith("52 minutes")
        shown = build_visit_times(session, redrafted, None).psychotherapy
        assert shown is not None
        assert not shown.disagrees

    def test_dictated_minutes_that_match_are_completed_on_confirm_and_on_redraft(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        stated = DictatedTime(minutes=52)
        note = notes.add(_note(session, dictated=stated))

        saved = _confirm(session, note, notes, start_seconds=750.0)
        window = "11:12 AM to 12:04 PM, 52 minutes"
        assert saved.content is not None
        assert saved.content["psychotherapy"]["psychotherapy_time"] == window
        shown = build_visit_times(session, saved, None).psychotherapy
        assert shown is not None
        assert not shown.disagrees

        redrafted = NoteService(notes).complete_redraft(
            saved,
            content={"psychotherapy": {"psychotherapy_time": dictated_time_text(stated)}},
            content_edited=None,
            note_type_version=None,
            user_id="u1",
            psychotherapy_proposal={"dictated": stated.to_dict()},
        )
        assert redrafted.content is not None
        assert redrafted.content["psychotherapy"]["psychotherapy_time"] == window

    def test_keeping_the_dictated_time_settles_the_disagreement(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        note = notes.add(_note(session, dictated=DictatedTime(minutes=45)))

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
            content={"psychotherapy": {"psychotherapy_time": "", "interventions": ""}},
            user_id="u1",
            psychotherapy_proposal={"dictated": None, "labels": []},
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
            psychotherapy_proposal={"labels": _CONTIGUOUS},
        )

        assert redrafted.psychotherapy_window is not None
        assert redrafted.psychotherapy_window["confirmed"]["minutes"] == 40
        assert redrafted.psychotherapy_window["proposal"]["labels"] == _CONTIGUOUS
        assert redrafted.content is not None
        assert redrafted.content["psychotherapy"]["psychotherapy_time"] == "40 minutes"

    def test_labels_in_one_run_write_a_window(self, notes: InMemoryNotesRepository) -> None:
        session = _session()
        note = notes.add(_note(session))

        saved = _confirm(session, note, notes, labels=_CONTIGUOUS)

        assert saved.content is not None
        assert (
            saved.content["psychotherapy"]["psychotherapy_time"]
            == "11:12 AM to 12:04 PM, 52 minutes"
        )
        assert saved.psychotherapy_window is not None
        assert saved.psychotherapy_window["confirmed"]["contiguous"] is True

    def test_interleaved_labels_write_the_minutes_and_say_so(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        note = notes.add(_note(session, labels=_CONTIGUOUS))

        saved = _confirm(session, note, notes, labels=_INTERLEAVED)

        assert saved.content is not None
        assert saved.content["psychotherapy"]["psychotherapy_time"] == interleaved_text(26)
        times = build_visit_times(session, saved, None).psychotherapy
        assert times is not None
        assert times.confirmed_minutes == 26
        assert times.contiguous is False
        # The confirmed labels, not the proposed ones, are what the timeline shows.
        assert [t.label for t in times.turns] == [i["label"] for i in _INTERLEAVED]
        assert [r.label for r in times.runs] == [
            "admin",
            "therapy",
            "medication_management",
            "therapy",
            "screening_risk",
        ]

    def test_relabeling_replaces_the_time_confirmed_before(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        first = _confirm(session, notes.add(_note(session)), notes, labels=_CONTIGUOUS)

        again = _confirm(session, first, notes, labels=_INTERLEAVED)

        assert again.content is not None
        assert again.content["psychotherapy"]["psychotherapy_time"] == interleaved_text(26)
        shown = build_visit_times(session, again, None).psychotherapy
        assert shown is not None
        assert not shown.disagrees

    def test_no_therapy_turns_confirm_zero_minutes(self, notes: InMemoryNotesRepository) -> None:
        session = _session()
        labels = [{**item, "label": "medication_management"} for item in _CONTIGUOUS]

        saved = _confirm(session, notes.add(_note(session)), notes, labels=labels)

        assert saved.psychotherapy_window is not None
        assert saved.psychotherapy_window["confirmed"]["minutes"] == 0

    def test_dictated_minutes_win_over_the_labels(self, notes: InMemoryNotesRepository) -> None:
        session = _session()
        note = notes.add(_note(session, dictated=DictatedTime(minutes=22), labels=_INTERLEAVED))

        shown = build_visit_times(session, note, None).psychotherapy
        assert shown is not None
        assert shown.labeled_minutes == 26
        assert shown.dictated is not None
        assert shown.dictated.minutes == 22

        saved = _confirm(session, note, notes, labels=_INTERLEAVED)
        assert saved.content is not None
        assert saved.content["psychotherapy"]["psychotherapy_time"] == "22 minutes"
        after = build_visit_times(session, saved, None).psychotherapy
        assert after is not None
        assert after.disagrees

        # "Use my minutes": the dictated count stays and nothing is left to settle.
        kept = _confirm(session, saved, notes, minutes=22, resolution="keep_dictated")
        assert kept.content is not None
        assert kept.content["psychotherapy"]["psychotherapy_time"] == "22 minutes"
        settled = build_visit_times(session, kept, None).psychotherapy
        assert settled is not None
        assert not settled.disagrees
        assert settled.confirmed_minutes == 22

    def test_a_label_on_a_turn_after_the_client_left_is_rejected(
        self, notes: InMemoryNotesRepository
    ) -> None:
        session = _session()
        note = notes.add(_note(session))

        with pytest.raises(UnprocessableEntityError):
            _confirm(session, note, notes, labels=[{"seconds": 3960.0, "label": "therapy"}])

    def test_a_request_gives_exactly_one_shape(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            ConfirmPsychotherapyWindowRequest(minutes=10, labels=[])


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


class TestDraftProposesTheTime:
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

    def test_the_draft_returns_the_dictated_time_and_the_turns_are_labeled(self) -> None:
        drafted = {
            "plan": {"follow_up": "Four weeks."},
            "psychotherapy": {
                "psychotherapy_time": "Psychotherapy 41 minutes, as the clinician said.",
                "modality_interventions": "Cognitive restructuring of the replayed argument.",
            },
            "psychotherapy_time_stated": {
                "start": "",
                "end": "",
                "minutes": 41,
                "as_dictated": "forty-one minutes of therapy",
            },
        }
        labeled = {
            "runs": [
                {"first_segment": 0, "last_segment": 2, "label": "medication_management"},
                {"first_segment": 3, "last_segment": 5, "label": "therapy"},
            ],
            "cue_segment": 3,
        }

        result, gateway = self._draft(drafted, labeled)

        assert "psychotherapy_time_stated" in gateway.calls[0]["response_schema"]["properties"]
        assert "psychotherapy_time_stated" not in result.content
        # The field is rendered from the parts, not the model's own sentence.
        assert result.content["psychotherapy"]["psychotherapy_time"] == "41 minutes"
        # Only the six turns before the client left are labeled; the addendum never is.
        assert "[S5]" in gateway.calls[1]["user_prompt"]
        assert "[S6]" not in gateway.calls[1]["user_prompt"]
        assert "Addendum" not in gateway.calls[1]["user_prompt"]
        assert result.psychotherapy_proposal == {
            "dictated": {
                "start": None,
                "end": None,
                "minutes": 41,
                "as_dictated": "forty-one minutes of therapy",
            },
            "labels": _CONTIGUOUS,
            "cue_seconds": 750.0,
        }

    def test_nothing_stated_and_a_failed_labeling_propose_nothing(self) -> None:
        drafted = {
            "psychotherapy": {"psychotherapy_time": "Not stated.", "modality_interventions": ""},
            "psychotherapy_time_stated": {"start": "", "end": "", "as_dictated": ""},
        }

        result, _gateway = self._draft(drafted)

        assert result.psychotherapy_proposal is None
        assert result.content["psychotherapy"]["psychotherapy_time"] == ""


@pytest.fixture
def appointments() -> Iterator[InMemoryAppointmentRepository]:
    repo = InMemoryAppointmentRepository()
    app.dependency_overrides[get_appointment_repository] = lambda: repo
    app.dependency_overrides[get_dictation_repository] = InMemorySessionDictationRepository
    yield repo
    app.dependency_overrides.pop(get_appointment_repository, None)
    app.dependency_overrides.pop(get_dictation_repository, None)


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
