# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for admin user management and allowlist endpoints."""

from datetime import datetime
from typing import Any

from app.models import User
from app.repositories import InMemoryAllowlistRepository, InMemoryUserRepository


class TestAdminUserList:
    """Test GET /api/admin/users."""

    def test_list_users(self, client: Any, mock_user_repo: InMemoryUserRepository) -> None:
        # Add a second user
        mock_user_repo.update(
            User(
                id="user-2",
                email="user2@example.com",
                name="Second User",
                created_at=datetime.fromisoformat("2024-01-02T00:00:00+00:00"),
            )
        )

        response = client.get("/api/admin/users")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] >= 2
        emails = [u["email"] for u in data["data"]]
        assert "user2@example.com" in emails


class TestAdminDisableEnable:
    """Test PATCH /api/admin/users/{user_id}/disable and /enable."""

    def test_disable_user(self, client: Any, mock_user_repo: InMemoryUserRepository) -> None:
        target = User(
            id="target-user",
            email="target@example.com",
            name="Target User",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        )
        mock_user_repo.update(target)

        response = client.patch("/api/admin/users/target-user/disable")
        assert response.status_code == 200
        assert response.json()["message"] == "User disabled"

        updated = mock_user_repo.get("target-user")
        assert updated is not None
        assert updated.status == "disabled"

    def test_enable_user(self, client: Any, mock_user_repo: InMemoryUserRepository) -> None:
        target = User(
            id="disabled-user",
            email="disabled@example.com",
            name="Disabled User",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            status="disabled",
        )
        mock_user_repo.update(target)

        response = client.patch("/api/admin/users/disabled-user/enable")
        assert response.status_code == 200
        assert response.json()["message"] == "User enabled"

        updated = mock_user_repo.get("disabled-user")
        assert updated is not None
        assert updated.status == "approved"

    def test_disable_nonexistent_user(self, client: Any) -> None:
        response = client.patch("/api/admin/users/nonexistent/disable")
        assert response.status_code == 404

    def test_cannot_disable_self(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        # The mock_user from conftest is the "admin" making the request
        mock_user_repo.update(mock_user)

        response = client.patch(f"/api/admin/users/{mock_user.id}/disable")
        assert response.status_code == 400
        assert response.json()["detail"]["error"]["code"] == "CANNOT_DISABLE_SELF"


class TestAllowlistCRUD:
    """Test allowlist management endpoints."""

    def test_add_to_allowlist(
        self, client: Any, mock_allowlist_repo: InMemoryAllowlistRepository
    ) -> None:
        response = client.post("/api/admin/allowlist", json={"email": "New@Example.com"})
        assert response.status_code == 201
        assert response.json()["email"] == "new@example.com"
        assert mock_allowlist_repo.is_allowed("new@example.com")

    def test_list_allowlist(
        self, client: Any, mock_allowlist_repo: InMemoryAllowlistRepository
    ) -> None:
        mock_allowlist_repo.add("a@example.com", "admin")
        mock_allowlist_repo.add("b@example.com", "admin")

        response = client.get("/api/admin/allowlist")
        assert response.status_code == 200

        data = response.json()
        assert data["total"] == 2
        emails = [e["email"] for e in data["data"]]
        assert "a@example.com" in emails
        assert "b@example.com" in emails

    def test_remove_from_allowlist(
        self, client: Any, mock_allowlist_repo: InMemoryAllowlistRepository
    ) -> None:
        mock_allowlist_repo.add("remove-me@example.com", "admin")

        response = client.delete("/api/admin/allowlist/remove-me@example.com")
        assert response.status_code == 200
        assert not mock_allowlist_repo.is_allowed("remove-me@example.com")

    def test_remove_nonexistent_email(self, client: Any) -> None:
        response = client.delete("/api/admin/allowlist/nonexistent@example.com")
        assert response.status_code == 404
