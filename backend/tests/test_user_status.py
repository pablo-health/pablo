# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for GET /api/users/me/status.

Covers the practice_id population path added for multi-tenant deployments
so the dashboard can gate practice-scoped UI (e.g. audio-retention
settings) on `practiceId` being present in the response.
"""

from typing import Any
from unittest.mock import patch


class TestUserStatusPracticeId:
    def test_omits_practice_id_when_multi_tenancy_disabled(self, client: Any) -> None:
        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False

            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert "practice_id" not in response.json()

    def test_includes_practice_id_when_email_maps_to_practice(self, client: Any) -> None:
        with (
            patch("app.settings.get_settings") as mock_settings,
            patch(
                "app.auth.service._resolve_practice_from_email",
                return_value=("practice-abc", "practice_abc"),
            ),
        ):
            mock_settings.return_value.multi_tenancy_enabled = True
            mock_settings.return_value.is_saas = False

            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["practice_id"] == "practice-abc"

    def test_omits_practice_id_when_email_has_no_mapping(self, client: Any) -> None:
        with (
            patch("app.settings.get_settings") as mock_settings,
            patch("app.auth.service._resolve_practice_from_email", return_value=None),
        ):
            mock_settings.return_value.multi_tenancy_enabled = True
            mock_settings.return_value.is_saas = False

            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert "practice_id" not in response.json()
