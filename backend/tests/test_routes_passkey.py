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
from app import routes
from app.api_errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.auth.providers import VerifiedIdentity
from app.models.audit import AuditAction
from app.models.passkey import PasskeyAuthenticationResult, PasskeyRegistrationResult
from app.routes.passkey import (
    authenticate_finish,
    list_credentials,
    redeem_recovery_code,
    register_finish,
    revoke_credential,
)
from app.services.passkey_service import AuthenticatedAssertion

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

    def _service(self) -> MagicMock:
        service = MagicMock()
        service.finish_registration.return_value = PasskeyRegistrationResult(
            credential_id="cred-1",
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
            custom_token="factor-token",  # noqa: S106 — test fixture value, not a real secret
        )
        return service

    def test_first_passkey_stamps_milestone_and_issues_backup_codes(self) -> None:
        # First second factor: stamp mfa_enrolled_at AND issue one-time codes,
        # returned once so the client can show them (passkey-first onboarding).
        service, user_repo, audit, backup = self._service(), MagicMock(), MagicMock(), MagicMock()
        backup.issue.return_value = ["AAAAA-BBBBB", "CCCCC-DDDDD"]
        user = self._user(mfa_enrolled_at=None)

        out = register_finish(
            SimpleNamespace(credential={}, device_label=None),
            MagicMock(),
            user,
            service,
            user_repo,
            audit,
            backup,
        )

        assert user.mfa_enrolled_at is not None
        user_repo.update.assert_called_once_with(user)
        action, milestone_user, _req = audit.log_onboarding_milestone.call_args.args
        assert action == AuditAction.ONBOARDING_MFA_ENROLLED
        assert milestone_user is user
        assert audit.log_onboarding_milestone.call_args.kwargs["changes"] == {"factor": "passkey"}
        backup.issue.assert_called_once_with(user.id)
        assert out.backup_codes == ["AAAAA-BBBBB", "CCCCC-DDDDD"]
        # The backup-code model_copy must not drop the minted factor token —
        # it's what lets the passkey-first onboard reach PHI (PABLO-mee).
        assert out.custom_token == "factor-token"

    def test_subsequent_passkey_does_not_restamp_or_reissue(self) -> None:
        # A user who already has a second factor keeps their timestamp, gets no
        # duplicate audit entry, and is NOT issued a fresh code set.
        service, user_repo, audit, backup = self._service(), MagicMock(), MagicMock(), MagicMock()
        already = datetime(2026, 1, 1, tzinfo=UTC)
        user = self._user(mfa_enrolled_at=already)

        out = register_finish(
            SimpleNamespace(credential={}, device_label=None),
            MagicMock(),
            user,
            service,
            user_repo,
            audit,
            backup,
        )

        assert user.mfa_enrolled_at == already
        user_repo.update.assert_not_called()
        audit.log_onboarding_milestone.assert_not_called()
        backup.issue.assert_not_called()
        assert out.backup_codes is None


class TestRedeemRecoveryCode:
    def test_valid_code_consumes_and_mints_recovery_session(self) -> None:
        # First-factor session + a valid code = two factors → mint a session.
        service, backup, audit = MagicMock(), MagicMock(), MagicMock()
        backup.redeem.return_value = True
        service.mint_recovery_session.return_value = PasskeyAuthenticationResult(
            custom_token="recovery-tok"  # noqa: S106 — stub mint, not a secret
        )

        out = redeem_recovery_code(
            SimpleNamespace(code="ABCDE-FGHJK"), USER, MagicMock(), service, backup, audit, None
        )

        backup.redeem.assert_called_once_with(USER.id, "ABCDE-FGHJK")
        service.mint_recovery_session.assert_called_once_with(USER.id)
        assert out.custom_token == "recovery-tok"
        # The redemption is recorded as a durable security event (HIPAA audit).
        audit.log.assert_called_once()
        assert audit.log.call_args.args[0] is AuditAction.RECOVERY_CODE_REDEEMED

    def test_invalid_or_used_code_rejected_without_minting(self) -> None:
        # A bad/spent code never mints — and a code alone (no first factor)
        # can't reach here, so this is always first-factor + code.
        service, backup, audit = MagicMock(), MagicMock(), MagicMock()
        backup.redeem.return_value = False

        with pytest.raises(UnauthorizedError) as err:
            redeem_recovery_code(
                SimpleNamespace(code="nope"), USER, MagicMock(), service, backup, audit, None
            )

        assert err.value.code == "INVALID_RECOVERY_CODE"
        service.mint_recovery_session.assert_not_called()
        # A rejected code mints nothing and writes no audit row.
        audit.log.assert_not_called()


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


class TestAuthenticateFinish:
    def _service(self) -> MagicMock:
        service = MagicMock()
        service.finish_authentication.return_value = AuthenticatedAssertion(
            result=PasskeyAuthenticationResult(custom_token="login-tok"),  # noqa: S106 — stub
            user_id=USER.id,
        )
        return service

    def _patch_tenant(self, monkeypatch: pytest.MonkeyPatch, *, schema: str | None) -> None:
        # The route re-arms the request session onto the resolved tenant before
        # the audit write; stub the db plumbing so the unit test stays in-memory.
        monkeypatch.setattr(routes.passkey, "resolve_tenant_schema_for_user", lambda _u: schema)
        monkeypatch.setattr(routes.passkey, "get_db_session", MagicMock)
        monkeypatch.setattr(routes.passkey, "set_tenant_schema", lambda *_a, **_k: None)
        monkeypatch.setattr(routes.passkey, "arm_current_user_id", lambda *_a, **_k: None)

    def test_audits_login_against_resolved_tenant(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, audit = self._service(), MagicMock()
        self._patch_tenant(monkeypatch, schema="practice_x")

        out = authenticate_finish(
            SimpleNamespace(credential={}), MagicMock(), service, audit, None, None
        )

        assert out.custom_token == "login-tok"
        audit.log_account_security_event.assert_called_once()
        action, user_id, _req = audit.log_account_security_event.call_args.args
        assert action is AuditAction.PASSKEY_AUTHENTICATED
        assert user_id == USER.id

    def test_unmapped_user_returns_token_without_audit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # OSS self-host / no active tenant: no tenant log to write to → skip.
        service, audit = self._service(), MagicMock()
        self._patch_tenant(monkeypatch, schema=None)

        out = authenticate_finish(
            SimpleNamespace(credential={}), MagicMock(), service, audit, None, None
        )

        assert out.custom_token == "login-tok"
        audit.log_account_security_event.assert_not_called()

    def test_audit_failure_does_not_block_login(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Best-effort: an audit-store hiccup must not deny a verified login.
        service, audit = self._service(), MagicMock()
        self._patch_tenant(monkeypatch, schema="practice_x")
        audit.log_account_security_event.side_effect = RuntimeError("db down")

        out = authenticate_finish(
            SimpleNamespace(credential={}), MagicMock(), service, audit, None, None
        )

        assert out.custom_token == "login-tok"
