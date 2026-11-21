# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Inviting a clinician into the practice, and accepting the invitation.

The practice's side, for its owner or an admin (``require_practice_admin``),
always on the caller's own practice:

* ``POST /api/practice/invites`` — invite an address; the link is emailed and
  never returned.
* ``GET /api/practice/invites`` — the open invitations.
* ``DELETE /api/practice/invites/{invite_id}`` — withdraw one.

The invitee's side:

* ``POST /api/invites/preview`` — public: the practice's name and the invited
  address, or 410 once the link is spent.
* ``POST /api/invites/redeem`` — a verified sign-in with no practice
  (``app.auth.invite_identity``) joins the practice.

The link token travels in the JSON body of both, never in a URL path or query:
a platform request log records the full URL, and anyone who can read that log
and sign in could otherwise redeem a pending link. The emailed link carries it
in the fragment (``/invite#<token>``), which a browser never sends to a server.

The rules live in ``app.services.practice_invites``. Create, revoke and redeem
are audited with ids only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from ..auth.invite_identity import VerifiedInvitee, get_verified_invitee
from ..auth.route_security import truly_public
from ..auth.service import get_current_user, require_active_subscription
from ..db import arm_current_user_id, get_db_session, set_tenant_schema
from ..db.platform_models import PracticeRow  # noqa: TC001 — annotation used at runtime
from ..db.practice_members import notify_membership_change
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.practice_invite import (
    CreatePracticeInviteRequest,
    CreatePracticeInviteResponse,
    InvitePreviewRequest,
    InvitePreviewResponse,
    PracticeInvite,
    PracticeInvitesResponse,
    RedeemInviteRequest,
    RedeemInviteResponse,
)
from ..services import AuditService, get_audit_service
from ..services import practice_invites as invites
from ..services.email_sender import EmailSender, email_sender_from_settings
from ..settings import Settings, get_settings
from .users import require_practice_admin

router = APIRouter(
    prefix="/api/practice/invites",
    tags=["practice"],
    dependencies=[Depends(require_active_subscription)],
)
invitee_router = APIRouter(prefix="/api/invites", tags=["practice"])


def get_invite_email_sender(settings: Settings = Depends(get_settings)) -> EmailSender:
    """FastAPI dependency: the deployment's email backend, overridable in tests."""
    return email_sender_from_settings(settings)


def _view(invite: invites.PracticeInviteRow) -> PracticeInvite:
    return PracticeInvite(
        id=invite.id,
        email=invite.email,
        invited_by=invite.invited_by,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_practice_invite(
    body: CreatePracticeInviteRequest,
    http_request: Request,
    practice: PracticeRow = Depends(require_practice_admin),
    user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    sender: EmailSender = Depends(get_invite_email_sender),
    settings: Settings = Depends(get_settings),
) -> CreatePracticeInviteResponse:
    """Invite an address into the caller's practice, and email it the link."""
    db = get_db_session()
    invite, token = invites.create_invite(db, practice, email=body.email, invited_by=user.id)
    audit.log(
        AuditAction.PRACTICE_INVITE_CREATED,
        user,
        http_request,
        resource_type=ResourceType.PRACTICE,
        resource_id=practice.id,
        changes={"invite_id": invite.id},
    )
    # Committed before the email goes, so no link is ever sent for an
    # invitation that was not saved.
    db.commit()
    sent = invites.send_invite_email(
        sender,
        to=invite.email,
        practice_name=practice.name,
        inviter_name=user.name,
        link=invites.invite_link(settings.app_url, token),
    )
    return CreatePracticeInviteResponse(**_view(invite).model_dump(), email_sent=sent)


@router.get("")
def list_practice_invites(
    practice: PracticeRow = Depends(require_practice_admin),
) -> PracticeInvitesResponse:
    """The caller's practice's open invitations, newest first."""
    pending = invites.pending_invites(get_db_session(), practice.id)
    return PracticeInvitesResponse(invites=[_view(invite) for invite in pending])


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_practice_invite(
    invite_id: str,
    http_request: Request,
    practice: PracticeRow = Depends(require_practice_admin),
    user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """Withdraw one of the practice's invitations. Withdrawing twice changes nothing."""
    if invites.revoke_invite(get_db_session(), practice.id, invite_id):
        audit.log(
            AuditAction.PRACTICE_INVITE_REVOKED,
            user,
            http_request,
            resource_type=ResourceType.PRACTICE,
            resource_id=practice.id,
            changes={"invite_id": invite_id},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@invitee_router.post("/preview", dependencies=[Depends(truly_public)])
def preview_invite(body: InvitePreviewRequest) -> InvitePreviewResponse:
    """The practice an open invitation is for, and the address it was sent to.

    Public, so the page can say who invited whom before anyone signs in. The
    token is 32 random bytes; nothing else about the practice is returned.
    """
    practice_name, email = invites.preview(get_db_session(), body.token)
    return InvitePreviewResponse(practice_name=practice_name, email=email)


@invitee_router.post("/redeem")
def redeem_invite(
    body: RedeemInviteRequest,
    http_request: Request,
    invitee: VerifiedInvitee = Depends(get_verified_invitee),
    audit: AuditService = Depends(get_audit_service),
) -> RedeemInviteResponse:
    """Join the signed-in account to the invitation's practice.

    410 for a spent, withdrawn or expired link; 409 ``ALREADY_IN_PRACTICE``
    for an account that belongs to a practice; 409 ``EMAIL_MISMATCH``, with
    both addresses, when the account signs in with another address and
    ``confirm_mismatch`` is false. Nothing is written on any refusal.
    """
    db = get_db_session()
    redeemed = invites.redeem(db, body.token, invitee, confirm_mismatch=body.confirm_mismatch)
    # The audit row lives in the practice the account just joined, written as
    # that account, in the same transaction as the membership.
    set_tenant_schema(db, redeemed.schema_name)
    arm_current_user_id(db, redeemed.user.id)
    audit.log(
        AuditAction.PRACTICE_INVITE_REDEEMED,
        redeemed.user,
        http_request,
        resource_type=ResourceType.PRACTICE,
        resource_id=redeemed.practice_id,
        changes={"invite_id": redeemed.invite_id, "email_matched": redeemed.email_matched},
    )
    db.commit()
    notify_membership_change(redeemed.practice_id, redeemed.user.id, "joined")
    return RedeemInviteResponse(
        practice_id=redeemed.practice_id, practice_name=redeemed.practice_name
    )
