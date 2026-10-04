# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""API models for sessions followed from another service's calendar."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, model_validator

# Runtime import: Pydantic resolves this annotation at runtime.
from .scheduling import MAX_SOURCE_IDENTIFIER, SeriesMatchResponse


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
    """One "who is this?": every open event under one identifier, or one event."""

    key: str
    source: str
    source_identifier: str
    title: str
    recurring: bool
    sessions: int = Field(description="How many open events the answer settles")
    next_start_at: datetime
    match: SeriesMatchResponse
    outside_session_id: str | None = Field(
        default=None,
        description=(
            "Set when the question is about this one event: its identifier (initials, a "
            "name two charts share) could mean someone else next time, so each event is "
            "asked. Hand it back with the answer"
        ),
    )
    client_inactive: bool = Field(
        default=False,
        description=(
            "The suggested client's chart is inactive or on hold. Confirming with "
            "``reactivate`` makes it active again; the session books either way"
        ),
    )


class OutsideQuestionsResponse(BaseModel):
    count: int
    questions: list[OutsideQuestionResponse]


class OutsideAnswer(BaseModel):
    """Exactly one of: an existing client, a new client's name, or not a client."""

    source: str = Field(min_length=1, max_length=64)
    source_identifier: str = Field(min_length=1, max_length=MAX_SOURCE_IDENTIFIER)
    patient_id: str | None = None
    new_client_name: str | None = Field(
        default=None,
        max_length=1024,
        description="The calendar's wording, as-is; an empty one still adds a client",
    )
    not_a_client: bool = False
    outside_session_id: str | None = Field(
        default=None,
        max_length=64,
        description="The one event this answers, when the question was about one event",
    )
    reactivate: bool = Field(
        default=False,
        description="Make an inactive client's chart active again while booking",
    )

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


class NotAddedSession(BaseModel):
    """An answered session that wasn't booked: something was already there."""

    outside_session_id: str
    client_name: str
    start_at: datetime


class OutsideAnswerResponse(BaseModel):
    answered: int
    appointments_created: int
    appointments: list[AnsweredAppointment]
    not_added: list[NotAddedSession] = Field(default_factory=list)


class BookedOnItsOwn(BaseModel):
    """A session Pablo booked without asking, because its title named the client."""

    appointment_id: str
    patient_id: str
    client_name: str
    start_at: datetime
    end_at: datetime
    source: str = Field(description="``google_calendar`` or ``ical:<feed>``")


class BookedOnItsOwnResponse(BaseModel):
    """Upcoming ones the clinician hasn't seen yet, soonest first."""

    sessions: list[BookedOnItsOwn]


class BookedOnItsOwnSeenRequest(BaseModel):
    """The bookings the clinician has seen, which leave the list."""

    appointment_ids: list[str] = Field(min_length=1, max_length=500)


class BookedOnItsOwnSeenResponse(BaseModel):
    seen: int


class FollowedCalendarRequest(BaseModel):
    """Which calendar to follow: an id from the calendar list, ``primary`` for
    the main calendar, or None to stop following."""

    calendar_id: str | None = Field(default=None, min_length=1, max_length=1024)


class FollowedCalendarResponse(BaseModel):
    follow_calendar_id: str | None


class ReadableCalendarResponse(BaseModel):
    id: str
    name: str
    primary: bool


class ReadableCalendarsResponse(BaseModel):
    """The calendars the connection can read, the main one first."""

    calendars: list[ReadableCalendarResponse]
    follow_calendar_id: str | None = Field(
        default=None,
        description="The calendar followed now, by the id it appears under in ``calendars``",
    )


class CalendarSyncResponse(BaseModel):
    """What one pass over the caller's calendars did, in counts."""

    ical_sources_synced: int
    ical_errors: int
    google_synced: bool
    google_error: bool
    google_changes_processed: int
    outside_sessions_followed: int
    reminders_sent: int
