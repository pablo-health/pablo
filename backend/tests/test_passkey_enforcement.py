# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The passkey MFA-enforcement seam (build-spec hardening H1/H3).

``mfa_satisfied`` must become True for a passkey session — which carries
``pablo_amr: ["webauthn"]`` and NO ``firebase.sign_in_second_factor`` —
at both enforcement points: the verifier seam (``providers.py``) and the
parallel hand-rolled native-code gate (``routes/auth.py``). A token with
neither signal must stay first-factor-only.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from app.api_errors import ForbiddenError
from app.auth.providers import (
    FirebaseVerifier,
    passkey_factor_satisfied,
    second_factor_satisfied,
)
from app.routes.auth import CreateAuthCodeRequest, create_native_code


class TestPasskeyFactorSatisfied:
    def test_true_when_webauthn_in_pablo_amr(self) -> None:
        assert passkey_factor_satisfied({"pablo_amr": ["webauthn"]}) is True

    @pytest.mark.parametrize(
        "claims",
        [
            {},
            {"pablo_amr": []},
            {"pablo_amr": ["password"]},
            {"pablo_amr": "webauthn"},  # must be a list, not a bare string
            {"pablo_amr": None},
        ],
    )
    def test_false_otherwise(self, claims: dict) -> None:
        assert passkey_factor_satisfied(claims) is False


class TestSecondFactorSatisfied:
    """One definition shared by both verifiers and the native-code gate."""

    def test_firebase_native_second_factor(self) -> None:
        assert second_factor_satisfied({"firebase": {"sign_in_second_factor": "phone"}}) is True

    def test_passkey_factor(self) -> None:
        assert second_factor_satisfied({"pablo_amr": ["webauthn"]}) is True

    def test_recovery_code_factor(self) -> None:
        # A redeemed backup code (minted as pablo_amr ["recovery"]) is a valid
        # second factor — it was combined with a first factor at redemption.
        assert second_factor_satisfied({"pablo_amr": ["recovery"]}) is True

    def test_oidc_amr_step_up(self) -> None:
        assert second_factor_satisfied({"amr": ["mfa"]}) is True

    def test_none_present(self) -> None:
        assert second_factor_satisfied({"email": "t@pablo.health"}) is False


class TestVerifierSeam:
    def _decoded(self, **extra: object) -> dict:
        return {"uid": "fb-uid", "email": "t@pablo.health", **extra}

    def test_passkey_claim_alone_satisfies_mfa(self) -> None:
        identity = FirebaseVerifier().verify_from_decoded(
            self._decoded(pablo_amr=["webauthn"])
        )
        assert identity.mfa_satisfied is True

    def test_legacy_second_factor_alone_satisfies_mfa(self) -> None:
        identity = FirebaseVerifier().verify_from_decoded(
            self._decoded(firebase={"sign_in_second_factor": "phone"})
        )
        assert identity.mfa_satisfied is True

    def test_neither_signal_is_not_satisfied(self) -> None:
        identity = FirebaseVerifier().verify_from_decoded(self._decoded())
        assert identity.mfa_satisfied is False


class TestNativeCodeGate:
    """The desktop code-exchange gate must honour the passkey factor too."""

    def _call(self, decoded: dict) -> None:
        request = CreateAuthCodeRequest(
            id_token="tok",  # noqa: S106 — test placeholder, not a real credential
            refresh_token="r",  # noqa: S106 — test placeholder, not a real credential
            redirect_uri="pablohealth://cb",
        )
        with (
            patch("app.routes.auth.check_client_version"),
            patch("app.routes.auth.initialize_firebase_app"),
            patch("app.routes.auth.firebase_auth.verify_id_token", return_value=decoded),
            patch("app.routes.auth.create_auth_code", return_value="code-123"),
            patch("app.routes.auth.get_settings") as mock_settings,
        ):
            mock_settings.return_value.require_mfa = True
            mock_settings.return_value.is_development = False
            create_native_code(request, MagicMock(), _=None, _public=None)

    def test_passkey_token_is_accepted(self) -> None:
        # pablo_amr present, no sign_in_second_factor — must NOT be rejected.
        self._call({"uid": "fb-uid", "pablo_amr": ["webauthn"]})

    def test_first_factor_only_is_rejected(self) -> None:
        with pytest.raises(ForbiddenError):
            self._call({"uid": "fb-uid", "firebase": {}})
