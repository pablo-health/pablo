# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Signing out, and what this portal can do.

Three routes, all behind a resolved patient principal — which is what
separates them from :mod:`app.portal.routes`, where redemption and refresh
run before or across a principal:

* ``POST /api/patient/auth/logout`` — retire THIS session. Any auth
  strength: refusing to let someone leave because they have not proved
  enough is backwards.
* ``POST /api/patient/auth/logout-all`` — retire EVERY session this patient
  has. Step-up required, because it is a security action taken on devices
  that are not in the room, and the one thing a person does with a borrowed
  link should not be to lock the real patient out.
* ``GET /api/patient/capabilities`` — the practice's name and which portal
  modules exist here, so the shell can render a navigation that matches the
  deployment instead of guessing.

**Sign-out is a server-side event, not a cleared browser.** The whole point
of the session row is that a token stops working when the row says so, and a
patient who signs out on a shared machine has to get that, not a token that
is merely no longer in local storage. The client clears its storage too,
because the credential should not sit there either — but the clearing is the
cleanup, and this is the act.

**A sign-out never fails at the patient.** Revoking a session that already
expired, or that a rotation retired a moment ago, is a no-op that answers
success. There is nothing for the caller to do differently and nothing to
learn from the difference.

Nothing here logs a token or a patient id. The audit row names the session
handle, which unlocks nothing on its own.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

# Runtime import: FastAPI resolves this annotation when it builds the route,
# so it cannot live in a TYPE_CHECKING block.
from sqlalchemy.orm import Session  # noqa: TC002

from ..api_errors import ForbiddenError
from ..auth.patient_context import AuthStrength, PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..db import get_db_session
from ..models.audit import AuditAction, ResourceType
from ..services.audit_service import AuditService, get_audit_service
from ..settings import get_settings
from .db_store import DbPortalSessionStore
from .factory import build_portal_auth_service
from .modules import mounted_modules_on, portal_capabilities
from .practice_routes import practice_address_for_schema

logger = logging.getLogger(__name__)

