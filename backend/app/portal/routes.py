# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The portal credential lifecycle over HTTP.

Five routes, and they do not share a principal:

* ``POST /api/patients/{id}/portal-invite`` — CLINICIAN. Mints a single-use
  invitation, texts the step-up code, emails the magic link.
* ``GET``/``DELETE /api/patients/{id}/portal-access`` — CLINICIAN. The state
  of one patient's portal access, and the kill switch.
* ``POST /api/patient/auth/redeem`` — UNAUTHENTICATED. Link plus code in,
  patient-session token out. The only route in the engine that mints a
  credential for someone who had none.
* ``POST /api/patient/auth/refresh`` — rotates a live session.

**The invite token never appears in a response body.** It travels to the
patient's email address and nowhere else: a clinician's screenshot, a
support ticket or a browser history entry must not be a credential. The same
goes for the code, which only ever exists in the text message.

**Every redemption failure is the same 401.** Bad token, wrong code,
consumed invitation, expired invitation, attempt-capped, revoked — one
status, one body, byte-identical to the one an unresolvable session gets
from ``get_patient_context``. Distinguishable failures would make this
endpoint an oracle for which invitations exist and which codes are close.

**Redemption runs before any principal exists.** There is no clinician
session and no patient session yet — redeeming is what creates one — so the
practice is entered from the SIGNATURE-VERIFIED token's tenant claim, on a
standalone session, after checking the claim names a real practice schema.
That ordering is the security argument: signature first, schema second,
database third.

Nothing here logs a token, a link, a code, an email address or a phone
number. The token id — a random handle that unlocks nothing on its own — is
the most identifying thing that reaches an audit row.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..api_errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from ..auth.patient_context import patient_not_authenticated_detail
from ..auth.route_access import subscription_exempt
from ..auth.route_security import truly_public
from ..auth.service import _resolve_practice_from_email, require_active_subscription
from ..db import DEFAULT_PRACTICE_SCHEMA
from ..models import User
from ..models.audit import AuditAction, ResourceType
from ..rate_limit import (
    check_portal_redeem_invite_limit,
    require_portal_redeem_rate_limit,
    require_portal_refresh_rate_limit,
)
from ..repositories import get_patient_repository
from ..repositories.patient import PatientRepository
from ..services.audit_service import AuditService, get_audit_service
from ..settings import get_settings
from ..utcnow import utc_now
from . import tokens
from .delivery import (
    DeliveryNotConfigured,
    DeliveryNotConfiguredError,
    PortalInviteDelivery,
    SmsGateway,
)
from .errors import PortalAuthError
from .factory import (
    build_invite_link,
    build_portal_auth_service,
    get_invite_delivery,
    get_sms_gateway,
)
from .practice_routes import ensure_practice_slug
from .tenant_gateway import (
    PortalStores,
    PortalTenantGateway,
    PortalTenantWork,
    get_portal_stores,
    get_portal_tenant_gateway,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ..models.patient import Patient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["patient-portal"])

#: The two patient-facing routes are deliberately outside the subscription
#: gate. They are AUTHENTICATION, not practice data: a patient mid-redemption
#: has no way to know or fix a practice's billing state, and refusing to
#: rotate a live session would strand them with no explanation. What a lapsed
#: practice actually stops is issuance — the clinician's invite route is
#: gated — and every route BEHIND a session carries its own posture. So the
#: credential surface stays reachable and the practice-data surface is where
#: the access level decides.
_SUBSCRIPTION_EXEMPT = Depends(subscription_exempt)


# ── shared refusals and small helpers ───────────────────────────────────


