# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Refusing to start a recording for a client who declined AI-assisted notes.

When the practice asks its clients about AI-assisted notes (the practice
setting in ``app.routes.practice_ai_notes_consent``) and a client's current
answer is "declined", a recorded session for that client does not start: the
start paths refuse with ``CLIENT_DECLINED_AI_NOTES`` and the day the client
declined, so the caller can say so. A client nobody has asked yet is allowed;
the web app prompts the clinician instead. With the setting off, nothing is
checked.

The gate sits on the paths that START a recorded session, not on the audio
upload. By the time audio arrives the session has already been recorded, and
refusing the upload would only lose the clinician's recording; the decline
has to stop the session before it begins.

This is not the deployment's recording entitlement (``_gate_recording`` in
``app.routes.sessions``). That one asks whether the practice may record at all;
this one asks whether this client agreed. They stay separate checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends

from ..api_errors import ForbiddenError
from ..auth.service import TenantContext, get_tenant_context
from ..models.audit import AuditAction
from .client_ai_consent import ai_consent_record

if TYPE_CHECKING:
    from fastapi import Request

    from ..models import Patient, User
    from ..repositories.client_ai_consent import ClientAiConsentRepository
    from .audit_service import AuditService

CLIENT_DECLINED_AI_NOTES = "CLIENT_DECLINED_AI_NOTES"


class RecordingConsentGate:
    """Refuses a recorded session for a client who declined, when the practice asks."""

    def __init__(
        self,
        *,
        asks_clients: bool,
        consents: ClientAiConsentRepository | None = None,
    ) -> None:
        self.asks_clients = asks_clients
        self._consents = consents

    def refuse_if_declined(
        self, patient: Patient, user: User, request: Request, audit: AuditService
    ) -> None:
        """Raise ``CLIENT_DECLINED_AI_NOTES`` when the client's current answer is no.

        Takes the :class:`Patient` the caller has already looked up, never a
        bare id: under the row policy a clinician without a grant reads an
        empty consent record, which would look like "not asked yet". Looking
        the client up first turns that into the caller's own 404.

        A refusal tells the clinician the client's answer and its date, so it
        is audited as a read of the consent record.
        """
        if not self.asks_clients:
            return
        current = ai_consent_record(patient.id, repo=self._consents).current
        if current is None or current.decision != "declined":
            return
        audit.log_patient_action(
            AuditAction.PATIENT_AI_CONSENT_VIEWED,
            user,
            request,
            patient,
            changes={"recording_refused": True},
        )
        raise ForbiddenError(
            "This client declined AI-assisted notes.",
            {"declined_on": current.effective_on.isoformat()},
            code=CLIENT_DECLINED_AI_NOTES,
        )


def _practice_asks_clients(practice_id: str | None) -> bool:
    if practice_id is None:
        return False
    from ..db import get_db_session
    from ..db.platform_models import PracticeRow

    practice = get_db_session().get(PracticeRow, practice_id)
    return practice is not None and practice.ask_clients_about_ai_notes


def get_recording_consent_gate(
    ctx: TenantContext = Depends(get_tenant_context),
) -> RecordingConsentGate:
    """The gate for the caller's practice, reading the same setting its clinicians see."""
    return RecordingConsentGate(asks_clients=_practice_asks_clients(ctx.practice_id))
