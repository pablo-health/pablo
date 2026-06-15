# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain + API types for WebAuthn passkey authentication.

A passkey is a phishing-resistant possession factor a user enrols on an
authenticator (phone/laptop platform authenticator or a roaming hardware
key). The backend verifies the WebAuthn ceremony itself and, on a
successful assertion, mints a Firebase custom token carrying the
``pablo_amr`` factor claim that ``require_mfa`` honours.

See ``docs/internal/passkey-auth-build-spec.md`` (epic PABLO-egm,
backend slice PABLO-egm.1). No PHI: authenticator metadata plus a
user-chosen label only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class PasskeyCredential:
    """A persisted passkey credential (mirrors ``PasskeyCredentialRow``)."""

    credential_id: str
    user_id: str
    public_key: bytes
    sign_count: int
    transports: list[str] | None
    aaguid: str | None
    backup_eligible: bool
    backup_state: bool
    device_label: str | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class PasskeyRegistrationVerify(BaseModel):
    """Finish-registration payload: the authenticator's attestation response."""

    credential: dict[str, Any]
    device_label: str | None = Field(default=None, max_length=120)


class PasskeyAuthenticationBegin(BaseModel):
    """Begin-authentication payload.

    Empty by default — the ceremony is usernameless (resident-key /
    discoverable-credential) so the authenticator resolves the user. A
    pre-bound user is never accepted here; the asserting credential
    determines the user at finish.
    """


class PasskeyAuthenticationVerify(BaseModel):
    """Finish-authentication payload: the authenticator's assertion response."""

    credential: dict[str, Any]


class PasskeyRegistrationResult(BaseModel):
    """Result of a successful enrolment."""

    credential_id: str
    created_at: datetime


class PasskeyAuthenticationResult(BaseModel):
    """Result of a successful assertion — a Firebase custom token.

    The frontend exchanges this via ``signInWithCustomToken`` for an ID
    token carrying ``pablo_amr: ["webauthn"]``.
    """

    custom_token: str