def _unauthenticated() -> HTTPException:
    """The single 401 every portal sign-in failure produces.

    Identical in status AND body to the one ``get_patient_context`` raises
    for an unresolvable session — it is literally the same dict — so a
    caller cannot tell a bad code from a consumed invitation from a revoked
    session, or tell this endpoint's refusals apart from the rest of the
    surface.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=patient_not_authenticated_detail(),
    )


def _delivery_unavailable() -> ServiceUnavailableError:
    """503 when a channel is not wired.

    A 503 rather than a quiet success: an invitation nobody can receive
    looks exactly like a delivered one from the clinician's side, and the
    patient is the one who finds out.
    """
    return ServiceUnavailableError(
        "Portal invitations cannot be delivered from this deployment.",
        code="PORTAL_DELIVERY_NOT_CONFIGURED",
    )


def _is_tenant_schema(schema: str) -> bool:
    """Is this claim the schema of an actual practice?

    ``platform``, ``public`` and the provisioning template are all valid
    identifiers and all wrong answers. Checked before the name reaches
    ``create_standalone_session``, which would otherwise point the session
    at the shared template quite happily.
    """
    return schema.startswith(f"{DEFAULT_PRACTICE_SCHEMA}_")


def _signing_key() -> str:
    return get_settings().portal_token_signing_key.get_secret_value()


def _practice_slug_for(user: User) -> str:
    """The caller's practice's portal address, minted if it has none yet.

    Resolved from the caller rather than taken as a parameter: a clinician
    cannot invite a patient into somebody else's practice, and there is no
    request field here that could be made to say otherwise.
    """
    practice = _resolve_practice_from_email(user.email)
    if practice is None:
        raise ConflictError("No practice is associated with this account yet.")
    practice_id, _schema_name = practice
    return ensure_practice_slug(practice_id)


def _patient_or_404(patients: PatientRepository, patient_id: str, user_id: str) -> Patient:
    """The patient this clinician may act on, or a 404.

    Through the repository rather than a direct row read, so the clinician's
    access grant decides. A clinician with no grant on this chart gets the
    same 404 as an id that does not exist — there is nothing to learn here
    about who another practitioner treats.
    """
    patient = patients.get(patient_id, user_id)
    if patient is None:
        raise NotFoundError("Patient not found.")
    return patient


# ── clinician: issue, inspect, revoke ───────────────────────────────────


class PortalInviteAccepted(BaseModel):
    """Acknowledgement of an issued invitation. Carries no credential."""

    patient_id: str
    #: When the magic link stops working (unix seconds), so the clinician
    #: can say "within fifteen minutes" without seeing the link itself.
    invite_expires_at: int


class PortalAccessState(BaseModel):
    """What a patient's portal access looks like right now."""

    patient_id: str
    #: A live, unconsumed, unexpired invitation is outstanding.
    invite_outstanding: bool
    #: How many sessions would authenticate right now.
    live_sessions: int


class PortalAccessRevoked(BaseModel):
    """What the kill switch actually retired."""

    patient_id: str
    sessions_revoked: int
    invites_revoked: int


