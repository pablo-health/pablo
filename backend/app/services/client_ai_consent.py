# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Recording and reading a client's answer about AI-assisted notes.

Two functions, and they are the interface — anything that writes an answer
calls :func:`record_ai_consent`, and anything that needs to know the answer
reads :func:`ai_consent_record`:

* the chart's own route, where a clinician records what the client told them
  (``source="clinician"``);
* an intake form that asks the client directly (``source="intake_form"``,
  with the submission it came from);
* anything deciding whether a session may be recorded, which reads
  ``ai_consent_record(...).current``.

Neither checks who may see the client. The caller does that first, the way
every chart route does, and the table's row policy enforces it again
underneath. Neither audits either: an audit row names the person who acted,
which only the caller knows.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..models.client_ai_consent import AiConsentEvent, AiConsentRecord
from ..repositories import get_client_ai_consent_repository
from ..utcnow import utc_now

if TYPE_CHECKING:
    from datetime import date

    from ..models.client_ai_consent import AiConsentDecision, AiConsentSource
    from ..repositories.client_ai_consent import ClientAiConsentRepository


class FutureAiConsentDateError(ValueError):
    """The answer is dated after today. A client cannot have given it yet."""


def record_ai_consent(
    patient_id: str,
    decision: AiConsentDecision,
    effective_on: date,
    source: AiConsentSource,
    recorded_by: str | None,
    intake_submission_id: str | None = None,
    *,
    today: date | None = None,
    repo: ClientAiConsentRepository | None = None,
) -> AiConsentEvent:
    """Append one answer to the client's record and return it.

    Never changes an earlier answer: a new one supersedes it by being later.

    ``effective_on`` is the day the client gave the answer. It may be earlier
    than today — a clinician catching up on paperwork — but not later, and
    :class:`FutureAiConsentDateError` says so. ``today`` is the caller's
    today, which for a clinician is their own calendar day; it defaults to
    the date in UTC.

    A clinician's entry names who recorded it (``recorded_by``, a user id). An
    intake-form entry names the submission it came from
    (``intake_submission_id``) and may have nobody behind ``recorded_by``.
    Anything else is a programming error and raises ``ValueError``.

    ``repo`` defaults to the request's tenant-scoped repository.
    """
    if source == "clinician" and not recorded_by:
        raise ValueError("A clinician's entry must name who recorded it.")
    if (source == "intake_form") != (intake_submission_id is not None):
        raise ValueError("An intake-form entry, and only one, names its submission.")
    if effective_on > (today or utc_now().date()):
        raise FutureAiConsentDateError(effective_on.isoformat())

    event = AiConsentEvent(
        id=str(uuid.uuid4()),
        patient_id=patient_id,
        decision=decision,
        effective_on=effective_on,
        source=source,
        recorded_by=recorded_by,
        recorded_at=utc_now(),
        intake_submission_id=intake_submission_id,
    )
    return (repo or get_client_ai_consent_repository()).append(event)


def ai_consent_record(
    patient_id: str, *, repo: ClientAiConsentRepository | None = None
) -> AiConsentRecord:
    """The client's current answer and every answer before it.

    ``current`` is ``None`` when nobody has asked the client yet.
    """
    events = (repo or get_client_ai_consent_repository()).list_for_patient(patient_id)
    return AiConsentRecord.from_events(events)
