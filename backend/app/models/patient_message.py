# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain models for secure patient messaging."""

from __future__ import annotations

from dataclasses import dataclass
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
class PatientMessage:
    """One message inside a :class:`PatientMessageThread`.

    ``read_at`` is set when the patient reads something somebody else sent
    them; it stays ``None`` on the patient's own messages.
    """

    id: str
    thread_id: str
    patient_id: str
    sender: str
    body: str
    created_at: datetime
    read_at: datetime | None = None
