# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for tenant provisioning service."""

from unittest.mock import MagicMock, Mock, patch

import pytest
from app.services.tenant_provisioning import (
    ProvisionResult,
    TenantProvisioningError,
    TenantProvisioningService,
)
from google.cloud.firestore_admin_v1.types import Database


@pytest.fixture
def mock_admin_db():
    """Mock Firestore admin (default) database client."""
    db = MagicMock()
    batch = MagicMock()
    db.batch.return_value = batch
    return db


@pytest.fixture
def mock_firestore_admin():
    """Mock FirestoreAdminClient for database creation."""
    client = MagicMock()
    operation = MagicMock()
    client.create_database.return_value = operation
    return client


@pytest.fixture
def service(mock_admin_db, mock_firestore_admin):
    """Create a TenantProvisioningService with mocked dependencies."""
    return TenantProvisioningService(
        admin_db=mock_admin_db,
        firestore_admin=mock_firestore_admin,
        project_id="test-project",
    )


@pytest.fixture(autouse=True)
def _mock_firebase_init():
    """Mock Firebase functions that require GCP credentials."""
    with (
        patch("app.services.tenant_provisioning.enable_google_sign_in"),
        patch("app.services.tenant_provisioning.enable_mfa"),
    ):
        yield


class TestProvisionPractice:
    """Test the full provisioning flow."""

    @patch("app.services.tenant_provisioning.create_tenant")
    def test_creates_tenant_database_and_mappings(self, mock_create, service, mock_admin_db):
        mock_tenant = Mock()
        mock_tenant.tenant_id = "practice-abc123"
        mock_create.return_value = mock_tenant

        result = service.provision_practice("Dr. Smith's Practice", "dr.smith@gmail.com")

        assert result == ProvisionResult(
            tenant_id="practice-abc123",
            database_name="tenant-practice-abc123",
        )
        mock_create.assert_called_once_with(display_name="Dr. Smith's Practice")
        mock_admin_db.batch().commit.assert_called_once()

    @patch("app.services.tenant_provisioning.create_tenant")
    def test_creates_firestore_database(self, mock_create, service, mock_firestore_admin):
        mock_tenant = Mock()
        mock_tenant.tenant_id = "practice-abc123"
        mock_create.return_value = mock_tenant

        service.provision_practice("Practice", "owner@example.com")

        call_kwargs = mock_firestore_admin.create_database.call_args
        assert call_kwargs.kwargs["parent"] == "projects/test-project"
        assert call_kwargs.kwargs["database_id"] == "tenant-practice-abc123"

        db_arg = call_kwargs.kwargs["database"]
        assert db_arg.type_ == Database.DatabaseType.FIRESTORE_NATIVE
        assert db_arg.location_id == "nam5"

    @patch("app.services.tenant_provisioning.create_tenant")
    def test_stores_tenant_mapping(self, mock_create, service, mock_admin_db):
        mock_tenant = Mock()
        mock_tenant.tenant_id = "practice-xyz"
        mock_create.return_value = mock_tenant

        service.provision_practice("My Practice", "owner@example.com")

        batch = mock_admin_db.batch()
        set_calls = batch.set.call_args_list

        # First set call: tenants collection
        tenant_ref = mock_admin_db.collection("tenants").document("practice-xyz")
        tenant_data = set_calls[0][0][1]
        assert set_calls[0][0][0] == tenant_ref
        assert tenant_data["tenant_id"] == "practice-xyz"
        assert tenant_data["practice_name"] == "My Practice"
        assert tenant_data["firestore_database"] == "tenant-practice-xyz"
        assert tenant_data["owner_email"] == "owner@example.com"
        assert tenant_data["status"] == "active"

    @patch("app.services.tenant_provisioning.create_tenant")
    def test_stores_email_tenant_mapping(self, mock_create, service, mock_admin_db):
        mock_tenant = Mock()
        mock_tenant.tenant_id = "practice-xyz"
        mock_create.return_value = mock_tenant

        service.provision_practice("My Practice", "Owner@Example.COM")

        batch = mock_admin_db.batch()
        set_calls = batch.set.call_args_list

        # Second set call: email_tenants collection (lowercased)
        email_ref = mock_admin_db.collection("email_tenants").document("owner@example.com")
        email_data = set_calls[1][0][1]
        assert set_calls[1][0][0] == email_ref
        assert email_data["tenant_id"] == "practice-xyz"
        assert email_data["email"] == "owner@example.com"

    @patch("app.services.tenant_provisioning.create_tenant")
    def test_database_name_is_lowercase(self, mock_create, service):
        mock_tenant = Mock()
        mock_tenant.tenant_id = "Practice-ABC123"
        mock_create.return_value = mock_tenant

        result = service.provision_practice("Practice", "owner@example.com")

        assert result.database_name == "tenant-practice-abc123"


