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
from typing import Annotated

from pydantic import BaseModel, Field

from .patient_message import (
    MAX_ATTACHMENTS_PER_MESSAGE,
    MessageAttachment,
    PatientMessage,
    PatientMessageThread,
)

# A patient types a message, not an essay, and the practice replies in kind.
# Generous enough that nobody meets it in normal use, small enough that the
# store cannot be filled by one caller.
MAX_MESSAGE_BODY = 8_000
MAX_SUBJECT = 200

# Every send route takes the same optional list of files, so it is declared
# once. The ids name documents the caller has already uploaded and finalized
# through their own document routes with category ``message``; what makes
# one usable is checked at send time, not here, because this layer cannot
# see whose chart a document is on.
AttachmentIds = Annotated[list[str], Field(max_length=MAX_ATTACHMENTS_PER_MESSAGE)]


class StartThreadRequest(BaseModel):
    """``POST /api/patient/messages/threads`` body."""

    subject: str | None = Field(default=None, max_length=MAX_SUBJECT)
    body: str = Field(min_length=1, max_length=MAX_MESSAGE_BODY)
    attachment_ids: AttachmentIds = []


class SendMessageRequest(BaseModel):
    """Body for both send routes: the patient's and the clinician's reply."""

    body: str = Field(min_length=1, max_length=MAX_MESSAGE_BODY)
    attachment_ids: AttachmentIds = []


class MessageAttachmentResponse(BaseModel):
    """One file on a message.

    Four fields, pinned. ``filename`` is here because a chip has to say what
    it is; it is the reason this shape never goes into a notification, which
    carries a link and nothing else.
    """

    document_id: str
    filename: str
    mime_type: str
    size_bytes: int

    @staticmethod
    def from_attachment(attachment: MessageAttachment) -> MessageAttachmentResponse:
        return MessageAttachmentResponse(
            document_id=attachment.document_id,
            filename=attachment.filename,
            mime_type=attachment.mime_type,
            size_bytes=attachment.size_bytes,
        )


class PatientMessageResponse(BaseModel):
    id: str
    thread_id: str
    sender: str
    body: str
    created_at: datetime
    read_at: datetime | None = None
    attachments: list[MessageAttachmentResponse] = Field(default_factory=list)

    @staticmethod
    def from_message(message: PatientMessage) -> PatientMessageResponse:
        return PatientMessageResponse(
            id=message.id,
            thread_id=message.thread_id,
            sender=message.sender,
            body=message.body,
            created_at=message.created_at,
            read_at=message.read_at,
            attachments=[MessageAttachmentResponse.from_attachment(a) for a in message.attachments],
        )


class AssignThreadRequest(BaseModel):
    """``POST /api/message-threads/{thread_id}/assign`` body.

    ``null`` is a real answer and means "nobody" — unassigning is how a
    thread goes back to the pool, so it is the same route rather than a
    DELETE that reads as removing the thread.
    """

    user_id: str | None = None


class PatientMessageThreadResponse(BaseModel):
    id: str
    subject: str | None = None
    status: str
    created_at: datetime
    last_message_at: datetime
    closed_at: datetime | None = None
    closed_by: str | None = None
    assigned_user_id: str | None = None
    # Both lists fill this in, and they count different things: the patient's
    # is what the practice sent them and they have not opened, the clinician's
    # is what the patient has sent since anybody at the practice looked. A
    # thread opened on its own carries no count either way.
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
            closed_at=thread.closed_at,
            closed_by=thread.closed_by,
            assigned_user_id=thread.assigned_user_id,
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
            closed_at=thread.closed_at,
            closed_by=thread.closed_by,
            assigned_user_id=thread.assigned_user_id,
            messages=[PatientMessageResponse.from_message(m) for m in messages],
        )


class PatientMessageThreadListResponse(BaseModel):
    data: list[PatientMessageThreadResponse]
    total: int


class MarkThreadReadResponse(BaseModel):
    """How many incoming messages the call marked read."""

    marked_read: int


class ThreadExportResponse(BaseModel):
    """A whole conversation, for the record.

    The same fields the reading surfaces already show, arranged so a
    transcript can be filed or handed on without a second call. Rendering it
    for a human is somebody else's job; this is the machine-readable form.
    """

    thread: PatientMessageThreadResponse
    messages: list[PatientMessageResponse]


__all__ = [
    "MAX_MESSAGE_BODY",
    "MAX_SUBJECT",
    "AssignThreadRequest",
    "MarkThreadReadResponse",
    "MessageAttachmentResponse",
    "PatientMessageResponse",
    "PatientMessageThreadDetailResponse",
    "PatientMessageThreadListResponse",
    "PatientMessageThreadResponse",
    "SendMessageRequest",
    "StartThreadRequest",
    "ThreadExportResponse",
]
