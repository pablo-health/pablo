# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where the client left the recording, and the clinician's addendum after it."""

from __future__ import annotations

import types
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.models import Patient, Transcript
from app.models.session import SessionResponse, TherapySession
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.client_present import (
    DICTATED_HEADING,
    MIN_BOUNDARY_CLIENT_WORDS,
    TimedSegment,
    client_present_end,
    is_call,
    segments_from_transcript,
    segments_from_utterances,
    split_at_boundary,
)
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.routes import internal_transcription as it
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.session_service import _client_present_end
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion

# A 36-minute session: the client's last line ends at 36:00, then the
# clinician dictates for three more minutes. Measured to the end of the
# recording it would read 39 minutes, over the 38-minute line.
_SESSION_WITH_TAIL = "\n".join(
    [
        "[00:00:05]",
        "Therapist: Good to see you. How has the week been?",
        "[00:10:00]",
        "Client: The new dose has helped me sleep through the night.",
        "[00:35:56]",
        "Client: Thank you, see you next month.",
        "[00:36:30]",
        "Therapist: Addendum. Client denies suicidal ideation, intent or plan.",
        "[00:38:00]",
        "Therapist: Mood euthymic, thought process linear. Plan continues as discussed.",
        "[00:39:00]",
        "Therapist: Ending the recording now, thank you all for today.",
    ]
)

_DICTATION_ONLY = "\n".join(
    [
        "[00:00:02]",
        "Therapist: Dictating for the visit earlier today. No risk concerns stated.",
        "[00:01:10]",
        "Therapist: Continue current medication and follow up in four weeks.",
    ]
)


def _utterance(speaker: str, start: float, end: float, text: str) -> dict[str, Any]:
    return {"speaker": speaker, "start": start, "end": end, "text": text}


class TestBoundary:
    def test_client_present_end_is_the_clients_last_turn_not_the_recording_end(self) -> None:
        segments = segments_from_utterances(
            [
                _utterance("Therapist", 5, 9, "Good to see you. How has the week been?"),
                _utterance("Client", 600, 610, "The new dose has helped me sleep through."),
                _utterance("Client", 2156, 2160, "Thank you, see you next month."),
                _utterance("Therapist", 2190, 2340, "Addendum. Client denies suicidal ideation."),
            ]
        )

        assert client_present_end(segments, client_channel_expected=True) == 2160.0

    def test_trailing_client_noise_below_the_threshold_is_ignored(self) -> None:
        segments = segments_from_utterances(
            [
                _utterance("Client", 2156, 2160, "Thank you, see you next month."),
                _utterance("Therapist", 2190, 2340, "Addendum. Long dictation follows here."),
                _utterance("Client", 2300, 2300.4, "Okay."),
                _utterance("Client A", 2320, 2320.5, "Mm hmm"),
            ]
        )

        assert MIN_BOUNDARY_CLIENT_WORDS == 3
        assert client_present_end(segments, client_channel_expected=True) == 2160.0

    def test_a_client_turn_at_the_threshold_counts(self) -> None:
        segments = [
            TimedSegment("Client", "Bye for now", 100.0, 101.5),
            TimedSegment("Therapist", "Dictating.", 110.0, 112.0),
        ]

        assert client_present_end(segments, client_channel_expected=True) == 101.5

    def test_a_diarized_client_channel_still_marks_the_client(self) -> None:
        segments = [TimedSegment("Client B", "I will call the pharmacy tomorrow", 50.0, 54.0)]

        assert client_present_end(segments, client_channel_expected=True) == 54.0

    def test_dictation_only_call_has_no_client_present_time(self) -> None:
        segments = segments_from_transcript(
            Transcript(format="google_meet", content=_DICTATION_ONLY)
        )

        assert client_present_end(segments, client_channel_expected=True) == 0.0

    def test_only_noise_on_the_client_channel_is_still_a_dictation(self) -> None:
        segments = [
            TimedSegment("Therapist", "Dictating the visit.", 0.0, 10.0),
            TimedSegment("Client", "Okay.", 5.0, 5.4),
        ]

        assert client_present_end(segments, client_channel_expected=False) == 0.0

    def test_in_person_with_no_client_channel_is_unknown(self) -> None:
        # In person both voices land on the one microphone: no client turn
        # does not mean no client.
        segments = segments_from_transcript(
            Transcript(format="google_meet", content=_DICTATION_ONLY)
        )

        assert client_present_end(segments, client_channel_expected=False) is None

    def test_stored_text_estimates_a_turns_end_from_its_length(self) -> None:
        segments = segments_from_transcript(
            Transcript(format="google_meet", content=_SESSION_WITH_TAIL)
        )
        # "Thank you, see you next month." is six words: 2.4 s at 2.5 words/s.
        assert client_present_end(segments, client_channel_expected=True) == pytest.approx(
            2156 + 2.4
        )

    def test_an_estimated_end_never_runs_past_the_next_turn(self) -> None:
        content = "[00:00:10] Client: one two three four five six seven eight nine ten\n" + (
            "[00:00:11] Therapist: Thanks."
        )
        segments = segments_from_transcript(Transcript(format="txt", content=content))

        assert segments[0].end == 11.0

    def test_only_a_video_platform_is_a_call(self) -> None:
        assert is_call("zoom")
        assert not is_call(None)
        assert not is_call("none")

    def test_unparseable_transcript_has_no_segments(self) -> None:
        assert segments_from_transcript(Transcript(format="pdf", content="anything")) == []


