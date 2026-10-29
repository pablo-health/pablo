# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who may change the practice's practice-wide settings besides its owner.

* ``GET /api/practice/admins`` — every clinician of the practice, with who is
  the owner and who is an admin.
* ``PUT /api/practice/admins/{user_id}`` — make a clinician of the practice an
  admin.
* ``DELETE /api/practice/admins/{user_id}`` — take that away.

All three are the practice owner's alone (``_get_own_practice_as_owner``): an
admin may change settings but not decide who else may. The owner is an admin
by being the owner and is never granted or revoked. The practice is always the
caller's own, resolved from the caller, never taken from the request. Grants
and revokes are audited; neither touches PHI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..api_errors import BadRequestError, NotFoundError
from ..auth.service import require_active_subscription
from ..db import get_db_session
from ..db.platform_models import PracticeRow  # noqa: TC001 — annotation used at runtime
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.practice_admin import PracticeAdminsResponse, PracticeClinician
from ..portal.clinicians import practice_clinicians
from ..repositories import (
    PracticeAdminRepository,
    UserRepository,
    get_practice_admin_repository,
    get_user_repository,
)
from ..services import AuditService, get_audit_service
from ..utcnow import utc_now
from .users import _get_own_practice_as_owner, _is_practice_owner

router = APIRouter(prefix="/api/practice/admins", tags=["practice"])


def _clinicians(practice: PracticeRow, user_repo: UserRepository) -> list[tuple[User, bool]]:
    """The practice's clinicians, each with whether they own it."""
    found: list[tuple[User, bool]] = []
    for user_id in practice_clinicians(get_db_session(), practice.schema_name):
        member = user_repo.get(user_id)
        if member is not None:
            found.append((member, _is_practice_owner(practice, member)))
    return found


def _response(
    practice: PracticeRow, user_repo: UserRepository, admins: PracticeAdminRepository
) -> PracticeAdminsResponse:
    admin_ids = admins.list_admin_ids()
    clinicians = [
        PracticeClinician(
            user_id=member.id,
            name=member.name,
            email=member.email,
            is_owner=is_owner,
            is_admin=is_owner or member.id in admin_ids,
        )
        for member, is_owner in _clinicians(practice, user_repo)
    ]
    clinicians.sort(key=lambda c: (not c.is_owner, (c.name or c.email).lower()))
    return PracticeAdminsResponse(clinicians=clinicians)


def _grantable(practice: PracticeRow, user_id: str, user_repo: UserRepository) -> None:
    """Refuse a target who is not a clinician here, or who owns the practice."""
    for member, is_owner in _clinicians(practice, user_repo):
        if member.id == user_id:
            if is_owner:
                raise BadRequestError(
                    "The practice owner can always change practice settings",
                    code="PRACTICE_OWNER",
                )
            return
    raise NotFoundError("No clinician with that id in this practice", code="NOT_A_CLINICIAN")


@router.get("")
def list_practice_admins(
    user: User = Depends(require_active_subscription),
    user_repo: UserRepository = Depends(get_user_repository),
    admins: PracticeAdminRepository = Depends(get_practice_admin_repository),
) -> PracticeAdminsResponse:
    """Every clinician of the practice, and who may change its settings."""
    practice = _get_own_practice_as_owner(user)
    return _response(practice, user_repo, admins)


@router.put("/{user_id}")
def grant_practice_admin(
    user_id: str,
    http_request: Request,
    user: User = Depends(require_active_subscription),
    user_repo: UserRepository = Depends(get_user_repository),
    admins: PracticeAdminRepository = Depends(get_practice_admin_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PracticeAdminsResponse:
    """Make a clinician of the practice an admin. Granting twice changes nothing."""
    practice = _get_own_practice_as_owner(user)
    _grantable(practice, user_id, user_repo)
    if admins.grant(user_id, granted_by=user.id, granted_at=utc_now()):
        audit.log(
            AuditAction.PRACTICE_ADMIN_GRANTED,
            user,
            http_request,
            resource_type=ResourceType.PRACTICE,
            resource_id=practice.id,
            changes={"user_id": user_id},
        )
    return _response(practice, user_repo, admins)


@router.delete("/{user_id}")
def revoke_practice_admin(
    user_id: str,
    http_request: Request,
    user: User = Depends(require_active_subscription),
    user_repo: UserRepository = Depends(get_user_repository),
    admins: PracticeAdminRepository = Depends(get_practice_admin_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PracticeAdminsResponse:
    """Stop a clinician being an admin. Revoking twice changes nothing."""
    practice = _get_own_practice_as_owner(user)
    _grantable(practice, user_id, user_repo)
    if admins.revoke(user_id):
        audit.log(
            AuditAction.PRACTICE_ADMIN_REVOKED,
            user,
            http_request,
            resource_type=ResourceType.PRACTICE,
            resource_id=practice.id,
            changes={"user_id": user_id},
        )
    return _response(practice, user_repo, admins)
