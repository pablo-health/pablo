# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain models for secure patient messaging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# Who wrote a message. ``practice`` is the practice itself speaking without a
# clinician composing the words; nothing in the engine writes it today, and
# reading surfaces render it the same way they render a clinician.
SENDER_PATIENT = "patient"
SENDER_CLINICIAN = "clinician"
SENDER_PRACTICE = "practice"
MESSAGE_SENDERS: frozenset[str] = frozenset({SENDER_PATIENT, SENDER_CLINICIAN, SENDER_PRACTICE})

THREAD_STATUS_OPEN = "open"
THREAD_STATUS_CLOSED = "closed"

# How a clinician narrows a thread list. ``me`` is the clinician asking;
# ``unassigned`` is what nobody has picked up yet. The filter changes what is
# listed and never what may be read — see :class:`PatientMessageThread`.
ThreadAssignmentFilter = Literal["me", "unassigned", "all"]


@dataclass
class PatientMessageThread:
    """One conversation between a patient and their practice.

    ``subject`` is what the patient typed when they started it and may be
    absent. ``last_message_at`` tracks the newest message so a thread list
    sorts without reading the messages.

    ``assigned_user_id`` says who is looking after the thread. It is a
    routing hint and never an access rule: every clinician with a grant on
    the patient reads and answers the thread regardless of it.

    ``clinician_last_read_at`` is the practice side's single "we looked"
    mark, which is what the clinician unread count is measured from. The
    patient's own read state is per message and lives on
    :class:`PatientMessage`.
    """

    id: str
    patient_id: str
    subject: str | None
    status: str
    created_at: datetime
    last_message_at: datetime
    closed_at: datetime | None = None
    closed_by: str | None = None
    assigned_user_id: str | None = None
    clinician_last_read_at: datetime | None = None


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
