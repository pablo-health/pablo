# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A verified sign-in with no practice resolved: the redeeming side of an invitation.

Every other signed-in dependency resolves the caller to a practice, or to a
user it may create, before the route runs (``get_current_user``,
``get_tenant_context``). Neither fits the account redeeming an invitation:

* it usually has no practice yet, so ``get_tenant_context`` refuses it with
  ``NO_PRACTICE``;
* where sign-ups are restricted, ``get_current_user`` refuses an address with
  no grant (``SIGNUP_NOT_ALLOWED``), and the invitation IS the grant;
* where they are not, ``get_current_user`` maps a new address onto the
  deployment's own practice, which would join it to the wrong practice before
  it could redeem.

:func:`get_verified_invitee` does only what the redeem needs: it verifies the
sign-in token, applies the idle-session gate, refuses an address the issuer has
not verified, and looks up (never creates) the account the token belongs to.
It resolves no practice, writes nothing, and arms no row-level security
principal. It asks for no second factor: the invitee has had no chance to
enrol one, and everything past the redeem asks for it as usual.

ONLY ``POST /api/invites/redeem`` may depend on it.
``tests/test_practice_invite_routes.py`` walks the route table and fails if
any other route does, and the route guardrails list it as a pre-MFA marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import (
    HTTPAuthorizationCredentials,  # noqa: TC002 — fastapi resolves the annotation at runtime
)

from ..logging_config import bind_log_user_id
from ..repositories import IdentityRepository, get_identity_repository
from ..version_check import check_client_version
from . import idle_session
from .service import (
    _email_not_verified_error,
    _extract_email,
    _lookup_pablo_user_id,
    _verify_request_identity,
    security,
)


@dataclass(frozen=True)
class VerifiedInvitee:
    """Who is redeeming: the verified sign-in, and its account if it has one."""

    provider: str
    subject_id: str
    #: Lowercased, and verified by the issuer.
    email: str
    #: The account the sign-in belongs to, or ``None`` for a first sign-in.
    user_id: str | None
    claims: dict[str, Any]


def get_verified_invitee(
    request: Request,
    auth_credentials: HTTPAuthorizationCredentials = Depends(security),
    identity_repo: IdentityRepository = Depends(get_identity_repository),
) -> VerifiedInvitee:
    """FastAPI dependency: a verified sign-in, with no practice resolved.

    401 for a token that does not verify; 403 ``EMAIL_NOT_VERIFIED`` for a
    sign-in whose address the issuer has not verified, or that carries none.
    """
    check_client_version(request)
    identity = _verify_request_identity(request, auth_credentials.credentials)
    idle_session.check_and_touch(
        identity.claims, refresh=not idle_session.is_background_request(request)
    )
    if not identity.subject_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_TOKEN",
                    "message": "User ID not found in token",
                    "details": {},
                }
            },
        )
    email = (identity.email or _extract_email(identity.claims)).strip().lower()
    # Stricter than the sign-in path, which lets a reserved test address
    # through unverified: an invitation joins a real practice, so the address
    # has to be one its holder proved they control.
    if not email or not identity.email_verified:
        raise _email_not_verified_error()
    user_id = _lookup_pablo_user_id(
        request, identity.subject_id, identity_repo, provider=identity.provider
    )
    if user_id is not None:
        bind_log_user_id(user_id)
    return VerifiedInvitee(
        provider=identity.provider,
        subject_id=identity.subject_id,
        email=email,
        user_id=user_id,
        claims=identity.claims,
    )
