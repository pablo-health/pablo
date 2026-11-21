# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Inviting a clinician into a practice, and redeeming the invitation.

A practice's owner or an admin invites a clinician by address
(:func:`create_invite`). The invitation is a link carrying a random token;
only the token's sha256 is stored (``platform.practice_invites``), and the
token itself exists only in the link that is emailed. The link works for
:data:`INVITE_TTL`, once.

Whoever redeems the link (:func:`redeem`) joins the practice, whatever address
their account signs in with, so a clinician whose sign-in differs from the
address they were invited at is not stranded. When the two differ the redeem
is refused until the caller confirms which account to use.

Redeeming, in one transaction on the request session:

1. lock the invitation, and refuse one that is spent, revoked or expired;
2. refuse an account that already belongs to a practice: one practice per
   account (``ALREADY_IN_PRACTICE``);
3. refuse a differing address unless confirmed (``EMAIL_MISMATCH``);
4. create the account if this is its first sign-in;
5. grant and map the account's address (``AllowlistRepository.add``), so the
   member is listed with the practice's other clinicians, and write the
   account's membership (``joined_via = 'invite'``), which is what its next
   request resolves through;
6. mark the invitation redeemed, and audit it in the practice.

Membership is created here and nowhere else: a pending invitation changes no
member count. Once the redeem commits, the membership hooks are told
(``app.db.practice_members.notify_membership_change``).

The membership is written through the ORM, so the practice-resolution cache
drops the account's cached answer when the redeem commits
(``app.db.practice_resolution_cache``).

Audit rows and logs carry ids only.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError

from ..api_errors import (
    ConflictError,
    ForbiddenError,
    GoneError,
    NotFoundError,
    ServiceUnavailableError,
)
from ..db.platform_models import (
    SCHEMA_READY,
    EmailTenantMappingRow,
    PracticeInviteRow,
    PracticeMemberRow,
    PracticeRow,
)
from ..models import User
from ..utcnow import utc_now
from .email_sender import OutboundEmail

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from ..auth.invite_identity import VerifiedInvitee
    from .email_sender import EmailSender

logger = logging.getLogger(__name__)

#: How long an invitation link works.
INVITE_TTL = timedelta(days=7)

#: ``kind`` on the invitation email, for logs and tests.
INVITE_EMAIL_KIND = "practice_invite"


def new_token() -> str:
    """A fresh link token: 32 random bytes, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """What is stored for ``token``: its sha256, hex."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def invite_link(app_url: str, token: str) -> str:
    """The page a clinician opens to accept."""
    return f"{app_url.rstrip('/')}/invite/{token}"


def is_open(invite: PracticeInviteRow, now: datetime) -> bool:
    """Whether the invitation can still be redeemed."""
    return invite.redeemed_at is None and invite.revoked_at is None and invite.expires_at > now


# --- the practice's side -----------------------------------------------------


def create_invite(
    db: Session, practice: PracticeRow, *, email: str, invited_by: str
) -> tuple[PracticeInviteRow, str]:
    """Record an invitation into ``practice``; returns it and its link token.

    The token is returned for the email alone and is never stored.
    """
    token = new_token()
    now = utc_now()
    invite = PracticeInviteRow(
        id=str(uuid.uuid4()),
        practice_id=practice.id,
        email=email.strip(),
        token_hash=hash_token(token),
        invited_by=invited_by,
        created_at=now,
        expires_at=now + INVITE_TTL,
    )
    db.add(invite)
    db.flush()
    return invite, token


def pending_invites(db: Session, practice_id: str) -> list[PracticeInviteRow]:
    """The practice's open invitations, newest first."""
    now = utc_now()
    return list(
        db.execute(
            select(PracticeInviteRow)
            .where(
                PracticeInviteRow.practice_id == practice_id,
                PracticeInviteRow.redeemed_at.is_(None),
                PracticeInviteRow.revoked_at.is_(None),
                PracticeInviteRow.expires_at > now,
            )
            .order_by(PracticeInviteRow.created_at.desc())
        ).scalars()
    )


