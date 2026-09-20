# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain models for secure patient messaging."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Who wrote a message. ``practice`` is the practice itself speaking without a
# clinician composing the words; nothing in the engine writes it today, and
# reading surfaces render it the same way they render a clinician.
SENDER_PATIENT = "patient"
SENDER_CLINICIAN = "clinician"
SENDER_PRACTICE = "practice"
MESSAGE_SENDERS: frozenset[str] = frozenset({SENDER_PATIENT, SENDER_CLINICIAN, SENDER_PRACTICE})

THREAD_STATUS_OPEN = "open"
THREAD_STATUS_CLOSED = "closed"

# How many files may ride on one message. Generous for the errands this
# surface exists for — a card, a letter, a page of a form — and small enough
# that one send cannot become a bulk transfer.
MAX_ATTACHMENTS_PER_MESSAGE = 5


@dataclass
class PatientMessageThread:
    """One conversation between a patient and their practice.

    ``subject`` is what the patient typed when they started it and may be
    absent. ``last_message_at`` tracks the newest message so a thread list
    sorts without reading the messages.
    """

    id: str
    patient_id: str
    subject: str | None
    status: str
    created_at: datetime
    last_message_at: datetime


@dataclass
class MessageAttachment:
    """A file sent on a message, as a reader of the thread sees it.

    A projection, not a row: the link table carries only ids, and the three
    descriptive fields are read off the document it names. They travel
    together because a thread payload is useless without them — a chip
    reading ``8f3c…`` tells the patient nothing about what they sent.

    ``filename`` is PHI-adjacent. People name files after what is in them,
    so it belongs in an authenticated API response and nowhere else: never
    in a notification, never in an audit payload, never in a log line.
    """

    message_id: str
    document_id: str
    filename: str
    mime_type: str
    size_bytes: int


@dataclass
class PatientMessage:
    """One message inside a :class:`PatientMessageThread`.

    ``read_at`` is set when the patient reads something somebody else sent
    them; it stays ``None`` on the patient's own messages.

    ``attachments`` is what a reader of the thread was sent alongside the
    words. It is filled in by whoever read the message; a message built to
    be written carries an empty list, because the links do not exist until
    the message does.
    """

    id: str
    thread_id: str
    patient_id: str
    sender: str
    body: str
    created_at: datetime
    read_at: datetime | None = None
    attachments: list[MessageAttachment] = field(default_factory=list)
