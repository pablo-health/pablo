# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's agreement, as a clinician who does not own the practice meets it.

The agreement binds the practice. Its owner or an admin accepts it; everyone
else is cleared by that acceptance and is shown a one-time notice instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..api_errors import BadRequestError, ConflictError
from ..auth.service import get_baa_version, get_current_user_no_mfa
from ..db.platform_models import PracticeRow  # noqa: TC001 — annotation used at runtime
from ..models import BAAStatusResponse, User
from ..models.audit import AuditAction
from ..people_term_lookup import practice_row_for
from ..repositories import UserRepository, get_user_repository
from ..services import AuditService, get_audit_service
from ..utcnow import utc_now
from .users import _is_practice_admin, _is_practice_owner

router = APIRouter(prefix="/api/users", tags=["users"])

PENDING = "PRACTICE_AGREEMENT_PENDING"
PENDING_MESSAGE = "Your practice has not accepted the Business Associate Agreement yet"


def practice_agreement_clears(user: User) -> bool:
    """Whether ``user``, not the practice owner, is cleared by the practice's acceptance.

    Any version the practice accepted clears them. Raises the waiting-state 403 before then.
    """
    practice = practice_row_for(user)
    if practice is None or _is_practice_owner(practice, user):
        return False
    if practice.baa_accepted_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": PENDING,
                    "message": PENDING_MESSAGE,
                    "details": {"baa_accepted": False},
                }
            },
        )
    return True


def writes_practice_snapshot(
    practice: PracticeRow, user: User, version: str, current_version: str
) -> bool:
    """Whether ``user`` accepting ``version`` replaces the practice's signed record.

    The owner's acceptance does. An admin's does only for a current version the
    practice has not accepted. Anyone else stamps only their own row, and is
    refused (409) until the practice has accepted.
    """
    if _is_practice_owner(practice, user):
        return True
    if _is_practice_admin(practice, user):
        return version == current_version and version != practice.baa_version
    if practice.baa_accepted_at is None:
        raise ConflictError(PENDING_MESSAGE, code=PENDING)
    return False


def member_baa_status(user: User, practice: PracticeRow, current_version: str) -> BAAStatusResponse:
    """``baa-status`` for a clinician who does not own ``practice``."""
    return BAAStatusResponse(
        accepted=user.baa_accepted_at is not None,
        accepted_at=user.baa_accepted_at,
        version=user.baa_version,
        current_version=current_version,
        signed_practice_name=practice.baa_practice_name,
        is_practice_owner=False,
        may_accept_new_versions=_is_practice_admin(practice, user),
        practice_accepted_at=practice.baa_accepted_at,
        practice_version=practice.baa_version,
        practice_accepted_by=practice.baa_legal_name,
        notice_seen_at=user.practice_agreement_notice_seen_at,
    )


@router.post("/me/practice-agreement-notice")
def mark_practice_agreement_notice_seen(
    http_request: Request,
    user: User = Depends(get_current_user_no_mfa),
    current_version: str = Depends(get_baa_version),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> BAAStatusResponse:
    """Record, once, that a clinician who does not own the practice saw that its
    agreement is in force. Pre-MFA, like ``accept_baa``; 409 before the practice accepts.
    """
    practice = practice_row_for(user)
    if practice is None or _is_practice_owner(practice, user):
        raise BadRequestError("Only a clinician who does not own the practice sees this notice")
    if practice.baa_accepted_at is None:
        raise ConflictError(PENDING_MESSAGE, code=PENDING)
    if user.practice_agreement_notice_seen_at is None:
        user.practice_agreement_notice_seen_at = utc_now()
        user_repo.update(user)
        audit.log_onboarding_milestone(
            AuditAction.ONBOARDING_PRACTICE_AGREEMENT_NOTICE_SEEN, user, http_request
        )
    return member_baa_status(user, practice, current_version)