def revoke_invite(db: Session, practice_id: str, invite_id: str) -> bool:
    """Revoke one of the practice's invitations. Whether this call revoked it.

    404 for an invitation that is not this practice's; 409 ``INVITE_REDEEMED``
    for one already redeemed, since its member has joined and revoking the
    link would not undo that. Revoking a revoked or expired one changes
    nothing.
    """
    try:
        uuid.UUID(invite_id)
    except ValueError:
        raise NotFoundError("No invitation with that id", code="INVITE_NOT_FOUND") from None
    invite = db.execute(
        select(PracticeInviteRow)
        .where(PracticeInviteRow.id == invite_id, PracticeInviteRow.practice_id == practice_id)
        .with_for_update()
    ).scalar_one_or_none()
    if invite is None:
        raise NotFoundError("No invitation with that id", code="INVITE_NOT_FOUND")
    if invite.redeemed_at is not None:
        raise ConflictError("This invitation has already been accepted", code="INVITE_REDEEMED")
    if not is_open(invite, utc_now()):
        return False
    invite.revoked_at = utc_now()
    db.flush()
    return True


def send_invite_email(
    sender: EmailSender,
    *,
    to: str,
    practice_name: str,
    inviter_name: str | None,
    link: str,
) -> bool:
    """Email the link. Whether it was handed to a backend that delivers.

    Best effort: a failure is logged by type and reported as not sent, so the
    invitation still exists and the inviter is told the email did not go out.
    """
    if not sender.can_deliver:
        return False
    who = inviter_name.strip() if inviter_name and inviter_name.strip() else "A colleague"
    text = (
        f"{who} invited you to join {practice_name} on Pablo.\n\n"
        f"To accept, open this link and sign in:\n{link}\n\n"
        f"The link works once and expires in {INVITE_TTL.days} days.\n"
    )
    try:
        sender.send(
            OutboundEmail(
                to=to,
                subject=f"Join {practice_name} on Pablo",
                text=text,
                kind=INVITE_EMAIL_KIND,
            )
        )
    except Exception as exc:
        logger.warning("practice invite email failed (%s)", type(exc).__name__)
        return False
    return True


# --- the invitee's side ------------------------------------------------------


def _find_by_token(db: Session, token: str, *, lock: bool) -> PracticeInviteRow:
    stmt = select(PracticeInviteRow).where(PracticeInviteRow.token_hash == hash_token(token))
    if lock:
        stmt = stmt.with_for_update()
    invite = db.execute(stmt).scalar_one_or_none()
    if invite is None:
        raise NotFoundError("This invitation link isn't valid", code="INVITE_NOT_FOUND")
    return invite


def _gone() -> GoneError:
    return GoneError(
        "This invitation has been used, withdrawn or has expired", code="INVITE_UNAVAILABLE"
    )


def preview(db: Session, token: str) -> tuple[str, str]:
    """The practice's name and the invited address, for an open invitation."""
    invite = _find_by_token(db, token, lock=False)
    practice = db.get(PracticeRow, invite.practice_id)
    if practice is None or not practice.is_active or not is_open(invite, utc_now()):
        raise _gone()
    return practice.name, invite.email


@dataclass(frozen=True)
class Redeemed:
    """What a redeem did, for the route to audit and report."""

    invite_id: str
    practice_id: str
    practice_name: str
    schema_name: str
    user: User
    email_matched: bool


def _already_in_practice() -> ConflictError:
    return ConflictError(
        "This account already belongs to a practice and can't join another",
        code="ALREADY_IN_PRACTICE",
    )


def _belongs_to_a_practice(db: Session, user_id: str | None, email: str) -> bool:
    """Whether the account, or the address it signs in with, already has a practice."""
    if db.get(EmailTenantMappingRow, email) is not None:
        return True
    if user_id is None:
        return False
    try:
        uuid.UUID(user_id)
    except ValueError:
        return False
    return bool(
        db.execute(
            select(
                exists().where(PracticeMemberRow.user_id == user_id)
                | exists().where(PracticeRow.owner_user_id == user_id)
            )
        ).scalar()
    )


