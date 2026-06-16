# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Passkey manage routes: list + the MFA-gated revoke.

Removing a factor is a security downgrade, so ``revoke`` must reject a
session that has not satisfied MFA — a phished first-factor session must
not be able to strip passkeys off an account.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.api_errors import ForbiddenError, NotFoundError
from app.auth.providers import VerifiedIdentity
from app.models.audit import AuditAction
from app.routes.passkey import list_credentials, register_finish, revoke_credential

USER = SimpleNamespace(id="11111111-1111-4111-8111-111111111111", email="t@pablo.health")


def _request(*, mfa_satisfied: bool) -> MagicMock:
    identity = VerifiedIdentity(
        provider="firebase",
        subject_id="fb-uid",
        email=USER.email,
        mfa_satisfied=mfa_satisfied,
        claims={},
    )
    request = MagicMock()
    request.state.verified_identity = identity
    return request


class TestRegisterFinish:
    def _user(self, *, mfa_enrolled_at: object) -> SimpleNamespace:
        return SimpleNamespace(id=USER.id, email=USER.email, mfa_enrolled_at=mfa_enrolled_at)

    def test_first_passkey_stamps_second_factor_milestone(self) -> None:
        # A verified passkey is a second factor: enrolling the first one must
        # set mfa_enrolled_at so onboarding/dashboard gates clear (passkey-first).
        service, user_repo, audit = MagicMock(), MagicMock(), MagicMock()
        service.finish_registration.return_value = "result"
        user = self._user(mfa_enrolled_at=None)
        payload = SimpleNamespace(credential={}, device_label=None)

        out = register_finish(payload, MagicMock(), user, service, user_repo, audit)

        assert out == "result"
        assert user.mfa_enrolled_at is not None
        user_repo.update.assert_called_once_with(user)
        action, milestone_user, _req = audit.log_onboarding_milestone.call_args.args
        assert action == AuditAction.ONBOARDING_MFA_ENROLLED
        assert milestone_user is user
        assert audit.log_onboarding_milestone.call_args.kwargs["changes"] == {"factor": "passkey"}

    def test_does_not_restamp_when_already_enrolled(self) -> None:
        # A user who already has a second factor (TOTP or an earlier passkey)
        # keeps their original milestone timestamp; no duplicate audit entry.
        service, user_repo, audit = MagicMock(), MagicMock(), MagicMock()
        service.finish_registration.return_value = "result"
        already = datetime(2026, 1, 1, tzinfo=UTC)
        user = self._user(mfa_enrolled_at=already)

        register_finish(
            SimpleNamespace(credential={}, device_label=None),
            MagicMock(),
            user,
            service,
            user_repo,
            audit,
        )

        assert user.mfa_enrolled_at == already
        user_repo.update.assert_not_called()
        audit.log_onboarding_milestone.assert_not_called()


class TestListCredentials:
    def test_returns_service_summaries(self) -> None:
        service = MagicMock()
        service.list_credentials.return_value = ["summary"]
        assert list_credentials(USER, passkey_service=service) == ["summary"]
        service.list_credentials.assert_called_once_with(USER.id)


class TestRevokeCredential:
    def test_rejects_session_without_mfa(self) -> None:
        service = MagicMock()
        with pytest.raises(ForbiddenError) as err:
            revoke_credential("cred-1", _request(mfa_satisfied=False), USER, service)
        assert err.value.code == "MFA_REQUIRED"
        service.revoke_credential.assert_not_called()

    def test_unknown_credential_is_404(self) -> None:
        service = MagicMock()
        service.revoke_credential.return_value = False
        with pytest.raises(NotFoundError):
            revoke_credential("cred-1", _request(mfa_satisfied=True), USER, service)

    def test_revokes_when_mfa_satisfied(self) -> None:
        service = MagicMock()
        service.revoke_credential.return_value = True
        revoke_credential("cred-1", _request(mfa_satisfied=True), USER, service)
        service.revoke_credential.assert_called_once_with(
            user_id=USER.id, credential_id="cred-1", require_hardware_floor=False
        )
