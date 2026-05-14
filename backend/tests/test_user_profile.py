# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the user profile PATCH endpoint and provider_type field."""

from typing import Any
from unittest.mock import patch

from app.models import User
from app.repositories import InMemoryUserRepository


class TestUpdateProfile:
    """Test PATCH /api/users/me."""

    def test_set_provider_type(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)

        response = client.patch("/api/users/me", json={"provider_type": "prescriber"})

        assert response.status_code == 200
        assert response.json()["provider_type"] == "prescriber"
        assert mock_user_repo.get(mock_user.id).provider_type == "prescriber"  # type: ignore[union-attr]

    def test_provider_type_accepts_all_three_values(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        for value in ("therapist", "prescriber", "both"):
            response = client.patch("/api/users/me", json={"provider_type": value})
            assert response.status_code == 200
            assert response.json()["provider_type"] == value

    def test_invalid_provider_type_rejected(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        response = client.patch("/api/users/me", json={"provider_type": "physician"})
        assert response.status_code == 422

    def test_partial_update_preserves_unspecified_fields(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user.provider_type = "therapist"
        mock_user_repo.update(mock_user)

        response = client.patch("/api/users/me", json={"name": "Dr. Renamed"})

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Dr. Renamed"
        assert body["provider_type"] == "therapist"

    def test_user_status_includes_provider_type(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """GET /api/users/me/status exposes provider_type so the frontend
        can gate the onboarding redirect on it."""
        mock_user.provider_type = "both"
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["provider_type"] == "both"

    def test_user_status_provider_type_null_for_new_users(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """A user who has never picked a provider_type returns null,
        which is the onboarding-needed signal."""
        mock_user.provider_type = None
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["provider_type"] is None
