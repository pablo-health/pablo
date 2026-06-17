# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""WebAuthn passkey ceremony orchestration.

Drives the four ceremony halves using ``py_webauthn``, the single-use
challenge store, and the credential repository. On a verified assertion it
mints a Firebase custom token carrying ``pablo_amr: ["webauthn"]`` — the
factor claim ``auth.providers`` reads in ``mfa_satisfied``. The claim is
set only here, after a verified assertion, in the same request.

No PHI is logged — identifiers (user_id, credential_id) only.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from firebase_admin import auth as firebase_auth
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..auth.firebase_init import initialize_firebase_app
from ..models.passkey import (
    PasskeyAuthenticationResult,
    PasskeyCredential,
    PasskeyCredentialSummary,
    PasskeyRegistrationResult,
)
from ..settings import get_settings
from ..utcnow import utc_now
from .passkey_attestation import AttestationUntrustedError, build_attestation_verifier
from .passkey_challenge_store import build_challenge_store

if TYPE_CHECKING:
    from ..repositories.identity import IdentityRepository
    from ..repositories.passkey_credential import PasskeyCredentialRepository
    from ..settings import Settings
    from .passkey_attestation import AttestationVerifier
    from .passkey_challenge_store import PasskeyChallengeStore

logger = logging.getLogger(__name__)

_ZERO_AAGUID = "00000000-0000-0000-0000-000000000000"


class PasskeyEnrollmentError(Exception):
    """Adding another passkey needs an already-MFA-satisfied session."""


class PasskeyCeremonyError(Exception):
    """Malformed / expired / replayed ceremony input → 400."""


class PasskeyAssertionError(Exception):
    """Assertion failed verification or no usable credential → 401."""


class PasskeyLastHardwareKeyError(Exception):
    """Refuse to revoke an admin's only hardware key under enforcement → 409.

    Removing it would lock the admin out of every hardware-gated route. The
    user must enrol a second hardware key first (the >=2-key floor)."""


def _extract_challenge(credential: dict[str, Any]) -> bytes:
    """Recover the challenge bytes from the response's signed clientDataJSON.

    The challenge is the single-use lookup key: hashing it and consuming the
    matching row proves we issued it (SHA-256 preimage resistance is why the
    client returning the challenge is safe).
    """
    try:
        client_data = json.loads(base64url_to_bytes(credential["response"]["clientDataJSON"]))
        return base64url_to_bytes(client_data["challenge"])
    except (KeyError, TypeError, ValueError) as err:
        raise PasskeyCeremonyError("clientDataJSON") from err


def _transports(credential: dict[str, Any]) -> list[str] | None:
    response = credential.get("response")
    transports = response.get("transports") if isinstance(response, dict) else None
    if isinstance(transports, list) and all(isinstance(t, str) for t in transports):
        return transports or None
    return None


def _normalize_aaguid(aaguid: str | None) -> str | None:
    # All-zero AAGUID is the privacy-preserving sentinel → store NULL.
    if not aaguid or aaguid == _ZERO_AAGUID:
        return None
    return aaguid