router = APIRouter(tags=["patient-portal"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]

#: Same posture as redeem and refresh in :mod:`app.portal.routes`. These are
#: authentication and deployment shape, not practice data: a patient cannot
#: see or fix a practice's billing state, and refusing to let them sign out
#: over one would be absurd.
_SUBSCRIPTION_EXEMPT = Depends(subscription_exempt)


class SignOutResponse(BaseModel):
    """What the sign-out actually retired."""

    #: How many sessions stopped working because of this call. Zero is a
    #: success: the session was already gone.
    sessions_revoked: int


class PortalPracticeSummary(BaseModel):
    """The practice, as the person signed into it sees it named."""

    display_name: str | None = None


class PortalCapabilitiesResponse(BaseModel):
    """What this portal can do for the patient asking.

    ``modules`` carries every known module and a boolean, rather than a list
    of the enabled ones — see :mod:`app.portal.modules` for why the off ones
    are named too.
    """

    practice: PortalPracticeSummary
    modules: dict[str, bool]
    #: How strongly the caller proved who they are. Reported so the shell can
    #: tell a step-up refusal from a failure before it makes the request;
    #: every route still decides its own bar rather than trusting this.
    auth_strength: AuthStrength


def _require_stepped_up(patient: PatientContext) -> None:
    """Refuse a single-factor principal.

    Same bar and same words as the patient chat and intake surfaces. Signing
    every device out is not reading a chart, but it is a change to the
    account made from one device about all the others.
    """
    if patient.auth_strength is not AuthStrength.STEPPED_UP:
        raise ForbiddenError("Confirm it is you to continue.", code="STEP_UP_REQUIRED")


def _sessions(session: Session) -> DbPortalSessionStore:
    """The revocation list on the request's own tenant-scoped session.

    ``get_patient_context`` has already pointed ``search_path`` at this
    patient's practice and armed the patient GUC, so — unlike redeem and
    refresh, which have no principal to inherit a transaction from — there is
    nothing to open here. The request owns the transaction.
    """
    return DbPortalSessionStore(session)


@router.post(
    "/api/patient/auth/logout",
    response_model=SignOutResponse,
    dependencies=[_SUBSCRIPTION_EXEMPT],
)
def sign_out(
    request: Request,
    patient: CurrentPatient,
    session: Annotated[Session, Depends(get_db_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> SignOutResponse:
    """Sign this one device out, leaving the patient's other sessions alone.

    No auth-strength bar. A single-factor principal that wanted to cause
    harm would not choose to end its own session, and a patient on a
    borrowed computer who cannot sign out is the failure that matters.

    The session handle comes off the resolved principal — the row the front
    door proved this request against — so there is no id in the request for a
    caller to point somewhere else.
    """
    jti = patient.session_id
    if jti is None:
        # A patient principal from a front door that keeps no server-side
        # session. None exists today; if one lands, "sign out" is its own
        # question for that door rather than a no-op pretending to be one.
        logger.warning(
            "Portal sign-out on a principal with no session handle",
            extra={"credential_kind": patient.credential_kind},
        )
        return SignOutResponse(sessions_revoked=0)

    store = _sessions(session)
    record = store.get(jti)
    already_gone = record is None or record.revoked_at is not None
    build_portal_auth_service(sessions=store).revoke_session(jti=jti)

    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_SESSION_REVOKED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT,
        resource_id=patient.patient_id,
        session_id=jti,
        changes={"scope": "session", "sessions_revoked": 0 if already_gone else 1},
    )
    return SignOutResponse(sessions_revoked=0 if already_gone else 1)


@router.post(
    "/api/patient/auth/logout-all",
    response_model=SignOutResponse,
    dependencies=[_SUBSCRIPTION_EXEMPT],
)
def sign_out_everywhere(
    request: Request,
    patient: CurrentPatient,
    session: Annotated[Session, Depends(get_db_session)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> SignOutResponse:
    """Sign out of every device, including this one.

    What a patient reaches for after losing a phone, so it is the one
    sign-out that needs the second factor: it acts on sessions that are not
    in the room, and a link that reached the wrong inbox should not be able
    to lock the real patient out of their own portal.

    Outstanding invitations are deliberately NOT burned. That is the
    clinician's kill switch, which withdraws access; this is the patient
    ending their own sessions, and a patient who signs out everywhere and
    then opens the link still sitting in their inbox should be let back in.
    """
    _require_stepped_up(patient)

    service = build_portal_auth_service(sessions=_sessions(session))
    revoked = service.revoke_patient(patient_id=patient.patient_id)

    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_SESSION_REVOKED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT,
        resource_id=patient.patient_id,
        session_id=patient.session_id,
        changes={"scope": "all", "sessions_revoked": revoked},
    )
    return SignOutResponse(sessions_revoked=revoked)


@router.get(
    "/api/patient/capabilities",
    response_model=PortalCapabilitiesResponse,
    dependencies=[_SUBSCRIPTION_EXEMPT],
)
def get_capabilities(
    request: Request,
    patient: CurrentPatient,
) -> PortalCapabilitiesResponse:
    """What this portal serves, for the shell to render a navigation from.

    Single-factor is enough. The answer is about the DEPLOYMENT, not about
    the person: which modules exist here is the same for every patient of
    this practice, and a shell that cannot draw its own navigation until the
    second factor clears would show an empty frame to someone mid-sign-in.

    The mounted half is read off the live application, so a module this
    build does not serve reports off however it is configured — see
    :mod:`app.portal.modules`.
    """
    configured = get_settings().portal_module_names
    mounted = mounted_modules_on(request.app)
    # The practice's own name, from the same platform directory the
    # unauthenticated slug route answers from — so the header before sign-in
    # and the header after it cannot disagree. ``None`` when the practice
    # has no address yet, which is a blank header rather than an error: the
    # load-bearing half of this document is the module map, and it came from
    # the route table.
    address = practice_address_for_schema(patient.practice_schema)
    return PortalCapabilitiesResponse(
        practice=PortalPracticeSummary(
            display_name=None if address is None else address.display_name
        ),
        modules=portal_capabilities(configured=configured, mounted=mounted),
        auth_strength=patient.auth_strength,
    )
