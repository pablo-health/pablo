# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A recorded visit's times, and confirming its psychotherapy time.

The visit's start and end are the call's own times when the telehealth
platform reported them, otherwise the recording's. Psychotherapy minutes are
counted on the recording, as the sum of the client-present turns labeled
therapy (:mod:`app.notes.visit_times`), and can never exceed the span the
client was present (:mod:`app.notes.client_present`).
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..api_errors import BadRequestError, NotFoundError, UnprocessableEntityError
from ..models.visit_times import (
    DictatedTimeResponse,
    PsychotherapyWindowResponse,
    RecordingTurn,
    TurnRun,
    VisitTimesResponse,
)
from ..notes import is_practice_key
from ..notes.client_present import recording_end, segments_from_transcript
from ..notes.visit_times import (
    PSYCHOTHERAPY_SECTION_KEY,
    TurnLabel,
    client_present_turns,
    disagrees,
    drafted_time,
    interleaved_text,
    labels_from_stored,
    labels_to_stored,
    layout,
    proposed_dictation,
    therapy_minutes,
    therapy_runs,
    turn_spans,
    window_minutes,
    window_text,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from ..models.note import Note
    from ..models.session import TherapySession
    from ..models.session_dictation import SessionDictation
    from ..models.visit_times import ConfirmPsychotherapyWindowRequest
    from ..scheduling_engine.models.appointment import Appointment
    from .note_service import NoteService

_SECONDS_PER_MINUTE = 60


def _has_psychotherapy(note: Note | None) -> bool:
    return note is not None and PSYCHOTHERAPY_SECTION_KEY in (note.content or {})


def _visit_bounds(
    session: TherapySession, appointment: Appointment | None
) -> tuple[datetime | None, datetime | None]:
    if appointment and appointment.telehealth_started_at and appointment.telehealth_ended_at:
        return appointment.telehealth_started_at, appointment.telehealth_ended_at
    return session.started_at, session.ended_at


def _client_present_span(session: TherapySession) -> float | None:
    """Where the client-present span ends, in seconds into the recording.

    The client-present boundary when measured; otherwise the whole recording.
    """
    if session.client_present_end_seconds is not None:
        return session.client_present_end_seconds
    if session.started_at and session.ended_at:
        return (session.ended_at - session.started_at).total_seconds()
    end = recording_end(segments_from_transcript(session.transcript))
    return end or None


def _labels(window: dict[str, Any]) -> dict[float, TurnLabel]:
    """The confirmed labels, else the proposed ones."""
    confirmed = window.get("confirmed") or {}
    if confirmed.get("labels") is not None:
        return labels_from_stored(confirmed["labels"])
    return labels_from_stored((window.get("proposal") or {}).get("labels"))


def _psychotherapy(session: TherapySession, note: Note) -> PsychotherapyWindowResponse:
    if session.client_present_end_seconds == 0:
        return PsychotherapyWindowResponse(offered=False)
    window = note.psychotherapy_window or {}
    proposal = window.get("proposal") or {}
    confirmed = window.get("confirmed") or {}
    end = _client_present_span(session)
    turns = client_present_turns(
        segments_from_transcript(session.transcript), session.client_present_end_seconds
    )
    labels = _labels(window)
    spans = dict(turn_spans(turns, end)) if end is not None else {}
    content = note.content_edited or note.content
    stated = proposed_dictation(window)
    return PsychotherapyWindowResponse(
        offered=True,
        end_seconds=end,
        turns=[
            RecordingTurn(
                seconds=t.start,
                end_seconds=spans.get(t.start, t.end),
                speaker=t.speaker,
                text=t.text,
                label=labels.get(t.start),
            )
            for t in turns
        ],
        runs=[
            TurnRun(label=r.label, start_seconds=r.start, end_seconds=r.end)
            for r in (layout(labels, turns, end) if end is not None else [])
        ],
        labeled_minutes=therapy_minutes(labels, turns, end) if end is not None and labels else None,
        cue_seconds=proposal.get("cue_seconds"),
        dictated=DictatedTimeResponse(**stated.to_dict()) if stated else None,
        confirmed_start_seconds=confirmed.get("start_seconds"),
        confirmed_minutes=confirmed.get("minutes"),
        contiguous=confirmed.get("contiguous"),
        labels_confirmed=confirmed.get("labels") is not None,
        window_text=confirmed.get("window_text"),
        dictated_time=drafted_time(content, window),
        disagrees=disagrees(content, window),
    )


def _dictated_seconds(dictations: Sequence[SessionDictation]) -> int:
    """Time spent dictating for the note after the recording stopped.

    Only clips that were transcribed count; a failed clip was never used. A
    clip the recorder sent no duration for counts as nothing.
    """
    return sum(d.duration_seconds or 0 for d in dictations if d.status == "transcribed")


def build_visit_times(
    session: TherapySession,
    note: Note | None,
    appointment: Appointment | None,
    dictations: Sequence[SessionDictation] = (),
) -> VisitTimesResponse:
    started_at, ended_at = _visit_bounds(session, appointment)
    visit_seconds = (ended_at - started_at).total_seconds() if started_at and ended_at else None
    total_minutes = (
        math.floor(visit_seconds / _SECONDS_PER_MINUTE) if visit_seconds is not None else None
    )
    boundary = session.client_present_end_seconds
    addendum = (
        max(recording_end(segments_from_transcript(session.transcript)) - boundary, 0.0)
        if boundary is not None
        else None
    )
    # Total time with documentation counts only for a visit chosen by time,
    # which a visit with a psychotherapy add-on never is. Built-in types are
    # not visit notes, so they never show it either.
    by_time = (
        visit_seconds is not None
        and note is not None
        and is_practice_key(note.note_type)
        and not _has_psychotherapy(note)
    )
    with_documentation = (
        math.floor((visit_seconds + _dictated_seconds(dictations)) / _SECONDS_PER_MINUTE)
        if by_time and visit_seconds is not None
        else None
    )
    return VisitTimesResponse(
        started_at=started_at,
        ended_at=ended_at,
        total_minutes=total_minutes,
        recording_started_at=session.started_at,
        client_present_end_seconds=boundary,
        clinician_addendum_seconds=addendum,
        psychotherapy=_psychotherapy(session, note) if note and _has_psychotherapy(note) else None,
        total_with_documentation_minutes=with_documentation,
    )


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnprocessableEntityError(f"Unknown time zone {name!r}", {"time_zone": name}) from exc


def _confirm_labels(
    session: TherapySession,
    request: ConfirmPsychotherapyWindowRequest,
    end: float,
    zone: ZoneInfo,
) -> dict[str, Any]:
    """The labels as confirmed: their therapy minutes, as a window when they are one run."""
    turns = client_present_turns(
        segments_from_transcript(session.transcript), session.client_present_end_seconds
    )
    starts = {t.start for t in turns}
    labels: dict[float, TurnLabel] = {}
    for item in request.labels or []:
        if item.seconds not in starts:
            raise UnprocessableEntityError(
                "A label names a turn that is not on the recording while the client was present",
                {"seconds": item.seconds},
            )
        labels[item.seconds] = item.label
    minutes = therapy_minutes(labels, turns, end)
    runs = therapy_runs(layout(labels, turns, end))
    began = session.started_at
    if len(runs) == 1 and began is not None:
        text = window_text(
            minutes,
            start_at=began + timedelta(seconds=runs[0].start),
            end_at=began + timedelta(seconds=runs[0].end),
            zone=zone,
        )
    elif len(runs) > 1:
        text = interleaved_text(minutes)
    else:
        text = window_text(minutes)
    return {
        "labels": labels_to_stored(labels),
        "start_seconds": runs[0].start if len(runs) == 1 else None,
        "minutes": minutes,
        "contiguous": len(runs) <= 1,
        "window_text": text,
    }


def confirm_psychotherapy_window(
    session: TherapySession,
    note: Note | None,
    request: ConfirmPsychotherapyWindowRequest,
    note_service: NoteService,
    user_id: str,
) -> Note:
    """Validate and store the window the clinician confirmed."""
    if note is None or not _has_psychotherapy(note):
        raise NotFoundError("This note has no psychotherapy section", {"session_id": session.id})
    if session.client_present_end_seconds == 0:
        raise BadRequestError(
            "The client was not on this recording", {"session_id": session.id}, code="NO_CLIENT"
        )
    end = _client_present_span(session)
    if end is None:
        raise BadRequestError(
            "This recording has no times to measure", {"session_id": session.id}, code="NO_TIMES"
        )
    zone = _zone(request.time_zone)
    confirmed: dict[str, Any]
    if request.labels is not None:
        confirmed = _confirm_labels(session, request, end, zone)
    elif request.start_seconds is not None:
        if request.start_seconds >= end:
            raise UnprocessableEntityError(
                "The therapy portion must start before the client left",
                {"start_seconds": request.start_seconds, "end_seconds": end},
            )
        minutes = window_minutes(request.start_seconds, end)
        began = session.started_at
        text = (
            window_text(
                minutes,
                start_at=began + timedelta(seconds=request.start_seconds),
                end_at=began + timedelta(seconds=end),
                zone=zone,
            )
            if began
            else window_text(minutes)
        )
        confirmed = {"start_seconds": request.start_seconds, "minutes": minutes, "contiguous": True}
        confirmed["window_text"] = text
    else:
        minutes = request.minutes or 0
        if minutes * _SECONDS_PER_MINUTE > end:
            raise UnprocessableEntityError(
                "Psychotherapy minutes can't be more than the time the client was present",
                {"minutes": minutes, "max_minutes": math.floor(end / _SECONDS_PER_MINUTE)},
            )
        confirmed = {"start_seconds": None, "minutes": minutes, "window_text": window_text(minutes)}
    confirmed["keep_dictated"] = request.resolution == "keep_dictated"
    # Remembered, so a redraft that brings the dictated time back gets the window again.
    confirmed["use_confirmed"] = request.resolution == "use_confirmed"
    return note_service.confirm_psychotherapy_window(
        note.id,
        confirmed,
        user_id,
        replace_dictated=request.resolution == "use_confirmed",
    )


__all__ = ["build_visit_times", "confirm_psychotherapy_window"]
