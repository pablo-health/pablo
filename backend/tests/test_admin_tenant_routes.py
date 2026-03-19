# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Admin Tenant Management API endpoints."""

from collections.abc import Generator
from unittest.mock import MagicMock, Mock, patch

import pytest
from app.auth.service import clear_tenant_cache, require_admin, resolve_tenant_database
from app.main import app
from app.models import User
from app.services import AuditService, get_audit_service
from fastapi import status
from fastapi.testclient import TestClient


@pytest.fixture
def admin_user() -> User:
    return User(
        id="admin-user-123",
        email="admin@example.com",
        name="Admin User",
        created_at="2024-01-01T00:00:00Z",
        is_admin=True,
    )


@pytest.fixture
def mock_audit() -> AuditService:
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value.set = MagicMock()
    return AuditService(mock_db)


@pytest.fixture
def _override_admin(admin_user: User, mock_audit: AuditService) -> Generator[None, None, None]:
    app.dependency_overrides[require_admin] = lambda: admin_user
    app.dependency_overrides[get_audit_service] = lambda: mock_audit
    yield
    app.dependency_overrides.clear()


def _make_tenant_doc(
    tenant_id: str = "tenant-abc",
    practice_name: str = "Test Practice",
    owner_email: str = "owner@example.com",
    db_name: str = "tenant-abc",
    tenant_status: str = "active",
) -> dict[str, str]:
    return {
        "tenant_id": tenant_id,
        "practice_name": practice_name,
        "owner_email": owner_email,
        "firestore_database": db_name,
        "status": tenant_status,
        "created_at": "2024-06-01T00:00:00Z",
    }


def _mock_admin_db_with_tenant(
    tenant_data: dict[str, str] | None = None,
    doc_exists: bool = True,
) -> Mock:
    """Build a mock admin Firestore client with a single tenant document."""
    mock_db = Mock()
    mock_collection = Mock()
    mock_doc_ref = Mock()
    mock_doc = Mock()

    mock_doc.exists = doc_exists
    mock_doc.to_dict.return_value = tenant_data if doc_exists else None

    mock_collection.document.return_value = mock_doc_ref
    mock_doc_ref.get.return_value = mock_doc
    mock_db.collection.return_value = mock_collection
    mock_db.batch.return_value = Mock()

    return mock_db


# --- List Tenants ---


@pytest.mark.usefixtures("_override_admin")
class TestListTenants:
    def test_returns_empty_list(self) -> None:
        mock_db = Mock()
        mock_db.collection.return_value.stream.return_value = []

        with patch(
            "app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db
        ):
            client = TestClient(app)
            response = client.get("/api/admin/tenants")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["data"] == []
        assert data["total"] == 0

    def test_returns_tenant_list(self) -> None:
        mock_db = Mock()
        doc1 = Mock()
        doc1.to_dict.return_value = _make_tenant_doc("t1", "Practice A")
        doc2 = Mock()
        doc2.to_dict.return_value = _make_tenant_doc("t2", "Practice B")
        mock_db.collection.return_value.stream.return_value = [doc1, doc2]

        with patch(
            "app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db
        ):
            client = TestClient(app)
            response = client.get("/api/admin/tenants")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total"] == 2
        assert data["data"][0]["practice_name"] == "Practice A"
        assert data["data"][1]["practice_name"] == "Practice B"

    def test_skips_empty_documents(self) -> None:
        mock_db = Mock()
        good_doc = Mock()
        good_doc.to_dict.return_value = _make_tenant_doc()
        empty_doc = Mock()
        empty_doc.to_dict.return_value = None
        mock_db.collection.return_value.stream.return_value = [good_doc, empty_doc]

        with patch(
            "app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db
        ):
            client = TestClient(app)
            response = client.get("/api/admin/tenants")

        assert response.json()["total"] == 1


# --- Get Tenant Detail ---


