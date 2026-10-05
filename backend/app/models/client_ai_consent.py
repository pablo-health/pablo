# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain and API models for a client's answer about AI-assisted notes.

AI-assisted notes means a session is recorded, transcribed and drafted into a
note by a model. A client either agreed to that or declined it, and the answer
holds for every session until they give a different one.

:class:`AiConsentEvent` is one recorded answer. :class:`AiConsentRecord` is the
whole record for a client — the current answer and the history behind it — and
is both what the chart reads and what anything that needs to know "may this
session be recorded" should read. Its shape is the ``GET`` response and is kept
stable for those readers.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

AiConsentDecision = Literal["consented", "declined"]

#: Who put the answer on the chart. ``clinician`` is a staff member recording
#: what the client told them; ``intake_form`` is the client answering a
#: question on an intake form, which carries the submission it came from.
AiConsentSource = Literal["clinician", "intake_form"]


class AiConsentEvent(BaseModel):
    """One answer, as it was recorded. Never changed once written."""

    id: str
    patient_id: str
    decision: AiConsentDecision
    #: The day the client gave the answer, which may be earlier than the day
    #: it was recorded.
    effective_on: date
    source: AiConsentSource
    recorded_by: str | None = None
    #: The recording staff member's name, resolved on read. ``None`` when the
    #: answer came from a form with nobody behind it, or the account is gone.
    recorded_by_name: str | None = None
    recorded_at: datetime
    intake_submission_id: str | None = None


class AiConsentEntry(BaseModel):
    """One answer as the API shows it."""

    id: str
    decision: AiConsentDecision
    effective_on: date
    source: AiConsentSource
    recorded_by_name: str | None
    recorded_at: datetime

    @classmethod
    def from_event(cls, event: AiConsentEvent) -> AiConsentEntry:
        return cls(
            id=event.id,
            decision=event.decision,
            effective_on=event.effective_on,
            source=event.source,
            recorded_by_name=event.recorded_by_name,
            recorded_at=event.recorded_at,
        )


class AiConsentRecord(BaseModel):
    """``GET /api/patients/{id}/ai-consent``.

    ``current`` is the latest answer by ``recorded_at``, or ``None`` when the
    client has not been asked. ``history`` is every answer, oldest first, and
    ends with ``current``.
    """

    current: AiConsentEntry | None
    history: list[AiConsentEntry]

    @classmethod
    def from_events(cls, events: list[AiConsentEvent]) -> AiConsentRecord:
        history = [AiConsentEntry.from_event(e) for e in events]
        return cls(current=history[-1] if history else None, history=history)


class RecordAiConsentRequest(BaseModel):
    """``POST /api/patients/{id}/ai-consent``: a clinician records an answer.

    ``effective_on`` defaults to today and may not be in the future.
    """

    decision: AiConsentDecision
    effective_on: date | None = Field(default=None)
