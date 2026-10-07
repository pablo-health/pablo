# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A client's transcription answer on an intake form, put on their chart.

The AI-tools starter (:mod:`app.intake.starters`) asks the client whether
they consent to session transcription. That answer is not left as one more
form answer: it is the client's AI-notes answer, and it is written to the
record every other reader of that answer uses —
:func:`app.services.client_ai_consent.record_ai_consent` — when the
clinician accepts the form. The chart header reads it from there.

Written on acceptance rather than when the client hands the form in, for the
same reason every other answer on a form reaches the chart through review: a
form handed in can still be sent back for corrections, and acceptance is the
practice saying this is what the client answered. It also keeps the write on
the clinician's session, under the record's own access rule, rather than
opening the table to the client.

It takes effect on the day the client signed the document it sits under,
which is the day they read what they were agreeing to. A form with no
signature on that document (a practice that removed it) takes the day the
form was handed in instead.

It also says how the answer was given, as far as the form shows it. A form
whose telehealth consent document was signed records the answer as given
over telehealth, with the client's answer to the telehealth location
question as where they said they would be. A form without a signed one
leaves the modality unset rather than guessing in person: a form is not tied
to a visit, so nothing else on it says how the client was being seen. A form that names a parent or
guardian records the answer as theirs; any other form, as the client's.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, cast

from ..intake.starters import (
    AI_TOOLS_DOCUMENT_ITEM_KEY,
    AI_TRANSCRIPTION_DECISIONS,
    AI_TRANSCRIPTION_ITEM_KEY,
    TELEHEALTH_DOCUMENT_ITEM_KEY,
    TELEHEALTH_LOCATION_ITEM_KEY,
)
from ..models.client_ai_consent import CLIENT_STATED_LOCATION_MAX
from .client_ai_consent import record_ai_consent

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from ..models.client_ai_consent import (
        AiConsentDecision,
        AiConsentEvent,
        AiConsentGiver,
        AiConsentModality,
    )
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


def _document_items(items: list[dict[str, object]], key: str) -> set[str]:
    """The ids of the consent documents on this form under *key*."""
    return {
        str(item["id"])
        for item in items
        if item["key"] == key and item["item_type"] == "consent_document"
    }


def signed_on(items: list[dict[str, object]], signatures: list[dict[str, object]]) -> date | None:
    """The day the AI-tools document on this form was first signed, if it was."""
    document_items = _document_items(items, AI_TOOLS_DOCUMENT_ITEM_KEY)
    days = [
        signed.date()
        for row in signatures
        if str(row["item_id"]) in document_items
        and isinstance(signed := row["signed_at"], datetime)
    ]
    return min(days) if days else None


def modality(
    items: list[dict[str, object]], signatures: list[dict[str, object]]
) -> AiConsentModality | None:
    """``telehealth`` when the form's telehealth consent was signed; unknown otherwise."""
    document_items = _document_items(items, TELEHEALTH_DOCUMENT_ITEM_KEY)
    signed = any(str(row["item_id"]) in document_items for row in signatures)
    return "telehealth" if signed else None


def stated_location(
    items: list[dict[str, object]], answers: Mapping[str, Mapping[str, object]]
) -> str | None:
    """The client's answer to the telehealth location question, if they gave one."""
    for item in items:
        if item["key"] != TELEHEALTH_LOCATION_ITEM_KEY or item["item_type"] != "free_text":
            continue
        text = answers.get(str(item["id"]), {}).get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()[:CLIENT_STATED_LOCATION_MAX]
    return None


#: Words in a named guardian's relationship that make them the client's
#: parent. Anyone else named there (a grandparent, a legal guardian, an
#: aunt) is recorded as a guardian.
_PARENT_WORDS = frozenset({"parent", "mother", "father", "mom", "mum", "dad"})


def consented_by(
    items: list[dict[str, object]], answers: Mapping[str, Mapping[str, object]]
) -> AiConsentGiver:
    """A parent or guardian named on the form, or else the client."""
    for item in items:
        if item["item_type"] != "guardian":
            continue
        guardian = answers.get(str(item["id"]), {})
        name = guardian.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        relationship = guardian.get("relationship")
        text = relationship.lower() if isinstance(relationship, str) else ""
        return "parent" if set(re.findall(r"[a-z]+", text)) & _PARENT_WORDS else "guardian"
    return "client"


class FormAiConsentRecorder:
    """Writes the transcription answer from an accepted form to the record.

    Built on the clinician's tenant-scoped session: the caller has already
    checked the clinician may see the client, and the record's row policy
    checks it again underneath.
    """

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
        assignment: dict[str, object],
        items: list[dict[str, object]],
        answers: dict[str, dict[str, object]],
        accepted_by: str,
        accepted_at: datetime,
    ) -> AiConsentEvent | None:
        """Record the form's answer, or nothing when the form did not ask.

        The submission it names is the assignment that was accepted, and
        the clinician who accepted it is who recorded it.
        """
        decision = transcription_answer(items, answers)
        if decision is None:
            return None
        assignment_id = str(assignment["id"])
        patient_id = str(assignment["patient_id"])
        signatures = self._signatures.list_live_for_assignment(assignment_id, patient_id)
        submitted_at = assignment.get("submitted_at")
        handed_in = submitted_at.date() if isinstance(submitted_at, datetime) else None
        effective_on = signed_on(items, signatures) or handed_in or accepted_at.date()
        return record_ai_consent(
            patient_id,
            decision,
            effective_on,
            source="intake_form",
            recorded_by=accepted_by,
            intake_submission_id=assignment_id,
            modality=modality(items, signatures),
            client_stated_location=stated_location(items, answers),
            consented_by=consented_by(items, answers),
            today=accepted_at.date(),
            repo=self._consent,
        )


__all__ = [
    "FormAiConsentRecorder",
    "consented_by",
    "modality",
    "signed_on",
    "stated_location",
    "transcription_answer",
]