@pytest.mark.usefixtures("_override_admin")
class TestGetTenantDetail:
    def test_returns_tenant_with_identity_platform_info(self) -> None:
        mock_db = _mock_admin_db_with_tenant(_make_tenant_doc())

        mock_ip_tenant = Mock()
        mock_ip_tenant.display_name = "Test Practice Display"
        mock_ip_tenant.allow_password_sign_up = False

        with (
            patch("app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db),
            patch("app.routes.admin_tenants.get_identity_tenant", return_value=mock_ip_tenant),
        ):
            client = TestClient(app)
            response = client.get("/api/admin/tenants/tenant-abc")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["tenant_id"] == "tenant-abc"
        assert data["practice_name"] == "Test Practice"
        assert data["display_name"] == "Test Practice Display"

    def test_returns_404_for_unknown_tenant(self) -> None:
        mock_db = _mock_admin_db_with_tenant(doc_exists=False)

        with patch(
            "app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db
        ):
            client = TestClient(app)
            response = client.get("/api/admin/tenants/nonexistent")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_handles_identity_platform_failure_gracefully(self) -> None:
        mock_db = _mock_admin_db_with_tenant(_make_tenant_doc())

        with (
            patch("app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db),
            patch(
                "app.routes.admin_tenants.get_identity_tenant",
                side_effect=Exception("IP unavailable"),
            ),
        ):
            client = TestClient(app)
            response = client.get("/api/admin/tenants/tenant-abc")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["display_name"] is None


# --- Disable Tenant ---


@pytest.mark.usefixtures("_override_admin")
class TestDisableTenant:
    def test_disables_active_tenant(self) -> None:
        mock_db = _mock_admin_db_with_tenant(_make_tenant_doc())

        with (
            patch("app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db),
            patch("app.routes.admin_tenants.clear_tenant_cache") as mock_clear,
        ):
            client = TestClient(app)
            response = client.patch("/api/admin/tenants/tenant-abc/disable")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Tenant disabled"

        doc_ref = mock_db.collection.return_value.document.return_value
        update_args = doc_ref.update.call_args[0][0]
        assert update_args["status"] == "disabled"
        assert "disabled_at" in update_args
        mock_clear.assert_called_once()

    def test_returns_409_if_already_disabled(self) -> None:
        mock_db = _mock_admin_db_with_tenant(
            _make_tenant_doc(tenant_status="disabled")
        )

        with patch(
            "app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db
        ):
            client = TestClient(app)
            response = client.patch("/api/admin/tenants/tenant-abc/disable")

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_returns_404_for_unknown_tenant(self) -> None:
        mock_db = _mock_admin_db_with_tenant(doc_exists=False)

        with patch(
            "app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db
        ):
            client = TestClient(app)
            response = client.patch("/api/admin/tenants/nonexistent/disable")

        assert response.status_code == status.HTTP_404_NOT_FOUND


# --- Enable Tenant ---


@pytest.mark.usefixtures("_override_admin")
class TestEnableTenant:
    def test_enables_disabled_tenant(self) -> None:
        mock_db = _mock_admin_db_with_tenant(
            _make_tenant_doc(tenant_status="disabled")
        )

        with (
            patch("app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db),
            patch("app.routes.admin_tenants.clear_tenant_cache") as mock_clear,
        ):
            client = TestClient(app)
            response = client.patch("/api/admin/tenants/tenant-abc/enable")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Tenant enabled"

        doc_ref = mock_db.collection.return_value.document.return_value
        update_args = doc_ref.update.call_args[0][0]
        assert update_args["status"] == "active"
        mock_clear.assert_called_once()

    def test_returns_404_for_unknown_tenant(self) -> None:
        mock_db = _mock_admin_db_with_tenant(doc_exists=False)

        with patch(
            "app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db
        ):
            client = TestClient(app)
            response = client.patch("/api/admin/tenants/nonexistent/enable")

        assert response.status_code == status.HTTP_404_NOT_FOUND


# --- Delete Tenant ---


@pytest.mark.usefixtures("_override_admin")
class TestDeleteTenant:
    def test_full_cleanup_on_delete(self) -> None:
        tenant_data = _make_tenant_doc()
        mock_db = _mock_admin_db_with_tenant(tenant_data)
        mock_batch = Mock()
        mock_db.batch.return_value = mock_batch

        mock_operation = Mock()
        mock_operation.result.return_value = None
        mock_fs_admin = Mock()
        mock_fs_admin.delete_database.return_value = mock_operation

        with (
            patch("app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db),
            patch("app.routes.admin_tenants.delete_identity_tenant") as mock_del_ip,
            patch("app.routes.admin_tenants.FirestoreAdminClient", return_value=mock_fs_admin),
            patch("app.routes.admin_tenants.clear_tenant_cache"),
        ):
            client = TestClient(app)
            response = client.delete("/api/admin/tenants/tenant-abc")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["message"] == "Tenant deleted"
        assert data["cleanup_errors"] == []

        mock_del_ip.assert_called_once_with("tenant-abc")
        mock_fs_admin.delete_database.assert_called_once()
        mock_batch.commit.assert_called_once()

    def test_returns_404_for_unknown_tenant(self) -> None:
        mock_db = _mock_admin_db_with_tenant(doc_exists=False)

        with patch(
            "app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db
        ):
            client = TestClient(app)
            response = client.delete("/api/admin/tenants/nonexistent")

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_reports_partial_cleanup_errors(self) -> None:
        tenant_data = _make_tenant_doc()
        mock_db = _mock_admin_db_with_tenant(tenant_data)
        mock_db.batch.return_value = Mock()

        with (
            patch("app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db),
            patch(
                "app.routes.admin_tenants.delete_identity_tenant",
                side_effect=Exception("IP error"),
            ),
            patch(
                "app.routes.admin_tenants.FirestoreAdminClient",
                side_effect=Exception("FS error"),
            ),
            patch("app.routes.admin_tenants.clear_tenant_cache"),
        ):
            client = TestClient(app)
            response = client.delete("/api/admin/tenants/tenant-abc")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "partial cleanup errors" in data["message"]
        assert len(data["cleanup_errors"]) == 2

    def test_skips_default_database_deletion(self) -> None:
        tenant_data = _make_tenant_doc(db_name="(default)")
        mock_db = _mock_admin_db_with_tenant(tenant_data)
        mock_db.batch.return_value = Mock()

        with (
            patch("app.routes.admin_tenants.get_admin_firestore_client", return_value=mock_db),
            patch("app.routes.admin_tenants.delete_identity_tenant"),
            patch("app.routes.admin_tenants.FirestoreAdminClient") as mock_fs_admin_cls,
            patch("app.routes.admin_tenants.clear_tenant_cache"),
        ):
            client = TestClient(app)
            response = client.delete("/api/admin/tenants/tenant-abc")

        assert response.status_code == status.HTTP_200_OK
        mock_fs_admin_cls.assert_not_called()


# --- Resolve Tenant Database (disabled status enforcement) ---


class TestResolveTenantDatabaseStatus:
    def test_returns_none_for_disabled_tenant(self) -> None:
        clear_tenant_cache()

        mock_db = Mock()
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "firestore_database": "tenant-xyz",
            "status": "disabled",
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = resolve_tenant_database("xyz", mock_db)
        assert result is None

        clear_tenant_cache()

    def test_returns_db_name_for_active_tenant(self) -> None:
        clear_tenant_cache()

        mock_db = Mock()
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "firestore_database": "tenant-xyz",
            "status": "active",
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = resolve_tenant_database("xyz", mock_db)
        assert result == "tenant-xyz"

        clear_tenant_cache()

    def test_cached_disabled_tenant_returns_none(self) -> None:
        clear_tenant_cache()

        mock_db = Mock()
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "firestore_database": "tenant-xyz",
            "status": "disabled",
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        # First call populates cache
        resolve_tenant_database("xyz-cached", mock_db)
        # Second call uses cache
        result = resolve_tenant_database("xyz-cached", mock_db)
        assert result is None

        # DB was only hit once (cached)
        assert mock_db.collection.return_value.document.return_value.get.call_count == 1

        clear_tenant_cache()
