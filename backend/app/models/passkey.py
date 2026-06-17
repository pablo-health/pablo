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
    fmt: str | None
    attestation_verified: bool
    backup_eligible: bool
    backup_state: bool
    device_label: str | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    @property
    def is_hardware_authenticator(self) -> bool:
        """True for a device-bound authenticator (a roaming hardware key or a
        non-syncable platform credential).

        The WebAuthn BE flag is the cryptographic signal: a syncable
        multi-device passkey (iCloud/Google Password Manager) sets
        ``backup_eligible``; a hardware security key does not. Admin
        hardware-key enforcement keys off this, strengthened by
        ``attestation_verified`` when a trust store is provisioned.
        """
        return not self.backup_eligible


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


class RecoveryCodeRedeem(BaseModel):
    """Redeem-recovery-code payload: one one-time backup code.

    Submitted on a first-factor session — the code is the *second* factor.
    """

    code: str = Field(min_length=1, max_length=64)


class PasskeyRegistrationResult(BaseModel):
    """Result of a successful enrolment.

    ``custom_token`` is a Firebase custom token carrying
    ``pablo_amr: ["webauthn"]``, minted from the just-verified attestation —
    the same factor claim ``authenticate/finish`` returns. The client
    exchanges it via ``signInWithCustomToken`` and force-refreshes its ID
    token so the freshly-enrolled session clears MFA without a second WebAuthn
    ceremony or a sign-out/in (PABLO-mee). A verified attestation proves
    possession at enrolment time, so treating it as second-factor-satisfied is
    legitimate.

    ``backup_codes`` is populated **only** when this enrolment is the user's
    first second factor — the plaintext one-time recovery codes, returned
    exactly once so the client can show them. ``None`` on every later
    enrolment (the user already has codes). See PABLO-e82.
    """

    credential_id: str
    created_at: datetime
    custom_token: str | None = None
    backup_codes: list[str] | None = None


class PasskeyAuthenticationResult(BaseModel):
    """Result of a successful assertion — a Firebase custom token.

    The frontend exchanges this via ``signInWithCustomToken`` for an ID
    token carrying ``pablo_amr: ["webauthn"]``.
    """

    custom_token: str


class PasskeyCredentialSummary(BaseModel):
    """A user's enrolled passkey, as shown in the manage UI.

    Authenticator metadata and a user label only — no PHI, no key material.
    """

    credential_id: str
    device_label: str | None
    transports: list[str] | None
    backup_eligible: bool
    is_hardware_authenticator: bool
    attestation_verified: bool
    created_at: datetime
    last_used_at: datetime | None

    @classmethod
    def from_credential(cls, credential: PasskeyCredential) -> PasskeyCredentialSummary:
        return cls(
            credential_id=credential.credential_id,
            device_label=credential.device_label,
            transports=credential.transports,
            backup_eligible=credential.backup_eligible,
            is_hardware_authenticator=credential.is_hardware_authenticator,
            attestation_verified=credential.attestation_verified,
            created_at=credential.created_at,
            last_used_at=credential.last_used_at,
        )
