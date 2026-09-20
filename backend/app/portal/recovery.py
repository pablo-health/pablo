# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Getting back in without having to ask anyone.

``POST /api/portal/practices/{slug}/recover`` takes an email address and,
if it belongs to someone this practice has given portal access to, sends
them a fresh invitation — the same magic link plus texted code the clinician
sends, minted through the same path with the same lifetime.

It is the second unauthenticated route on this surface (redemption is the
first) and the only one whose request body is a piece of personal data the
caller supplies. Everything below follows from those two facts.

**One answer, always.** ``202`` with an empty body, for a match, for an
address nobody here has, for a practice that does not exist, for a patient
whose access was withdrawn. The response is not a channel: whether an
address is on a therapy practice's patient list is exactly the kind of thing
this endpoint would otherwise leak to anybody who can type, and "is this
person in treatment" is not a fact a stranger gets to test for.

**Never a knowledge question.** No date of birth, no last visit, no "what
are the last four digits". A knowledge check would give the caller a second
answer to read — a different error, a different shape, a different delay —
and would gate the recovery on something an acquaintance usually knows.
Possession of the email address is the first factor, and the texted code
minted alongside the link is the second, which is exactly the pair the
original invitation used.

**Only an active grant mints.** Access withdrawn by the practice is not
recoverable by the person it was withdrawn from — a clinician re-invite is
the only way back, which is what makes the kill switch mean something. See
:meth:`~app.portal.service.PortalAuthService.has_active_grant` for how that
is decided without a column that records it.

**What an attacker gets, and what bounds it.** The response tells them
nothing, so the remaining attacks are on volume and on the mailbox:

* *Sweeping a list of addresses to find patients.* Bounded by two windows —
  per address and per practice (``portal-recover``) — and gated by the
  deployment's CAPTCHA provider, the same one the public booking write
  carries. The per-practice window is the one that matters: a caller with
  many source addresses walks through a per-IP limit, and what they would
  be walking through is one practice's patient list.
* *Mailing invitations at somebody as harassment.* Same windows. The
  invitation itself is inert without the code, which goes to a phone the
  caller does not have.
* *Timing.* A match does more work than a miss — a tenant session, a
  lookup, an SMS, an email. The windows above are the mitigation rather
  than constant-time execution: five requests an hour per address is not
  enough samples to time anything, and padding a route that sends real mail
  to a fixed duration would mean either delaying every legitimate recovery
  to the slowest case or lying about how long the send took.
* *A wrong or stale email on the chart.* The invitation goes where the
  chart says, which may be an address the patient no longer holds. That is
  the same exposure the clinician's own invite route has and the same one
  it is bounded by: the link alone is one factor, and the code goes to the
  phone.

**Two patients, one address.** A shared family address matching two charts
mints nothing, deliberately. Picking one would be a guess about which
person is asking, and the wrong guess sends one patient's portal credential
toward another's inbox.

Nothing here logs an email address, a phone number, a link or a code. A
miss is logged as a salted digest of the address and the practice slug, so
that a burst of misses is recognisable as a sweep without the log becoming a
list of who was swept for.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from ..api_errors import ForbiddenError
from ..auth.route_access import subscription_exempt
from ..auth.route_security import truly_public
from ..models.validators import validate_email
from ..rate_limit import (
    check_portal_recover_slug_limit,
    get_client_ip,
    require_portal_recover_rate_limit,
)

# Runtime imports: FastAPI resolves these annotations when it builds the
# route, so they cannot live in a TYPE_CHECKING block.
from ..services.captcha import CaptchaVerifier, get_captcha_verifier
from ..settings import get_settings
from .delivery import (
    DeliveryNotConfiguredError,
    PortalInviteDelivery,
    SmsGateway,
)
from .factory import (
    build_invite_link,
    build_portal_auth_service,
    get_invite_delivery,
    get_sms_gateway,
)
from .recovery_gateway import RecoveryGateway, get_recovery_gateway

logger = logging.getLogger(__name__)

router = APIRouter(tags=["patient-portal"])

#: Refused identically to an invalid one, and identically to the message the
#: public booking write uses — a caller should not be able to tell which
#: surface turned them away.
_CAPTCHA_FAILED = "Verification failed. Please refresh and try again."


