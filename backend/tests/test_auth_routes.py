# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for pre-auth endpoints (resolve-tenant, signup, native code exchange)."""

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.main import app
from app.rate_limit import _preauth_limiter
from app.services.tenant_provisioning import (
    ProvisionResult,
    TenantProvisioningError,
)
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Reset the rate limiter between tests to prevent 429s."""
    _preauth_limiter.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _mock_doc(exists: bool, data: dict[str, Any] | None = None) -> MagicMock:
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data or {}
    return doc


def _mock_collection(doc_return: MagicMock) -> MagicMock:
    """Build a mock admin_db with collection().document().get() chain."""
    db = MagicMock()
    db.collection.return_value.document.return_value.get.return_value = doc_return
    return db


class TestResolveTenant:
    """POST /api/auth/resolve-tenant"""

    @patch("app.routes.auth.get_settings")
    def test_single_tenant_mode_returns_empty(
        self, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.multi_tenancy_enabled = False
        resp = client.post(
            "/api/auth/resolve-tenant",
            json={"email": "dr@example.com"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "tenant_id": None}

    @patch("app.routes.auth.get_admin_firestore_client")
    @patch("app.routes.auth.get_settings")
    def test_known_email_returns_tenant(
        self, mock_settings: MagicMock, mock_db: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.multi_tenancy_enabled = True
        mock_db.return_value = _mock_collection(
            _mock_doc(True, {"tenant_id": "tenant-abc"})
        )
        resp = client.post(
            "/api/auth/resolve-tenant",
            json={"email": "dr@example.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "tenant-abc"

    @patch("app.routes.auth.get_admin_firestore_client")
    @patch("app.routes.auth.get_settings")
    def test_unknown_email_returns_null_tenant(
        self, mock_settings: MagicMock, mock_db: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.multi_tenancy_enabled = True
        mock_db.return_value = _mock_collection(_mock_doc(False))
        resp = client.post(
            "/api/auth/resolve-tenant",
            json={"email": "unknown@example.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] is None

    def test_invalid_email_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/resolve-tenant",
            json={"email": "not-an-email"},
        )
        assert resp.status_code == 422


class TestSignup:
    """POST /api/auth/signup"""

    @patch("app.routes.auth.get_settings")
    def test_single_tenant_mode_returns_empty(
        self, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.multi_tenancy_enabled = False
        resp = client.post(
            "/api/auth/signup",
            json={"email": "dr@example.com", "practice_name": "My Practice"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "tenant_id": None}

    @patch("app.routes.auth.get_admin_firestore_client")
    @patch("app.routes.auth.get_settings")
    def test_not_allowlisted_returns_generic(
        self, mock_settings: MagicMock, mock_db: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.multi_tenancy_enabled = True
        mock_db.return_value = _mock_collection(_mock_doc(False))
        resp = client.post(
            "/api/auth/signup",
            json={
                "email": "rando@example.com",
                "practice_name": "Rando Practice",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] is None

    @patch("app.routes.auth.get_admin_firestore_client")
    @patch("app.routes.auth.get_settings")
    def test_already_provisioned_returns_existing(
        self, mock_settings: MagicMock, mock_db: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.multi_tenancy_enabled = True
        db = MagicMock()
        # allowed_emails check → exists
        # email_tenants check → exists with tenant_id
        db.collection.return_value.document.return_value.get.side_effect = [
            _mock_doc(True),
            _mock_doc(True, {"tenant_id": "existing-tenant"}),
        ]
        mock_db.return_value = db
        resp = client.post(
            "/api/auth/signup",
            json={
                "email": "dr@example.com",
                "practice_name": "My Practice",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "existing-tenant"

    @patch(
        "app.routes.auth.TenantProvisioningService.from_settings"
    )
    @patch("app.routes.auth.get_admin_firestore_client")
    @patch("app.routes.auth.get_settings")
    def test_new_signup_provisions_practice(
        self,
        mock_settings: MagicMock,
        mock_db: MagicMock,
        mock_service_cls: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.multi_tenancy_enabled = True
        db = MagicMock()
        # allowed_emails → exists, email_tenants → not exists
        db.collection.return_value.document.return_value.get.side_effect = [
            _mock_doc(True),
            _mock_doc(False),
        ]
        mock_db.return_value = db
        mock_service = MagicMock()
        mock_service.provision_practice.return_value = ProvisionResult(
            tenant_id="new-tenant", database_name="db-new-tenant"
        )
        mock_service_cls.return_value = mock_service

        resp = client.post(
            "/api/auth/signup",
            json={
                "email": "dr@example.com",
                "practice_name": "New Practice",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "new-tenant"
        mock_service.provision_practice.assert_called_once_with(
            "New Practice", "dr@example.com"
        )

    @patch(
        "app.routes.auth.TenantProvisioningService.from_settings"
    )
    @patch("app.routes.auth.get_admin_firestore_client")
    @patch("app.routes.auth.get_settings")
    def test_provisioning_failure_returns_500(
        self,
        mock_settings: MagicMock,
        mock_db: MagicMock,
        mock_service_cls: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.multi_tenancy_enabled = True
        db = MagicMock()
        db.collection.return_value.document.return_value.get.side_effect = [
            _mock_doc(True),
            _mock_doc(False),
        ]
        mock_db.return_value = db
        mock_service = MagicMock()
        mock_service.provision_practice.side_effect = TenantProvisioningError(
            "boom"
        )
        mock_service_cls.return_value = mock_service

        resp = client.post(
            "/api/auth/signup",
            json={
                "email": "dr@example.com",
                "practice_name": "Fail Practice",
            },
        )
        assert resp.status_code == 500

    def test_signup_invalid_email_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/signup",
            json={"email": "bad", "practice_name": "X"},
        )
        assert resp.status_code == 422


class TestNativeCodeExchange:
    """POST /api/auth/native/code and /api/auth/native/exchange"""

    REDIRECT_URI = "pablohealth://callback"

    @pytest.fixture(autouse=True)
    def _mock_firebase(self) -> Generator[None, None, None]:
        """Mock Firebase init and auth for all native code exchange tests."""
        with (
            patch("app.routes.auth.initialize_firebase_app"),
            patch("app.routes.auth.firebase_auth") as mock_auth,
        ):
            mock_auth.verify_id_token.return_value = {"uid": "user1"}
            self.mock_firebase_auth = mock_auth
            yield

    def test_create_code_valid_custom_scheme(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "id_tok",
                "refresh_token": "ref_tok",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        assert resp.status_code == 200
        assert "code" in resp.json()
        assert len(resp.json()["code"]) > 0

    def test_create_code_valid_localhost(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "id_tok",
                "refresh_token": "ref_tok",
                "redirect_uri": "http://localhost:12345/callback",
            },
        )
        assert resp.status_code == 200

    def test_create_code_invalid_redirect_uri(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "id_tok",
                "refresh_token": "ref_tok",
                "redirect_uri": "https://evil.com/steal",
            },
        )
        assert resp.status_code == 400

    def test_create_code_invalid_token_rejected(self, client: TestClient) -> None:
        self.mock_firebase_auth.verify_id_token.side_effect = Exception("invalid token")
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "forged_token",
                "refresh_token": "ref_tok",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        assert resp.status_code == 401

    def test_exchange_valid_code(self, client: TestClient) -> None:
        # Create a code
        create_resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "my_id_token",
                "refresh_token": "my_refresh_token",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        code = create_resp.json()["code"]

        # Exchange it
        exchange_resp = client.post(
            "/api/auth/native/exchange",
            json={"code": code, "redirect_uri": self.REDIRECT_URI},
        )
        assert exchange_resp.status_code == 200
        data = exchange_resp.json()
        expected_id = "my_id_token"
        expected_ref = "my_refresh_token"
        assert data["id_token"] == expected_id
        assert data["refresh_token"] == expected_ref

    def test_exchange_redirect_uri_mismatch(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "tok",
                "refresh_token": "ref",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        code = create_resp.json()["code"]

        # Exchange with wrong redirect_uri
        resp = client.post(
            "/api/auth/native/exchange",
            json={"code": code, "redirect_uri": "http://localhost:9999/evil"},
        )
        assert resp.status_code == 400
        assert "mismatch" in resp.json()["detail"]

    def test_exchange_single_use(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "tok",
                "refresh_token": "ref",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        code = create_resp.json()["code"]

        # First exchange succeeds
        assert client.post(
            "/api/auth/native/exchange",
            json={"code": code, "redirect_uri": self.REDIRECT_URI},
        ).status_code == 200

        # Second exchange fails (code consumed)
        assert client.post(
            "/api/auth/native/exchange",
            json={"code": code, "redirect_uri": self.REDIRECT_URI},
        ).status_code == 400

    def test_exchange_invalid_code(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/native/exchange",
            json={"code": "nonexistent-code", "redirect_uri": self.REDIRECT_URI},
        )
        assert resp.status_code == 400
