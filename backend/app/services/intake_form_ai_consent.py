# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client's transcription answer on an intake form, put on their chart.

The AI-tools starter (:mod:`app.intake.starters`) asks the client whether
they consent to session transcription. That answer is not left as one more
form answer: it is the client's AI-notes answer, and it is written to the
record every other reader of that answer uses —
:func:`app.services.client_ai_consent.record_ai_consent` — when the form is
handed in. The chart header reads it from there.

It takes effect on the day the client signed the document it sits under,
which is the day they read what they were agreeing to. A form with no
signature on that document (a practice that removed it) takes the day the
form was handed in instead.

Written on every submission that carries an answer, including a form handed
back in after corrections. The record is a history, so a second entry with
the same answer says only that the client gave it twice, which they did.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

from ..intake.starters import (
    AI_TOOLS_DOCUMENT_ITEM_KEY,
    AI_TRANSCRIPTION_DECISIONS,
    AI_TRANSCRIPTION_ITEM_KEY,
)
from .client_ai_consent import record_ai_consent

if TYPE_CHECKING:
    from datetime import date

    from ..models.client_ai_consent import AiConsentDecision, AiConsentEvent
    from ..repositories.client_ai_consent import ClientAiConsentRepository
    from ..repositories.patient_intake_signature import PatientIntakeSignatureRepository


def transcription_answer(
    items: list[dict[str, object]], answers: dict[str, dict[str, object]]
) -> AiConsentDecision | None:
    """The decision a form's transcription question was answered with, if any.

    ``items`` are the form's questions and ``answers`` the live answers keyed
    by item id, as :class:`IntakeAssignmentService` hands them back. ``None``
    when the form does not ask the question, or the answer is not one of the
    two the starter offers.
    """
    for item in items:
        if item["key"] != AI_TRANSCRIPTION_ITEM_KEY or item["item_type"] != "single_choice":
            continue
        chosen = answers.get(str(item["id"]), {}).get("key")
        decision = AI_TRANSCRIPTION_DECISIONS.get(chosen) if isinstance(chosen, str) else None
        return cast("AiConsentDecision", decision) if decision else None
    return None


def signed_on(items: list[dict[str, object]], signatures: list[dict[str, object]]) -> date | None:
    """The day the AI-tools document on this form was first signed, if it was."""
    document_items = {
        str(item["id"])
        for item in items
        if item["key"] == AI_TOOLS_DOCUMENT_ITEM_KEY and item["item_type"] == "consent_document"
    }
    days = [
        signed.date()
        for row in signatures
        if str(row["item_id"]) in document_items
        and isinstance(signed := row["signed_at"], datetime)
    ]
    return min(days) if days else None


class FormAiConsentRecorder:
    """Writes the transcription answer from a submitted form to the record."""

    def __init__(
        self,
        signatures: PatientIntakeSignatureRepository,
        consent: ClientAiConsentRepository,
    ) -> None:
        self._signatures = signatures
        self._consent = consent

    def record(
        self,
        *,
        assignment_id: str,
        patient_id: str,
        items: list[dict[str, object]],
        answers: dict[str, dict[str, object]],
        submitted_at: datetime,
    ) -> AiConsentEvent | None:
        """Record the form's answer, or nothing when the form did not ask.

        The submission it names is the assignment that was handed in.
        """
        decision = transcription_answer(items, answers)
        if decision is None:
            return None
        signatures = self._signatures.list_live_for_assignment(assignment_id, patient_id)
        effective_on = signed_on(items, signatures) or submitted_at.date()
        return record_ai_consent(
            patient_id,
            decision,
            effective_on,
            source="intake_form",
            recorded_by=None,
            intake_submission_id=assignment_id,
            today=submitted_at.date(),
            repo=self._consent,
        )


__all__ = ["FormAiConsentRecorder", "signed_on", "transcription_answer"]