class PortalRecoveryRequest(BaseModel):
    """The one thing the caller supplies.

    Shape-validated before it reaches a query, through the same
    ``validate_email`` every other address in the engine goes through. A
    value that is not an address is a 422 and is therefore the one way this
    route answers differently — which is safe, because it says something
    about the REQUEST's shape and nothing about whether any patient matches
    it.
    """

    email: str = Field(min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email_field(cls, v: str) -> str:
        validated = validate_email(v)
        if validated is None:
            raise ValueError("Invalid email format")
        return validated


def require_recovery_captcha(
    request: Request,
    verifier: Annotated[CaptchaVerifier, Depends(get_captcha_verifier)],
    x_captcha_token: Annotated[str | None, Header(alias="X-Captcha-Token")] = None,
) -> None:
    """Refuse the recovery request unless the CAPTCHA token verifies.

    A no-op under the default no-provider verifier, and the same
    header, verifier and message as the public booking write — one CAPTCHA
    decision per deployment rather than one per surface.

    Declared as a dependency so it runs before the handler, and therefore
    before the address in the body reaches a query. The per-address rate
    limit runs first, so a bot burning verify calls burns its window too.
    """
    if not verifier.verify(x_captcha_token, get_client_ip(request)):
        raise ForbiddenError(_CAPTCHA_FAILED)


@router.post(
    "/api/portal/practices/{slug}/recover",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(truly_public),
        Depends(require_portal_recover_rate_limit),
        Depends(require_recovery_captcha),
        Depends(subscription_exempt),
    ],
)
def recover_portal_access(  # noqa: PLR0913 — FastAPI Depends-injected params are idiomatic
    slug: str,
    body: PortalRecoveryRequest,
    request: Request,
    gateway: Annotated[RecoveryGateway, Depends(get_recovery_gateway)],
    delivery: Annotated[PortalInviteDelivery, Depends(get_invite_delivery)],
    sms: Annotated[SmsGateway, Depends(get_sms_gateway)],
) -> Response:
    """Send a fresh sign-in link, if there is someone here to send it to.

    Always ``202``, always an empty body. The branches below decide whether
    an invitation is minted, never what the caller is told.

    Exempt from the subscription gate for the same reason redemption is: a
    patient has no way to know or fix a practice's billing state, and this
    is authentication rather than practice data.
    """
    check_portal_recover_slug_limit(slug)

    try:
        _attempt_recovery(
            gateway=gateway,
            delivery=delivery,
            sms=sms,
            slug=slug,
            email=body.email,
            request=request,
        )
    except Exception:
        # Nothing that goes wrong downstream — an unreachable mail server, a
        # schema that vanished, a driver error — may become a second answer.
        # The exception's own message routinely quotes the offending value,
        # which here is somebody's email address, so it goes to the log with
        # no message of ours attached and the caller gets the same 202.
        logger.exception("Portal recovery failed after the response was decided")

    return Response(status_code=status.HTTP_202_ACCEPTED)


def _attempt_recovery(  # noqa: PLR0913 — one parameter per injected collaborator
    *,
    gateway: RecoveryGateway,
    delivery: PortalInviteDelivery,
    sms: SmsGateway,
    slug: str,
    email: str,
    request: Request,
) -> None:
    """Mint and send, if every condition holds. Otherwise do nothing at all.

    Split out from the route so the route reads as "one answer, whatever
    happens here", and so that every early return is visibly a return to
    that same answer. Every one of them also logs a reason, which is how a
    sweep is recognisable afterwards without the response ever having said
    anything.
    """
    schema = gateway.resolve(slug)
    if schema is None:
        _log_miss(slug=slug, email=email, reason="no_practice")
        return

    with gateway.open(schema, request) as work:
        target = work.find_patient(email)
        if target is None:
            # No chart with this address, or more than one — see the module
            # docstring on why a shared family address mints nothing.
            _log_miss(slug=slug, email=email, reason="no_patient")
            return
        if not target.phone:
            # The step-up code has nowhere to go, so an invitation would be
            # one factor wearing a two-factor shape — the same refusal the
            # clinician's invite route answers with a 422, except that this
            # caller is told nothing.
            _log_miss(slug=slug, email=email, reason="no_phone")
            return

        service = build_portal_auth_service(store=work.challenges, sessions=work.sessions, sms=sms)
        if not service.has_active_grant(patient_id=target.patient_id):
            # Never invited, or invited and then cut off. Recovery does not
            # undo a clinician's revoke; only a clinician re-invite does.
            _log_miss(slug=slug, email=email, reason="no_grant")
            return

        try:
            # Both channels, and somewhere for the link to point, before the
            # first side effect — the same order the clinician's invite
            # route checks them in.
            delivery.check_ready()
            sms.check_ready()
            issued = service.issue_invite(
                patient_id=target.patient_id, tenant=schema, phone=target.phone
            )
            # To the address ON THE CHART, not the one the caller typed.
            # The two match case-insensitively — that is how the row was
            # found — and sending to the stored value means the recipient is
            # the practice's record of this person rather than a string a
            # stranger supplied.
            delivery.send_invite(
                to_email=target.email or email, link=build_invite_link(issued.token)
            )
        except DeliveryNotConfiguredError:
            # A deployment with no channels wired cannot recover anyone. The
            # clinician's invite route says so with a 503; this one cannot,
            # because the caller must not learn that the address matched.
            logger.warning("Portal recovery could not be delivered: channel not configured")
            return

        work.record_request(target.patient_id, issued.jti)
        work.commit()


def _log_miss(*, slug: str, email: str, reason: str) -> None:
    """Record that a recovery request matched nothing, without saying who.

    The digest is keyed on the deployment's portal signing key, so it is
    stable within one deployment — a burst of requests for the same address
    is recognisable as a sweep — and useless outside it. An unkeyed hash of
    an email address is not anonymous: the space of addresses is small
    enough to enumerate, so a plain digest in a log is the address.

    Truncated to twelve hex characters, which is plenty to group a burst by
    and far too short to confirm a guess against.
    """
    key = get_settings().portal_token_signing_key.get_secret_value().encode()
    digest = hmac.new(key, email.strip().lower().encode(), hashlib.sha256).hexdigest()[:12]
    logger.info(
        "Portal recovery matched nothing",
        extra={"practice_slug": slug, "email_handle": digest, "reason": reason},
    )