@router.post(
    "/api/patients/{patient_id}/portal-invite",
    response_model=PortalInviteAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def issue_portal_invite(  # noqa: PLR0913 — FastAPI Depends-injected params are idiomatic
    patient_id: str,
    request: Request,
    user: Annotated[User, Depends(require_active_subscription)],
    patients: Annotated[PatientRepository, Depends(get_patient_repository)],
    stores: Annotated[PortalStores, Depends(get_portal_stores)],
    delivery: Annotated[PortalInviteDelivery, Depends(get_invite_delivery)],
    sms: Annotated[SmsGateway, Depends(get_sms_gateway)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> PortalInviteAccepted:
    """Invite one patient to the portal: text the code, email the link.

    422 when the patient has no email address or no phone number. Both are
    required and neither is a formality — they are the two channels the two
    factors travel on, and an invitation with only one of them is a single
    factor wearing a two-factor shape.

    503 when either channel is not configured, checked BEFORE anything is
    minted or sent, so an unconfigured deployment never leaves a patient
    holding a code for a link that will not arrive.

    The link opens the practice's own portal page, so the practice's address
    is resolved here — minted on the first invitation rather than made into a
    step a clinician has to go and do first.
    """
    patient = _patient_or_404(patients, patient_id, user.id)
    if not patient.email or not patient.phone:
        raise UnprocessableEntityError(
            "Add an email address and a mobile number to this chart first.",
            code="PATIENT_CONTACT_INCOMPLETE",
        )
    slug = _practice_slug_for(user)

    try:
        # Both channels, and somewhere for the link to point, before the
        # first side effect.
        delivery.check_ready()
        sms.check_ready()
        service = build_portal_auth_service(
            store=stores.challenges, sessions=stores.sessions, sms=sms
        )
        issued = service.issue_invite(
            patient_id=patient_id, tenant=stores.tenant, phone=patient.phone
        )
        delivery.send_invite(
            to_email=patient.email,
            link=build_invite_link(slug=slug, token=issued.token),
        )
    except DeliveryNotConfiguredError:
        raise _delivery_unavailable() from None

    audit.log(
        AuditAction.PATIENT_PORTAL_INVITE_ISSUED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        patient=patient,
        # A random handle to the challenge row. The token, the link and the
        # code are recorded nowhere.
        changes={"invite_jti": issued.jti},
    )
    return PortalInviteAccepted(
        patient_id=patient_id,
        invite_expires_at=issued.expires_at,
    )


@router.get(
    "/api/patients/{patient_id}/portal-access",
    response_model=PortalAccessState,
)
def get_portal_access(
    patient_id: str,
    user: Annotated[User, Depends(require_active_subscription)],
    patients: Annotated[PatientRepository, Depends(get_patient_repository)],
    stores: Annotated[PortalStores, Depends(get_portal_stores)],
) -> PortalAccessState:
    """Whether this patient has an invitation in flight, and how many live
    sessions.

    Counters only — never the invitation, never a token id, nothing the
    patient said. A read of access state rather than of chart content, so no
    audit entry.
    """
    _patient_or_404(patients, patient_id, user.id)
    now = int(utc_now().timestamp())
    return PortalAccessState(
        patient_id=patient_id,
        invite_outstanding=stores.challenges.has_outstanding(patient_id),
        live_sessions=stores.sessions.live_count_for_patient(patient_id, now=now),
    )


@router.delete(
    "/api/patients/{patient_id}/portal-access",
    response_model=PortalAccessRevoked,
)
def revoke_portal_access(  # noqa: PLR0913 — FastAPI Depends-injected params are idiomatic
    patient_id: str,
    request: Request,
    user: Annotated[User, Depends(require_active_subscription)],
    patients: Annotated[PatientRepository, Depends(get_patient_repository)],
    stores: Annotated[PortalStores, Depends(get_portal_stores)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> PortalAccessRevoked:
    """Cut off a patient's portal access now.

    Revokes every live session AND burns every outstanding invitation. Both
    halves matter: revoking sessions while an unredeemed invitation is in
    flight would undo itself the moment the patient clicked the link.

    Idempotent — revoking access nobody has returns zeros, not an error.
    """
    patient = _patient_or_404(patients, patient_id, user.id)
    # Revocation sends nothing, so the kill switch takes the refusing stub
    # as its gateway rather than a real one — it must keep working when a
    # provider is down, and it is the control that matters most precisely
    # when something is wrong.
    service = build_portal_auth_service(
        store=stores.challenges,
        sessions=stores.sessions,
        sms=DeliveryNotConfigured("SMS"),
    )

    sessions_revoked = service.revoke_patient(patient_id=patient_id)
    invites_revoked = stores.challenges.consume_outstanding(patient_id)

    audit.log(
        AuditAction.PATIENT_PORTAL_INVITE_REVOKED,
        user,
        request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient_id,
        patient=patient,
        changes={"sessions_revoked": sessions_revoked, "invites_revoked": invites_revoked},
    )
    return PortalAccessRevoked(
        patient_id=patient_id,
        sessions_revoked=sessions_revoked,
        invites_revoked=invites_revoked,
    )


# ── patient: redeem + refresh ───────────────────────────────────────────


class RedeemRequest(BaseModel):
    """The two factors: what the email carried, and what the text carried."""

    token: str = Field(min_length=1, max_length=4096)
    otp: str = Field(min_length=1, max_length=16)


class RefreshRequest(BaseModel):
    """The session token to rotate.

    In the body rather than an ``Authorization`` header on purpose. This is
    not a patient-context route: ``get_patient_context`` resolves a principal
    for routes that serve one, and rotation is what keeps a principal
    resolvable in the first place. Taking the token as a parameter keeps that
    dependency off a route whose whole job is to replace the row it would
    have checked.
    """

    session_token: str = Field(min_length=1, max_length=4096)


class PatientSessionResponse(BaseModel):
    """A minted patient-session credential."""

    session_token: str
    token_type: str = "bearer"
    expires_at: int


@router.post(
    "/api/patient/auth/redeem",
    response_model=PatientSessionResponse,
    dependencies=[
        Depends(truly_public),
        Depends(require_portal_redeem_rate_limit),
        _SUBSCRIPTION_EXEMPT,
    ],
)
def redeem_portal_invite(
    body: RedeemRequest,
    request: Request,
    gateway: Annotated[PortalTenantGateway, Depends(get_portal_tenant_gateway)],
) -> PatientSessionResponse:
    """Exchange a magic link plus its texted code for a patient session.

    The only unauthenticated route here, so the order of operations is the
    security argument:

    1. Verify the invite signature. Nothing else happens until this passes,
       so a forged token never reaches the database and never selects a
       schema.
    2. Check the tenant claim names a real practice schema, then enter it.
    3. Redeem against that practice's challenge row: single-use,
       attempt-capped, expiry-checked, code verified against a peppered
       hash.
    4. Mint a session — and record the row that lets it be revoked — in the
       same transaction that burns the invitation.

    Every failure is the same 401. Rate-limited per address by the route
    dependency, and per invitation once the signature is known good, so one
    invitation cannot be ground on from many addresses.
    """
    claims = _verified_invite_claims(body.token)
    check_portal_redeem_invite_limit(claims.jti)

    with _tenant_work(gateway, claims.tenant, request) as work:
        service = build_portal_auth_service(store=work.challenges, sessions=work.sessions)
        try:
            minted = service.redeem(token=body.token, otp=body.otp)
        except PortalAuthError:
            # Consumed, expired, attempt-capped, wrong code — one answer for
            # all of them. The attempt counter the store just bumped is the
            # part that must survive, so commit before refusing.
            work.commit()
            raise _unauthenticated() from None

        work.record_redemption(minted.patient_id, minted.jti)
        work.commit()
        return PatientSessionResponse(
            session_token=minted.session_token, expires_at=minted.expires_at
        )


@router.post(
    "/api/patient/auth/refresh",
    response_model=PatientSessionResponse,
    dependencies=[
        Depends(truly_public),
        Depends(require_portal_refresh_rate_limit),
        _SUBSCRIPTION_EXEMPT,
    ],
)
def refresh_portal_session(
    body: RefreshRequest,
    request: Request,
    gateway: Annotated[PortalTenantGateway, Depends(get_portal_tenant_gateway)],
) -> PatientSessionResponse:
    """Rotate a live patient session into a fresh one.

    Same shape as redeem and for the same reasons: signature first, schema
    second, database third, one uniform 401 for every refusal. The presented
    session is retired as part of the rotation, so a leaked token stops
    working the moment the real patient renews — and a revoked session can
    never be refreshed back into existence.
    """
    claims = _verified_session_claims(body.session_token)

    with _tenant_work(gateway, claims.tenant, request) as work:
        service = build_portal_auth_service(store=work.challenges, sessions=work.sessions)
        try:
            minted = service.refresh(token=body.session_token)
        except PortalAuthError:
            raise _unauthenticated() from None

        work.commit()
        return PatientSessionResponse(
            session_token=minted.session_token, expires_at=minted.expires_at
        )


# ── the two steps every patient-facing route takes first ────────────────


def _verified_invite_claims(token: str) -> tokens.InviteClaims:
    """Signature, type and expiry, then a tenant claim we would enter.

    Both checks refuse with the same 401, and both happen before any
    database work: a forged token must not reach a connection, let alone
    choose which schema it points at.
    """
    try:
        claims = tokens.verify_invite_token(signing_key=_signing_key(), token=token)
    except PortalAuthError:
        raise _unauthenticated() from None
    if not _is_tenant_schema(claims.tenant):
        logger.warning("Portal redemption presented a non-practice schema claim")
        raise _unauthenticated()
    return claims


def _verified_session_claims(token: str) -> tokens.SessionClaims:
    """The session-token half of :func:`_verified_invite_claims`."""
    try:
        claims = tokens.verify_session_token(signing_key=_signing_key(), token=token)
    except PortalAuthError:
        raise _unauthenticated() from None
    if not _is_tenant_schema(claims.tenant):
        logger.warning("Portal refresh presented a non-practice schema claim")
        raise _unauthenticated()
    return claims


@contextmanager
def _tenant_work(
    gateway: PortalTenantGateway, tenant: str, request: Request
) -> Iterator[PortalTenantWork]:
    """The gateway, with every non-HTTP failure collapsed into the 401.

    Two things must not escape a patient-facing route. A malformed schema
    name (``ValueError`` out of the identifier validation) would be a 500,
    which is distinguishable from the uniform 401 and so an oracle for "your
    token parsed but its tenant was junk". And a driver exception would
    carry its own message, which routinely quotes the offending value —
    here, a credential. Both become the same 401; the traceback goes to the
    log instead.
    """
    try:
        with gateway.open(tenant, request) as work:
            yield work
    except HTTPException:
        raise
    except ValueError:
        logger.warning("Portal sign-in presented a malformed schema claim")
        raise _unauthenticated() from None
    except Exception:
        logger.exception("Portal sign-in failed")
        raise _unauthenticated() from None