class PasskeyService:
    def __init__(
        self,
        *,
        credentials: PasskeyCredentialRepository,
        challenges: PasskeyChallengeStore,
        identities: IdentityRepository,
        attestation: AttestationVerifier,
        settings: Settings,
    ) -> None:
        self._credentials = credentials
        self._challenges = challenges
        self._identities = identities
        self._attestation = attestation
        self._settings = settings

    # --- registration -------------------------------------------------

    def begin_registration(
        self, *, user_id: str, account_email: str, session_mfa_satisfied: bool
    ) -> dict[str, Any]:
        """Issue registration options and persist the ceremony challenge."""
        # First passkey can be enrolled from a first-factor session; adding
        # another requires an MFA-satisfied session, so a phished password
        # can't add a passkey to an already-protected account.
        existing = self._credentials.list_for_user(user_id)
        if existing and not session_mfa_satisfied:
            raise PasskeyEnrollmentError

        options = generate_registration_options(
            rp_id=self._settings.webauthn_rp_id,
            rp_name=self._settings.webauthn_rp_name,
            user_id=user_id.encode("utf-8"),
            user_name=account_email,
            user_display_name=account_email,
            attestation=AttestationConveyancePreference(self._settings.webauthn_attestation),
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
                for c in existing
            ],
        )
        self._challenges.create("register", user_id, options.challenge)
        result: dict[str, Any] = json.loads(options_to_json(options))
        return result

    def finish_registration(
        self, *, user_id: str, credential: dict[str, Any], device_label: str | None
    ) -> PasskeyRegistrationResult:
        challenge = _extract_challenge(credential)
        consumed = self._challenges.consume("register", challenge)
        if consumed is None or consumed.user_id != user_id:
            raise PasskeyCeremonyError("challenge")

        try:
            verification = verify_registration_response(
                credential=json.dumps(credential),
                expected_challenge=challenge,
                expected_origin=self._settings.webauthn_origins,
                expected_rp_id=self._settings.webauthn_rp_id,
                require_user_verification=True,
            )
        except Exception as err:
            logger.warning("passkey_registration_rejected user_id=%s reason=%s", user_id, err)
            raise PasskeyCeremonyError("verification") from err

        try:
            attestation_verified = self._attestation.evaluate(
                credential=json.dumps(credential),
                expected_challenge=challenge,
                expected_origin=self._settings.webauthn_origins,
                expected_rp_id=self._settings.webauthn_rp_id,
                strict=self._settings.webauthn_attestation_require_trusted_root,
            )
        except AttestationUntrustedError as err:
            logger.warning("passkey_registration_untrusted user_id=%s fmt=%s", user_id, err)
            raise PasskeyCeremonyError("attestation") from err

        credential_id = bytes_to_base64url(verification.credential_id)
        now = utc_now()
        credential_record = PasskeyCredential(
            credential_id=credential_id,
            user_id=user_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            transports=_transports(credential),
            aaguid=_normalize_aaguid(verification.aaguid),
            fmt=verification.fmt,
            attestation_verified=attestation_verified,
            backup_eligible=verification.credential_device_type == "multi_device",
            backup_state=verification.credential_backed_up,
            device_label=device_label,
            created_at=now,
            last_used_at=None,
            revoked_at=None,
        )
        self._credentials.add(credential_record)
        logger.info(
            "passkey_enrolled user_id=%s credential_id=%s fmt=%s aaguid=%s "
            "device_type=%s backed_up=%s attested=%s",
            user_id,
            credential_id,
            verification.fmt,
            verification.aaguid,
            verification.credential_device_type,
            verification.credential_backed_up,
            attestation_verified,
        )

        # A verified attestation proves possession of the authenticator right
        # now, so the just-enrolled session is legitimately second-factor-
        # satisfied — mint the same webauthn factor token an assertion would.
        # Without this, a passkey-first onboard holds a first-factor-only token
        # and is blocked from every PHI route until it signs out and back in
        # (PABLO-mee). Best-effort: every user has a Firebase identity, but if
        # the lookup misses we don't fail an otherwise-successful enrolment —
        # the client can still step up via the authenticate ceremony.
        custom_token: str | None = None
        firebase_uid = self._identities.get_subject_id(user_id, "firebase")
        if firebase_uid is None:
            logger.warning("passkey_registration_no_firebase_uid user_id=%s", user_id)
        else:
            custom_token = self._mint_factor_token(
                firebase_uid,
                hardware=credential_record.is_hardware_authenticator,
                attested=attestation_verified,
            )
        return PasskeyRegistrationResult(
            credential_id=credential_id, created_at=now, custom_token=custom_token
        )

    # --- authentication ----------------------------------------------

    def begin_authentication(self) -> dict[str, Any]:
        """Issue usernameless (resident-key) authentication options."""
        options = generate_authentication_options(
            rp_id=self._settings.webauthn_rp_id,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        self._challenges.create("authenticate", None, options.challenge)
        result: dict[str, Any] = json.loads(options_to_json(options))
        return result

    def finish_authentication(
        self, *, credential: dict[str, Any]
    ) -> PasskeyAuthenticationResult:
        """Verify the assertion and mint the passkey-factor custom token."""
        # Consume the single-use challenge before issuing anything.
        challenge = _extract_challenge(credential)
        if self._challenges.consume("authenticate", challenge) is None:
            raise PasskeyCeremonyError("challenge")

        credential_id = credential.get("id")
        if not isinstance(credential_id, str):
            raise PasskeyCeremonyError("credential")

        # Verify the signature against the stored public key and counter.
        stored = self._credentials.get_active(credential_id)
        if stored is None:
            raise PasskeyAssertionError

        try:
            verification = verify_authentication_response(
                credential=json.dumps(credential),
                expected_challenge=challenge,
                expected_rp_id=self._settings.webauthn_rp_id,
                expected_origin=self._settings.webauthn_origins,
                credential_public_key=stored.public_key,
                credential_current_sign_count=stored.sign_count,
                require_user_verification=True,
            )
        except Exception as err:
            logger.warning(
                "passkey_assertion_rejected credential_id=%s reason=%s", credential_id, err
            )
            raise PasskeyAssertionError from err

        # A signature counter that doesn't increase signals a cloned
        # authenticator — unless both are 0, which is normal for authenticators
        # that don't keep a counter (most platform authenticators).
        new_count = verification.new_sign_count
        if new_count <= stored.sign_count and not (new_count == 0 and stored.sign_count == 0):
            logger.warning(
                "passkey_clone_suspected credential_id=%s stored=%d new=%d",
                credential_id,
                stored.sign_count,
                new_count,
            )
            raise PasskeyAssertionError

        firebase_uid = self._identities.get_subject_id(stored.user_id, "firebase")
        if firebase_uid is None:
            logger.warning("passkey_assertion_no_firebase_uid user_id=%s", stored.user_id)
            raise PasskeyAssertionError

        self._credentials.update_after_assertion(
            credential_id,
            sign_count=new_count,
            backup_state=verification.credential_backed_up,
        )

        # Mint the factor token; pablo_amr is set only after a verified
        # ceremony — an assertion here, or a fresh attestation at enrolment.
        token = self._mint_factor_token(
            firebase_uid,
            hardware=stored.is_hardware_authenticator,
            attested=stored.attestation_verified,
        )
        logger.info(
            "passkey_assertion_ok user_id=%s credential_id=%s hw=%s att=%s",
            stored.user_id,
            credential_id,
            stored.is_hardware_authenticator,
            stored.attestation_verified,
        )
        return PasskeyAuthenticationResult(custom_token=token)

    def _mint_factor_token(self, firebase_uid: str, *, hardware: bool, attested: bool) -> str:
        """Mint a Firebase custom token carrying the webauthn second-factor claim.

        ``pablo_amr: ["webauthn"]`` marks the session second-factor-satisfied.
        ``pablo_passkey`` records the verified credential's provenance so admin
        hardware-key enforcement can bind the session to a device-bound (and
        optionally attested) authenticator — not just "some passkey". Both an
        assertion (``authenticate/finish``) and a fresh attestation
        (``register/finish``) reach this after the ceremony is verified.
        """
        initialize_firebase_app()
        token: bytes = firebase_auth.create_custom_token(
            firebase_uid,
            {
                "pablo_amr": ["webauthn"],
                "pablo_passkey": {"hw": hardware, "att": attested},
            },
        )
        return token.decode("utf-8")

    # --- recovery -----------------------------------------------------

    def mint_recovery_session(self, user_id: str) -> PasskeyAuthenticationResult:
        """Mint a factor token for a user who redeemed a recovery code.

        The caller MUST have already proven a first factor (the route runs on a
        first-factor session) and spent a valid single-use code — so this is the
        *second* factor, never a standalone login. ``pablo_amr: ["recovery"]``
        is what ``require_mfa`` honours; it is set only here, only after a
        verified redemption.
        """
        firebase_uid = self._identities.get_subject_id(user_id, "firebase")
        if firebase_uid is None:
            logger.warning("recovery_mint_no_firebase_uid user_id=%s", user_id)
            raise PasskeyAssertionError
        initialize_firebase_app()
        token = firebase_auth.create_custom_token(firebase_uid, {"pablo_amr": ["recovery"]})
        logger.info("recovery_session_minted user_id=%s", user_id)
        return PasskeyAuthenticationResult(custom_token=token.decode("utf-8"))

    # --- management ---------------------------------------------------

    def list_credentials(self, user_id: str) -> list[PasskeyCredentialSummary]:
        """Return the user's active passkeys for the manage UI."""
        return [
            PasskeyCredentialSummary.from_credential(c)
            for c in self._credentials.list_for_user(user_id)
        ]

    def revoke_credential(
        self, *, user_id: str, credential_id: str, require_hardware_floor: bool = False
    ) -> bool:
        """Soft-revoke one of the user's passkeys; return whether it matched.

        When ``require_hardware_floor`` is set (admin under hardware-key
        enforcement), refuse to remove the user's *last* hardware credential —
        that would lock them out of every hardware-gated route. They must
        enrol a second hardware key first.
        """
        if require_hardware_floor:
            active = self._credentials.list_for_user(user_id)
            target = next((c for c in active if c.credential_id == credential_id), None)
            if target is not None and target.is_hardware_authenticator:
                hardware = [c for c in active if c.is_hardware_authenticator]
                if len(hardware) <= 1:
                    raise PasskeyLastHardwareKeyError

        revoked = self._credentials.revoke(credential_id, user_id=user_id)
        if revoked:
            logger.info(
                "passkey_revoked user_id=%s credential_id=%s", user_id, credential_id
            )
        return revoked


def get_passkey_service() -> PasskeyService:
    """Wire the service against the request-scoped Postgres session.

    The in-memory repositories are for unit tests only. Outside development a
    missing DB session is an error — never silently fall back in production.
    """
    settings = get_settings()
    challenges = build_challenge_store()
    attestation = build_attestation_verifier(settings.webauthn_attestation_roots_dir)

    credentials: PasskeyCredentialRepository
    identities: IdentityRepository
    try:
        from ..db import get_db_session

        session = get_db_session()
    except RuntimeError:
        # No request-scoped session: allowed only in development (unit tests).
        if not settings.is_development:
            raise
        from ..repositories.identity import InMemoryIdentityRepository
        from ..repositories.passkey_credential import InMemoryPasskeyCredentialRepository

        credentials = InMemoryPasskeyCredentialRepository()
        identities = InMemoryIdentityRepository()
    else:
        from ..repositories.postgres.identity import PostgresIdentityRepository
        from ..repositories.postgres.passkey_credential import (
            PostgresPasskeyCredentialRepository,
        )

        credentials = PostgresPasskeyCredentialRepository(session)
        identities = PostgresIdentityRepository(session)

    return PasskeyService(
        credentials=credentials,
        challenges=challenges,
        identities=identities,
        attestation=attestation,
        settings=settings,
    )