class TestSplit:
    def test_the_addendum_is_the_clinicians_turns_after_the_boundary(self) -> None:
        segments = segments_from_utterances(
            [
                _utterance("Therapist", 5, 9, "How has the week been?"),
                _utterance("Client", 600, 610, "Better, thank you so much."),
                _utterance("Therapist", 700, 720, "Addendum: denies suicidal ideation."),
                _utterance("Client", 710, 710.3, "Okay."),
            ]
        )

        split = split_at_boundary(segments, 610.0)

        assert split.session_lines == (
            "[00:05] Therapist: How has the week been?\n[10:00] Client: Better, thank you so much."
        )
        assert split.addendum_lines == "[11:40] Therapist: Addendum: denies suicidal ideation."

    def test_a_dictation_is_all_addendum(self) -> None:
        segments = segments_from_transcript(
            Transcript(format="google_meet", content=_DICTATION_ONLY)
        )

        split = split_at_boundary(segments, 0.0)

        assert split.session_lines == ""
        assert split.addendum_lines.count("Therapist:") == 2


def _session(content: str, *, video_platform: str | None, boundary: float | None = None) -> Any:
    return TherapySession(
        id="s1",
        user_id="u1",
        patient_id="p1",
        session_date=datetime.fromisoformat("2026-10-06T15:00:00+00:00"),
        session_number=1,
        status="pending_review",
        transcript=Transcript(format="google_meet", content=content),
        created_at=datetime.fromisoformat("2026-10-06T15:00:00+00:00"),
        video_platform=video_platform,
        client_present_end_seconds=boundary,
    )


