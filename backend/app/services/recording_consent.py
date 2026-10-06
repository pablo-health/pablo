# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Refusing to start a recording the client's answer does not allow.

When the practice asks its clients about AI-assisted notes (the practice
setting in ``app.routes.practice_ai_notes_consent``) and a client's current
answer is "declined", a recorded session for that client does not start: the
start paths refuse with ``CLIENT_DECLINED_AI_NOTES`` and the day the client
declined, so the caller can say so. With the setting off, nothing is checked.

A client nobody has asked yet depends on where the client is. In person, the
session may start; the web app and the companion prompt the clinician
instead, and the clinician may record anyway. Over telehealth, the client may be
somewhere every party to a recording has to agree to it, and the session does
not know where that is, so recording before asking is not offered: a start
with nothing on file is refused with ``CLIENT_AI_CONSENT_NEEDED`` unless the
caller says the clinician is asking once recording starts, which puts the
client's answer on the recording itself.

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
CLIENT_AI_CONSENT_NEEDED = "CLIENT_AI_CONSENT_NEEDED"


class RecordingConsentGate:
    """Refuses a recorded session the client's answer does not allow, when the practice asks."""

    def __init__(
        self,
        *,
        asks_clients: bool,
        consents: ClientAiConsentRepository | None = None,
    ) -> None:
        self.asks_clients = asks_clients
        self._consents = consents

    def refuse_unless_recordable(  # noqa: PLR0913 — the two facts about this start are keyword-only
        self,
        patient: Patient,
        user: User,
        request: Request,
        audit: AuditService,
        *,
        telehealth: bool = False,
        asking_on_recording: bool = False,
    ) -> None:
        """Raise when the client's answer does not allow recording this session.

        ``CLIENT_DECLINED_AI_NOTES`` when the client's current answer is no.
        ``CLIENT_AI_CONSENT_NEEDED`` for a *telehealth* session with no answer
        on file, unless the clinician is *asking_on_recording*.

        Takes the :class:`Patient` the caller has already looked up, never a
        bare id: under the row policy a clinician without a grant reads an
        empty consent record, which would look like "not asked yet". Looking
        the client up first turns that into the caller's own 404.

        A refusal tells the clinician what is on the client's record, so it
        is audited as a read of the consent record.
        """
        if not self.asks_clients:
            return
        current = ai_consent_record(patient.id, repo=self._consents).current
        if current is None:
            if not telehealth or asking_on_recording:
                return
            self._audit_refusal(patient, user, request, audit)
            raise ForbiddenError(
                "Ask this client about AI-assisted notes when recording starts.",
                code=CLIENT_AI_CONSENT_NEEDED,
            )
        if current.decision != "declined":
            return
        self._audit_refusal(patient, user, request, audit)
        raise ForbiddenError(
            "This client declined AI-assisted notes.",
            {"declined_on": current.effective_on.isoformat()},
            code=CLIENT_DECLINED_AI_NOTES,
        )

    @staticmethod
    def _audit_refusal(patient: Patient, user: User, request: Request, audit: AuditService) -> None:
        audit.log_patient_action(
            AuditAction.PATIENT_AI_CONSENT_VIEWED,
            user,
            request,
            patient,
            changes={"recording_refused": True},
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