def _create_account(db: Session, invitee: VerifiedInvitee, now: datetime) -> User:
    """The account for a first sign-in, as ``_resolve_user`` creates one."""
    from ..auth.service import _resolve_pablo_user_id
    from ..repositories.postgres.identity import PostgresIdentityRepository
    from ..repositories.postgres.user import PostgresUserRepository

    user_id = _resolve_pablo_user_id(
        None,
        invitee.subject_id,
        PostgresIdentityRepository(db),
        create_if_missing=True,
        provider=invitee.provider,
    )
    user = User(
        id=user_id,
        email=invitee.email,
        name=str(invitee.claims.get("name") or invitee.email),
        created_at=now,
        picture=invitee.claims.get("picture"),
        status="approved",
    )
    PostgresUserRepository(db).update(user)
    return user


def redeem(
    db: Session,
    token: str,
    invitee: VerifiedInvitee,
    *,
    confirm_mismatch: bool,
) -> Redeemed:
    """Join the invitee's account to the invitation's practice. See the module docstring.

    Every refusal is raised before anything is written. The caller commits.
    """
    from ..repositories.postgres.allowlist import PostgresAllowlistRepository
    from ..repositories.postgres.user import PostgresUserRepository

    now = utc_now()
    invite = _find_by_token(db, token, lock=True)
    # Shared, for the reason a membership claim takes it: a deactivation in
    # flight is waited for rather than read past (``practice_members._CLAIM_SQL``).
    practice = db.execute(
        select(PracticeRow).where(PracticeRow.id == invite.practice_id).with_for_update(read=True)
    ).scalar_one_or_none()
    if practice is None or not practice.is_active or not is_open(invite, now):
        raise _gone()
    if practice.provisioning_status != SCHEMA_READY:
        raise ServiceUnavailableError(
            "This practice is being set up. Try again in a minute.", code="PRACTICE_NOT_READY"
        )

    users = PostgresUserRepository(db)
    email = invitee.email
    existing = users.get(invitee.user_id) if invitee.user_id else None
    if existing is not None and existing.status == "disabled":
        raise ForbiddenError("Your account has been disabled", code="USER_DISABLED")
    if _belongs_to_a_practice(db, invitee.user_id, email):
        raise _already_in_practice()
    if existing is None and users.get_by_email(email) is not None:
        # Another account holds this address. Never attach a sign-in to it by
        # email; ``_resolve_user`` refuses the same case the same way.
        raise ConflictError(
            "This email is already linked to a different sign-in. "
            "Contact your administrator to restore access.",
            code="ACCOUNT_CONFLICT",
        )
    invited = invite.email.strip().lower()
    email_matched = invited == email
    if not email_matched and not confirm_mismatch:
        raise ConflictError(
            "You're signed in with a different email than the one invited",
            {"invited_email": invite.email, "account_email": email},
            code="EMAIL_MISMATCH",
        )

    # Writes start here. A unique or key violation means another redeem for
    # this account or address committed first; the whole transaction goes.
    try:
        user = existing or _create_account(db, invitee, now)
        PostgresAllowlistRepository(db).add(
            email, invite.invited_by or user.id, practice_id=practice.id
        )
        db.add(
            PracticeMemberRow(
                user_id=user.id,
                practice_id=practice.id,
                joined_at=now,
                joined_via="invite",
                invited_by=invite.invited_by,
                claimed_email=None,
            )
        )
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _already_in_practice() from None
    invite.redeemed_at = now
    invite.redeemed_by_user_id = user.id
    db.flush()
    logger.info(
        "practice invite %s redeemed into practice %s by account %s",
        invite.id,
        practice.id,
        user.id,
        extra={"practice_id": practice.id, "user_id": user.id},
    )
    return Redeemed(
        invite_id=invite.id,
        practice_id=practice.id,
        practice_name=practice.name,
        schema_name=practice.schema_name,
        user=user,
        email_matched=email_matched,
    )
