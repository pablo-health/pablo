# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firebase custom-token minting for verified passkey / recovery factors.

Kept apart from the ceremony orchestration so the ``firebase_admin`` seam
tests stub is a small dedicated surface rather than the whole service. The
``pablo_amr`` claim is what ``mfa_satisfied`` / ``require_mfa`` read; callers
reach these only after they have verified a ceremony or redeemed a code.
"""

from __future__ import annotations

from firebase_admin import auth as firebase_auth

from ..auth.firebase_init import initialize_firebase_app


def mint_factor_token(firebase_uid: str, *, hardware: bool, attested: bool) -> str:
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


def mint_recovery_token(firebase_uid: str) -> str:
    """Mint a recovery factor token after a single-use code is redeemed.

    ``pablo_amr: ["recovery"]`` is what ``require_mfa`` honours. The caller
    MUST have already proven a first factor and spent a valid code — so this
    is the *second* factor, never a standalone login.
    """
    initialize_firebase_app()
    token: bytes = firebase_auth.create_custom_token(firebase_uid, {"pablo_amr": ["recovery"]})
    return token.decode("utf-8")
