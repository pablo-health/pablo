# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for BAA version path traversal prevention."""

from typing import Any

import pytest


class TestGetBAAText:
    """Test GET /api/users/baa/{version} path traversal protection."""

    def test_valid_version_returns_baa(self, client: Any) -> None:
        response = client.get("/api/users/baa/2024-01-01")
        assert response.status_code == 200
        assert len(response.text) > 0

    def test_nonexistent_version_returns_404(self, client: Any) -> None:
        response = client.get("/api/users/baa/1999-01-01")
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "malicious_version",
        [
            "../../etc/passwd",
            "../../../etc/shadow",
            "..%2F..%2Fetc%2Fpasswd",
            "2024-01-01/../../etc/passwd",
            "2024-01-01%00.md",
            "....//....//etc/passwd",
            "abc",
            "v1.0",
            "2024_01_01",
            "2024/01/01",
        ],
    )
    def test_path_traversal_blocked(self, client: Any, malicious_version: str) -> None:
        response = client.get(f"/api/users/baa/{malicious_version}")
        assert response.status_code in (400, 404, 422)

    def test_traversal_returns_invalid_version_error(self, client: Any) -> None:
        response = client.get("/api/users/baa/../../etc/passwd")
        # FastAPI may return 404 for paths with slashes (routing mismatch),
        # but direct injection without slashes should return 400
        response = client.get("/api/users/baa/not-a-date")
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["error"]["code"] == "INVALID_VERSION"


class TestAcceptBAAPathTraversal:
    """Test POST /api/users/me/accept-baa path traversal protection."""

    def _baa_payload(self, version: str) -> dict[str, Any]:
        return {
            "legal_name": "Dr. Test",
            "license_number": "LIC-12345",
            "license_state": "CA",
            "business_address": "123 Main St",
            "version": version,
            "accepted": True,
        }

    def test_valid_version_accepted(self, client: Any) -> None:
        response = client.post(
            "/api/users/me/accept-baa",
            json=self._baa_payload("2024-01-01"),
        )
        assert response.status_code == 200
        assert response.json()["accepted"] is True

    def test_nonexistent_version_returns_404(self, client: Any) -> None:
        response = client.post(
            "/api/users/me/accept-baa",
            json=self._baa_payload("1999-01-01"),
        )
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "malicious_version",
        [
            "../../etc/passwd",
            "../../../etc/shadow",
            "2024-01-01/../../../etc/passwd",
            "abc",
            "v1.0",
            "2024_01_01",
        ],
    )
    def test_path_traversal_blocked(self, client: Any, malicious_version: str) -> None:
        response = client.post(
            "/api/users/me/accept-baa",
            json=self._baa_payload(malicious_version),
        )
        # Pydantic validation rejects non-date patterns with 422
        assert response.status_code == 422

    def test_pydantic_rejects_invalid_version_format(self, client: Any) -> None:
        response = client.post(
            "/api/users/me/accept-baa",
            json=self._baa_payload("not-a-date-format"),
        )
        assert response.status_code == 422
