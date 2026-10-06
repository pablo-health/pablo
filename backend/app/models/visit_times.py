# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""API models for a recorded visit's times and its psychotherapy window."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class RecordingTurn(BaseModel):
    """A transcript turn the therapy portion could start on."""

    seconds: float
    speaker: str
    text: str


class StartCandidateResponse(BaseModel):
    seconds: float
    #: ``spoken_cue``: the clinician said so at that turn; ``marked``: the
    #: draft marked it; ``attributed``: the drafted interventions cite it.
    source: Literal["spoken_cue", "marked", "attributed"]


class PsychotherapyWindowResponse(BaseModel):
    """The psychotherapy portion of a visit whose note has a section for it."""

    #: False for a dictation-only recording: no client, no therapy time.
    offered: bool
    #: Seconds into the recording the client was last present: the window's end.
    end_seconds: float | None = None
    turns: list[RecordingTurn] = Field(default_factory=list)
    candidates: list[StartCandidateResponse] = Field(default_factory=list)
    #: A start time the clinician stated aloud, exactly as said.
    stated_clock_time: str | None = None
    confirmed_start_seconds: float | None = None
    confirmed_minutes: int | None = None
    window_text: str | None = None
    #: What the note's psychotherapy time field holds, when the clinician said one.
    dictated_time: str | None = None
    #: The dictated time and the confirmed window disagree; the clinician picks.
    disagrees: bool = False


class VisitTimesResponse(BaseModel):
    """Start, end and minutes of a recorded visit, for the note header."""

    started_at: datetime | None = None
    ended_at: datetime | None = None
    total_minutes: int | None = None
    #: When the recording began: every offset in seconds counts from here.
    recording_started_at: datetime | None = None
    client_present_end_seconds: float | None = None
    clinician_addendum_seconds: float | None = None
    psychotherapy: PsychotherapyWindowResponse | None = None
    #: Total time on the date, documentation included. Only for a note
    #: without a psychotherapy section: with an add-on, the visit is not
    #: chosen by time.
    total_with_documentation_minutes: int | None = None


class ConfirmPsychotherapyWindowRequest(BaseModel):
    """The clinician confirms the start on the transcript, or types the minutes."""

    start_seconds: float | None = Field(default=None, ge=0)
    minutes: int | None = Field(default=None, ge=0)
    #: The clinician's time zone, for the clock times written into the note.
    time_zone: str = "UTC"
    #: How to settle a time the clinician dictated that disagrees.
    resolution: Literal["use_confirmed", "keep_dictated"] | None = None

    @model_validator(mode="after")
    def _one_way(self) -> Self:
        if (self.start_seconds is None) == (self.minutes is None):
            raise ValueError("give either start_seconds or minutes")
        return self