class TestProvisioningRollback:
    """Test rollback behavior when provisioning fails."""

    @patch("app.services.tenant_provisioning.delete_tenant")
    @patch("app.services.tenant_provisioning.create_tenant")
    def test_rolls_back_tenant_on_database_failure(
        self, mock_create, mock_delete, service, mock_firestore_admin
    ):
        mock_tenant = Mock()
        mock_tenant.tenant_id = "practice-fail"
        mock_create.return_value = mock_tenant

        mock_firestore_admin.create_database.return_value.result.side_effect = Exception(
            "DB creation failed"
        )

        with pytest.raises(TenantProvisioningError, match="Failed to provision"):
            service.provision_practice("Failing Practice", "fail@example.com")

        mock_delete.assert_called_once_with("practice-fail")

    @patch("app.services.tenant_provisioning.delete_tenant")
    @patch("app.services.tenant_provisioning.create_tenant")
    def test_rolls_back_tenant_on_mapping_failure(
        self, mock_create, mock_delete, service, mock_admin_db
    ):
        mock_tenant = Mock()
        mock_tenant.tenant_id = "practice-fail"
        mock_create.return_value = mock_tenant

        mock_admin_db.batch().commit.side_effect = Exception("Batch write failed")

        with pytest.raises(TenantProvisioningError):
            service.provision_practice("Practice", "owner@example.com")

        mock_delete.assert_called_once_with("practice-fail")

    @patch("app.services.tenant_provisioning.create_tenant")
    def test_no_rollback_when_tenant_creation_fails(self, mock_create, service):
        mock_create.side_effect = Exception("Tenant creation failed")

        with pytest.raises(TenantProvisioningError):
            service.provision_practice("Practice", "owner@example.com")

        # No rollback needed — tenant was never created

    @patch("app.services.tenant_provisioning.delete_tenant")
    @patch("app.services.tenant_provisioning.create_tenant")
    def test_rollback_failure_does_not_mask_original_error(
        self, mock_create, mock_delete, service, mock_firestore_admin
    ):
        mock_tenant = Mock()
        mock_tenant.tenant_id = "practice-fail"
        mock_create.return_value = mock_tenant

        mock_firestore_admin.create_database.return_value.result.side_effect = Exception(
            "DB creation failed"
        )
        mock_delete.side_effect = Exception("Rollback also failed")

        with pytest.raises(TenantProvisioningError, match="DB creation failed"):
            service.provision_practice("Practice", "owner@example.com")


class TestProvisionResult:
    """Test ProvisionResult data class."""

    def test_frozen(self):
        result = ProvisionResult(tenant_id="t1", database_name="db1")
        with pytest.raises(AttributeError):
            result.tenant_id = "t2"  # type: ignore[misc]

    def test_equality(self):
        a = ProvisionResult(tenant_id="t1", database_name="db1")
        b = ProvisionResult(tenant_id="t1", database_name="db1")
        assert a == b
