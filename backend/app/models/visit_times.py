# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""API models for a recorded visit's times and its psychotherapy time."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from ..notes.visit_times import RunLabel, TurnLabel  # noqa: TC001 — Pydantic needs these at runtime


class RecordingTurn(BaseModel):
    """A client-present transcript turn, and what it was."""

    seconds: float
    #: Where the turn's span ends: the next turn's start, or where the client left.
    end_seconds: float
    speaker: str
    text: str
    #: The confirmed label, else the proposed one; ``None`` is unattributed.
    label: TurnLabel | None = None


class TurnRun(BaseModel):
    """Consecutive turns with one label, for the timeline."""

    label: RunLabel
    start_seconds: float
    end_seconds: float


class DictatedTimeResponse(BaseModel):
    """A psychotherapy time the clinician stated, part by part, as said."""

    start: str | None = None
    end: str | None = None
    minutes: int | None = None
    as_dictated: str = ""


class PsychotherapyWindowResponse(BaseModel):
    """The psychotherapy time of a visit whose note has a section for it."""

    #: False for a dictation-only recording: no client, no therapy time.
    offered: bool
    #: Seconds into the recording the client was last present: the span's end.
    end_seconds: float | None = None
    turns: list[RecordingTurn] = Field(default_factory=list)
    runs: list[TurnRun] = Field(default_factory=list)
    #: The therapy minutes the labels add up to.
    labeled_minutes: int | None = None
    #: The turn where the clinician said aloud the therapy was starting.
    cue_seconds: float | None = None
    #: The time the clinician dictated, as the draft returned it.
    dictated: DictatedTimeResponse | None = None
    confirmed_start_seconds: float | None = None
    confirmed_minutes: int | None = None
    #: The therapy was one contiguous run (or a single window was confirmed).
    contiguous: bool | None = None
    #: The clinician confirmed the turn labels (rather than a start or minutes).
    labels_confirmed: bool = False
    window_text: str | None = None
    #: What the note's psychotherapy time field holds, when the clinician said one.
    dictated_time: str | None = None
    #: The dictated time and the confirmed one disagree; the clinician picks.
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


class TurnLabelRequest(BaseModel):
    seconds: float = Field(ge=0)
    label: TurnLabel


class ConfirmPsychotherapyWindowRequest(BaseModel):
    """The clinician confirms the turn labels, a start on the transcript, or the minutes."""

    labels: list[TurnLabelRequest] | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    minutes: int | None = Field(default=None, ge=0)
    #: The clinician's time zone, for the clock times written into the note.
    time_zone: str = "UTC"
    #: How to settle a time the clinician dictated that disagrees.
    resolution: Literal["use_confirmed", "keep_dictated"] | None = None

    @model_validator(mode="after")
    def _one_way(self) -> Self:
        given = [v for v in (self.labels, self.start_seconds, self.minutes) if v is not None]
        if len(given) != 1:
            raise ValueError("give exactly one of labels, start_seconds or minutes")
        return self
