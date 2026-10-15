# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The recording app's sign-in and device enrollment reach the user's audit trail.

Before this, opening and signing in to the recording app left nothing in the
practice's audit log: the only trace was a ``last_seen`` bump on a platform
table, so "has this clinician ever used the recording app" could not be
answered from their own trail. These drive the real ``/native/code`` →
``/native/exchange`` flow and assert on what the audit service was asked to
write.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.main import app
from app.models.audit import AuditAction
from app.rate_limit import reset_preauth_limiter
from app.services import get_audit_service
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Generator

REDIRECT_URI = "pablohealth://callback"
ENROLLMENT: dict[str, Any] = {
    "install_id": "install-abc123",
    "platform": "mac",
    "os_version": "15.2",
    "hostname_hash": "abc123",
    "device_public_key_jwk": {
        "kty": "EC",
        "crv": "P-256",
        "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
        "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
    },
    "key_storage": "hardware",
}


class TestCompanionSignInAudit:
    @pytest.fixture(autouse=True)
    def _env(self) -> Generator[None]:
        reset_preauth_limiter()
        identity_repo = MagicMock()
        identity_repo.get_user_id.return_value = "pablo-user-1"
        identity_repo.resolve_or_create.return_value = "pablo-user-1"
        self.audit = MagicMock()
        app.dependency_overrides[get_audit_service] = lambda: self.audit
        with (
            patch("app.routes.auth.initialize_firebase_app"),
            patch("app.routes.auth.firebase_auth") as firebase,
            patch("app.routes.auth.get_identity_repository", return_value=identity_repo),
            patch("app.routes.auth.get_companion_device_service", return_value=MagicMock()),
            patch(
                "app.routes.auth.resolve_tenant_schema_for_user", return_value="practice_x"
            ) as self.resolve_schema,
            patch("app.routes.auth.get_db_session"),
            patch("app.routes.auth.set_tenant_schema") as self.set_schema,
            patch("app.routes.auth.arm_current_user_id") as self.arm_user,
        ):
            firebase.verify_id_token.return_value = {"uid": "fb-user-1"}
            yield
        app.dependency_overrides.pop(get_audit_service, None)

    def _sign_in(self, enrollment: dict[str, Any] | None) -> Any:
        client = TestClient(app)
        code = client.post(
            "/api/auth/native/code",
            json={"id_token": "id", "refresh_token": "ref", "redirect_uri": REDIRECT_URI},
        ).json()["code"]
        body: dict[str, Any] = {"code": code, "redirect_uri": REDIRECT_URI}
        if enrollment is not None:
            body["enrollment"] = enrollment
        return client.post("/api/auth/native/exchange", json=body)

    def _events(self) -> list[tuple[Any, str, dict[str, Any]]]:
        return [
            (call.args[0], call.args[1], call.kwargs["changes"])
            for call in self.audit.log_account_security_event.call_args_list
        ]

    def test_sign_in_with_enrollment_records_both(self) -> None:
        response = self._sign_in(ENROLLMENT)

        assert response.status_code == 200
        assert response.json()["enrollment"] == "enrolled"
        assert self._events() == [
            (
                AuditAction.COMPANION_SIGNED_IN,
                "pablo-user-1",
                {"enrollment": "enrolled", "platform": "mac"},
            ),
            (
                AuditAction.COMPANION_DEVICE_ENROLLED,
                "pablo-user-1",
                {"install_id": "install-abc123", "platform": "mac", "key_storage": "hardware"},
            ),
        ]
        # Written into the user's own practice, not the default schema.
        self.resolve_schema.assert_called_once_with("pablo-user-1")
        assert self.set_schema.call_args.args[1] == "practice_x"
        assert self.arm_user.call_args.args[1] == "pablo-user-1"

    def test_sign_in_without_enrollment_is_still_recorded(self) -> None:
        response = self._sign_in(None)

        assert response.status_code == 200
        assert self._events() == [
            (
                AuditAction.COMPANION_SIGNED_IN,
                "pablo-user-1",
                {"enrollment": "not_requested", "platform": None},
            )
        ]

    def test_failed_enrollment_records_the_sign_in_and_the_failure(self) -> None:
        bad = {**ENROLLMENT, "device_public_key_jwk": {"kty": "nope"}}
        with patch("app.routes.auth._enroll_companion_device", return_value="failed"):
            response = self._sign_in(bad)

        assert response.status_code == 200
        events = self._events()
        assert [action for action, _, _ in events] == [AuditAction.COMPANION_SIGNED_IN]
        assert events[0][2]["enrollment"] == "failed"

    def test_account_with_no_practice_signs_in_with_no_audit_write(self) -> None:
        self.resolve_schema.return_value = None
        response = self._sign_in(ENROLLMENT)

        assert response.status_code == 200
        self.audit.log_account_security_event.assert_not_called()

    def test_audit_store_failure_never_blocks_sign_in(self) -> None:
        self.audit.log_account_security_event.side_effect = RuntimeError("store down")
        response = self._sign_in(ENROLLMENT)

        assert response.status_code == 200
        assert response.json()["id_token"] == "id"

    def test_hostname_and_key_material_never_reach_the_trail(self) -> None:
        self._sign_in(ENROLLMENT)
        written = repr(self._events())
        assert "abc123" not in written.replace("install-abc123", "")
        assert ENROLLMENT["device_public_key_jwk"]["x"] not in written
        assert "15.2" not in written