class TestSessionBoundary:
    def test_a_call_with_a_three_minute_tail_ends_client_time_at_the_clients_last_line(
        self,
    ) -> None:
        boundary = _client_present_end(_session(_SESSION_WITH_TAIL, video_platform="zoom"))

        assert boundary is not None
        # Minutes run to the client's last line (35:58), not to 39:00.
        assert int(boundary // 60) == 35

    def test_the_response_reports_the_boundary_and_the_addendum_length(self) -> None:
        session = _session(_SESSION_WITH_TAIL, video_platform="zoom", boundary=2158.4)

        response = SessionResponse.from_session(session, "Client Name")

        assert response.client_present_end_seconds == 2158.4
        # The last dictated line starts at 39:00 and runs nine words.
        assert response.clinician_addendum_seconds == pytest.approx(2340 + 9 / 2.5 - 2158.4)

    def test_a_session_from_before_the_boundary_existed_reports_none(self) -> None:
        response = SessionResponse.from_session(
            _session(_SESSION_WITH_TAIL, video_platform="zoom"), "Client Name"
        )

        assert response.client_present_end_seconds is None
        assert response.clinician_addendum_seconds is None


class TestPollMeasuresFromProviderTimings:
    def test_the_poll_hands_the_boundary_from_utterance_ends(self) -> None:
        jobs = [
            {
                "transcript_id": "t1",
                "speaker": "Therapist",
                "utterances": [
                    _utterance("Therapist", 5, 9, "How has the week been?"),
                    _utterance("Therapist", 2190, 2340, "Addendum: denies suicidal ideation."),
                ],
            },
            {
                "transcript_id": "t2",
                "speaker": "Client",
                "utterances": [_utterance("Client", 2150, 2161.5, "Thank you, see you soon.")],
            },
        ]
        session_row = types.SimpleNamespace(
            video_platform="zoom",
            transcription_job_metadata={"provider": "assemblyai", "jobs": jobs},
            audio_gcs_path="audio/s1/therapist.pcm,audio/s1/client.pcm",
            status="transcribing",
            error=None,
        )
        cm = _standalone_db(session_row)
        settings = types.SimpleNamespace(
            transcription_task_queue="q",
            assemblyai_api_key=types.SimpleNamespace(get_secret_value=lambda: "key"),
        )

        with (
            patch.object(it, "_resolve_schema_for_user", return_value=None),
            patch.object(it, "create_standalone_session", return_value=cm),
            patch.object(it, "get_settings", return_value=settings),
            patch.object(
                it, "process_transcription_result", return_value={"status": "processing"}
            ) as mock_process,
            patch.object(it, "_delete_staged_speech_objects"),
            patch.object(it, "_delete_completed_jobs"),
        ):
            it.transcription_poll(
                it.TranscriptionPollRequest(session_id="s1", user_id="u1"), _invoker=None
            )

        assert mock_process.call_args.kwargs["client_present_end_seconds"] == 2161.5


def _standalone_db(session_row: object) -> MagicMock:
    """A ``create_standalone_session`` stand-in whose one query returns ``session_row``."""
    db = MagicMock()
    db.execute.return_value.scalars.return_value.first.return_value = session_row
    cm = MagicMock()
    cm.__enter__.return_value = db
    cm.__exit__.return_value = False
    return cm


_FOLLOW_UP = PracticeNoteTypeSpec.model_validate(
    {
        "label": "Follow-up",
        "system_prompt": "Draft the visit.",
        "user_template": "{fields}\n\nTranscript:\n{transcript}",
        "sections": [
            {
                "key": "assessment",
                "label": "Assessment",
                "fields": [{"key": "impression", "label": "Impression", "ai_hint": "Summarize."}],
            },
            {
                "key": "psychotherapy",
                "label": "Psychotherapy",
                "fields": [{"key": "interventions", "label": "Interventions"}],
            },
        ],
    }
)


class TestGenerationReceivesTheAddendum:
    @pytest.fixture
    def patient(self) -> Patient:
        return Patient(
            id="p1",
            first_name="Jane",
            last_name="Doe",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        )

    def _generate(
        self, patient: Patient, content: str, boundary: float | None
    ) -> tuple[FakeStructuredLLMGateway, dict[str, Any]]:
        registry = NoteTypeRegistry()
        register_builtin_note_types(registry)
        gateway = FakeStructuredLLMGateway(
            responses=[
                StructuredCompletion(
                    data={
                        "assessment": {"impression": "x"},
                        "psychotherapy": {"interventions": "should not survive"},
                    }
                )
            ]
        )
        definition = to_definition("custom.follow_up", 1, _FOLLOW_UP)
        result = RegistryNoteGenerationService(
            registry=registry, llm_gateway=gateway
        ).generate_note(
            definition.key,
            Transcript(format="google_meet", content=content),
            patient,
            datetime.fromisoformat("2026-10-06T15:00:00+00:00"),
            definition=definition,
            client_present_end_seconds=boundary,
        )
        return gateway, result.content

    def test_the_tail_reaches_the_model_as_a_separate_addendum(self, patient: Patient) -> None:
        gateway, _content = self._generate(patient, _SESSION_WITH_TAIL, 2158.4)

        prompt = gateway.calls[0]["user_prompt"]
        transcript_part, addendum_part = prompt.split("Clinician addendum:", 1)
        assert "The new dose has helped" in transcript_part
        assert "denies suicidal ideation" not in transcript_part
        assert "client was not present" in addendum_part
        assert "Client denies suicidal ideation, intent or plan." in addendum_part
        assert "Clinician stated:" in addendum_part
        assert "Not stated." in addendum_part

    def test_a_dictation_is_drafted_from_the_addendum_with_no_psychotherapy(
        self, patient: Patient
    ) -> None:
        gateway, content = self._generate(patient, _DICTATION_ONLY, 0.0)

        call = gateway.calls[0]
        assert "psychotherapy" not in call["response_schema"]["properties"]
        assert "Section 'psychotherapy'" not in call["user_prompt"]
        assert "No risk concerns stated." in call["user_prompt"].split("Clinician addendum:")[1]
        assert "client was not present in this recording" in call["user_prompt"]
        assert content["psychotherapy"] == {"interventions": ""}

    def test_later_dictations_join_the_addendum_and_never_the_session(
        self, patient: Patient
    ) -> None:
        dictated = "[00:00:03] Therapist: PDMP checked today, no concerns."
        content = f"{_SESSION_WITH_TAIL}\n\n{DICTATED_HEADING}\n\n{dictated}"

        gateway, _content = self._generate(patient, content, 2158.4)

        transcript_part, addendum_part = gateway.calls[0]["user_prompt"].split(
            "Clinician addendum:", 1
        )
        assert "PDMP checked" not in transcript_part
        assert f"{DICTATED_HEADING}\n{dictated}" in addendum_part
        assert "Client denies suicidal ideation" in addendum_part

    def test_an_unknown_boundary_leaves_the_prompt_as_it_was(self, patient: Patient) -> None:
        gateway, _content = self._generate(patient, _SESSION_WITH_TAIL, None)

        prompt = gateway.calls[0]["user_prompt"]
        assert "Clinician addendum:" not in prompt
        assert "Client denies suicidal ideation" in prompt
