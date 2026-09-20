# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for secure patient messaging.

One set of response shapes serves both surfaces, because both surfaces
disclose the same thing: a thread and the messages in it. What differs is
who is allowed to ask, and that is settled by the dependency on the route,
not by the shape of the answer.

Nothing here accepts a ``patient_id`` or a ``sender``. Both are decided by
the route from the calling principal, so there is no field for a client to
put the wrong value in.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .patient_message import PatientMessage, PatientMessageThread  # noqa: TC001 — Pydantic runtime

# A patient types a message, not an essay, and the practice replies in kind.
# Generous enough that nobody meets it in normal use, small enough that the
# store cannot be filled by one caller.
MAX_MESSAGE_BODY = 8_000
MAX_SUBJECT = 200


class StartThreadRequest(BaseModel):
    """``POST /api/patient/messages/threads`` body."""

    subject: str | None = Field(default=None, max_length=MAX_SUBJECT)
    body: str = Field(min_length=1, max_length=MAX_MESSAGE_BODY)


class SendMessageRequest(BaseModel):
    """Body for both send routes: the patient's and the clinician's reply."""

    body: str = Field(min_length=1, max_length=MAX_MESSAGE_BODY)


class PatientMessageResponse(BaseModel):
    id: str
    thread_id: str
    sender: str
    body: str
    created_at: datetime
    read_at: datetime | None = None

    @staticmethod
    def from_message(message: PatientMessage) -> PatientMessageResponse:
        return PatientMessageResponse(
            id=message.id,
            thread_id=message.thread_id,
            sender=message.sender,
            body=message.body,
            created_at=message.created_at,
            read_at=message.read_at,
        )


class PatientMessageThreadResponse(BaseModel):
    id: str
    subject: str | None = None
    status: str
    created_at: datetime
    last_message_at: datetime
    # Only the patient's own list fills this in — a count of what they have
    # not read is not a fact about the clinician looking at the thread.
    unread_count: int | None = None

    @staticmethod
    def from_thread(
        thread: PatientMessageThread, unread_count: int | None = None
    ) -> PatientMessageThreadResponse:
        return PatientMessageThreadResponse(
            id=thread.id,
            subject=thread.subject,
            status=thread.status,
            created_at=thread.created_at,
            last_message_at=thread.last_message_at,
            unread_count=unread_count,
        )


class PatientMessageThreadDetailResponse(PatientMessageThreadResponse):
    messages: list[PatientMessageResponse] = Field(default_factory=list)

    @staticmethod
    def from_thread_with_messages(
        thread: PatientMessageThread, messages: list[PatientMessage]
    ) -> PatientMessageThreadDetailResponse:
        return PatientMessageThreadDetailResponse(
            id=thread.id,
            subject=thread.subject,
            status=thread.status,
            created_at=thread.created_at,
            last_message_at=thread.last_message_at,
            messages=[PatientMessageResponse.from_message(m) for m in messages],
        )


class PatientMessageThreadListResponse(BaseModel):
    data: list[PatientMessageThreadResponse]
    total: int


class MarkThreadReadResponse(BaseModel):
    """How many incoming messages the call marked read."""

    marked_read: int


__all__ = [
    "MAX_MESSAGE_BODY",
    "MAX_SUBJECT",
    "MarkThreadReadResponse",
    "PatientMessageResponse",
    "PatientMessageThreadDetailResponse",
    "PatientMessageThreadListResponse",
    "PatientMessageThreadResponse",
    "SendMessageRequest",
    "StartThreadRequest",
]
