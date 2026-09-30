# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""API models for sessions followed from another service's calendar."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, model_validator

# Runtime import: Pydantic resolves this annotation at runtime.
from .scheduling import SeriesMatchResponse  # noqa: TC001


class OutsideSessionResponse(BaseModel):
    """An event nobody has said who it is yet, shown as a read-only block."""

    id: str
    source: str
    source_identifier: str = Field(description="Hand back on answer, with source")
    title: str
    start_at: datetime
    end_at: datetime


class OutsideSessionsResponse(BaseModel):
    events: list[OutsideSessionResponse]


class OutsideQuestionResponse(BaseModel):
    """One "who is this?": every open event remembered under one identifier."""

    key: str
    source: str
    source_identifier: str
    title: str
    recurring: bool
    sessions: int = Field(description="How many open events the answer settles")
    next_start_at: datetime
    match: SeriesMatchResponse


class OutsideQuestionsResponse(BaseModel):
    count: int
    questions: list[OutsideQuestionResponse]


class OutsideAnswer(BaseModel):
    """Exactly one of: an existing client, a new client's name, or not a client."""

    source: str = Field(min_length=1, max_length=64)
    source_identifier: str = Field(min_length=1, max_length=255)
    patient_id: str | None = None
    new_client_name: str | None = Field(
        default=None,
        max_length=1024,
        description="The calendar's wording, as-is; an empty one still adds a client",
    )
    not_a_client: bool = False

    @model_validator(mode="after")
    def _one_answer(self) -> Self:
        given = sum([self.patient_id is not None, self.new_client_name is not None])
        if given + self.not_a_client != 1:
            msg = "Answer with a client, a new client, or not a client"
            raise ValueError(msg)
        return self


class OutsideAnswerRequest(BaseModel):
    answers: list[OutsideAnswer] = Field(min_length=1, max_length=200)


class AnsweredAppointment(BaseModel):
    outside_session_id: str
    appointment_id: str


class OutsideAnswerResponse(BaseModel):
    answered: int
    appointments_created: int
    appointments: list[AnsweredAppointment]


class FollowMainCalendarRequest(BaseModel):
    enabled: bool


class FollowMainCalendarResponse(BaseModel):
    follow_main_calendar: bool
