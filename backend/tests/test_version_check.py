# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for client version checking."""

from collections.abc import Generator
from unittest.mock import patch

import pytest
from app.main import app
from app.rate_limit import reset_preauth_limiter
from app.version_check import is_version_outdated, parse_semver
from fastapi.testclient import TestClient

# --- Unit tests for version parsing/comparison ---


class TestParseSemver:
    def test_full_semver(self) -> None:
        assert parse_semver("1.2.3") == (1, 2, 3)

    def test_two_part(self) -> None:
        assert parse_semver("1.2") == (1, 2, 0)

    def test_single_part(self) -> None:
        assert parse_semver("2") == (2, 0, 0)

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid version"):
            parse_semver("abc")

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid version"):
            parse_semver("")


class TestIsVersionOutdated:
    @pytest.mark.parametrize(
        ("client", "minimum", "expected"),
        [
            ("1.0.0", "1.0.0", False),
            ("1.0.1", "1.0.0", False),
            ("2.0.0", "1.0.0", False),
            ("0.9.9", "1.0.0", True),
            ("1.0.0", "1.0.1", True),
            ("1.2.0", "1.3.0", True),
            ("1.2", "1.2.1", True),
            ("1.2.1", "1.2", False),
        ],
    )
    def test_comparison(self, client: str, minimum: str, expected: bool) -> None:
        assert is_version_outdated(client, minimum) == expected


# --- Integration tests ---


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    reset_preauth_limiter()


class TestHealthEndpointVersions:
    def test_health_returns_min_versions(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "min_client_versions" in data
        versions = data["min_client_versions"]
        assert "web" in versions
        assert "macos" in versions
        assert "windows" in versions


class TestNativeCodeVersionCheck:
    """Version check on POST /api/auth/native/code"""

    REDIRECT_URI = "pablohealth://callback"

    @pytest.fixture(autouse=True)
    def _mock_firebase(self) -> Generator[None, None, None]:
        with (
            patch("app.routes.auth.initialize_firebase_app"),
            patch("app.routes.auth.firebase_auth") as mock_auth,
        ):
            mock_auth.verify_id_token.return_value = {"uid": "user1"}
            self.mock_firebase_auth = mock_auth
            yield

    def test_outdated_client_blocked(self, client: TestClient) -> None:
        with patch(
            "app.version_check.get_min_versions",
            return_value={"web": "1.0.0", "macos": "2.0.0", "windows": "1.0.0"},
        ):
            resp = client.post(
                "/api/auth/native/code",
                json={
                    "id_token": "tok",
                    "refresh_token": "ref",
                    "redirect_uri": self.REDIRECT_URI,
                },
                headers={
                    "X-Client-Version": "1.5.0",
                    "X-Client-Platform": "macos",
                },
            )
        assert resp.status_code == 426
        detail = resp.json()["detail"]
        assert detail["error"]["code"] == "CLIENT_UPDATE_REQUIRED"
        assert detail["error"]["details"]["min_version"] == "2.0.0"

    def test_current_client_allowed(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "tok",
                "refresh_token": "ref",
                "redirect_uri": self.REDIRECT_URI,
            },
            headers={
                "X-Client-Version": "1.0.0",
                "X-Client-Platform": "macos",
            },
        )
        assert resp.status_code == 200

    def test_no_version_headers_allowed(self, client: TestClient) -> None:
        """Clients without version headers pass through (backwards compat)."""
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "tok",
                "refresh_token": "ref",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        assert resp.status_code == 200
