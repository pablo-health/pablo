# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for HIPAA security middleware."""

import pytest
from app.middleware import HTTPSEnforcementMiddleware, SecurityHeadersMiddleware
from app.settings import Settings
from fastapi import FastAPI, status
from fastapi.testclient import TestClient


@pytest.fixture
def production_settings() -> Settings:
    """Settings with HTTPS enforcement enabled (production mode)."""
    return Settings(
        environment="production",
        hsts_max_age=31536000,
        hsts_include_subdomains=True,
        hsts_preload=True,
    )


@pytest.fixture
def development_settings() -> Settings:
    """Settings with HTTPS enforcement disabled (development mode)."""
    return Settings(
        environment="development",
    )


@pytest.fixture
def app_with_https_middleware(production_settings: Settings) -> FastAPI:
    """FastAPI app with HTTPS enforcement middleware."""
    app = FastAPI()
    app.add_middleware(HTTPSEnforcementMiddleware, settings=production_settings)

    @app.get("/test")
    def test_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def app_with_security_headers(production_settings: Settings) -> FastAPI:
    """FastAPI app with security headers middleware."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, settings=production_settings)

    @app.get("/test")
    def test_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def app_with_both_middleware(production_settings: Settings) -> FastAPI:
    """FastAPI app with both middleware in correct order."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, settings=production_settings)
    app.add_middleware(HTTPSEnforcementMiddleware, settings=production_settings)

    @app.get("/test")
    def test_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    return app


class TestHTTPSEnforcementMiddleware:
    """Test HTTPS enforcement for HIPAA compliance."""

    def test_https_request_allowed(self, app_with_https_middleware: FastAPI) -> None:
        """HTTPS requests should be allowed."""
        client = TestClient(app_with_https_middleware, base_url="https://testserver")
        response = client.get("/test")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"status": "ok"}

    def test_http_request_rejected_in_production(self, app_with_https_middleware: FastAPI) -> None:
        """HTTP requests should be rejected when HTTPS enforcement is enabled."""
        client = TestClient(app_with_https_middleware, base_url="http://testserver")
        response = client.get("/test")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "HTTPS required" in response.json()["detail"]

    def test_forwarded_headers_rejected_without_trusted_proxy(
        self, app_with_https_middleware: FastAPI
    ) -> None:
        """Forwarded headers should be ignored when no trusted proxies are configured."""
        client = TestClient(app_with_https_middleware, base_url="http://testserver")
        response = client.get("/test", headers={"X-Forwarded-Proto": "https"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_http_with_forwarded_proto_header_trusted_proxy(self) -> None:
        """HTTP with X-Forwarded-Proto: https allowed when proxies are trusted."""
        settings = Settings(environment="production", trusted_proxy_ips="*")
        app = FastAPI()
        app.add_middleware(HTTPSEnforcementMiddleware, settings=settings)

        @app.get("/test")
        def test_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(app, base_url="http://testserver")
        response = client.get("/test", headers={"X-Forwarded-Proto": "https"})
        assert response.status_code == status.HTTP_200_OK

    def test_http_with_forwarded_ssl_header_trusted_proxy(self) -> None:
        """HTTP with X-Forwarded-SSL: on allowed when proxies are trusted."""
        settings = Settings(environment="production", trusted_proxy_ips="*")
        app = FastAPI()
        app.add_middleware(HTTPSEnforcementMiddleware, settings=settings)

        @app.get("/test")
        def test_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(app, base_url="http://testserver")
        response = client.get("/test", headers={"X-Forwarded-SSL": "on"})
        assert response.status_code == status.HTTP_200_OK

    def test_localhost_allowed_in_development(self, development_settings: Settings) -> None:
        """Localhost HTTP should be allowed in development mode."""
        app = FastAPI()
        app.add_middleware(HTTPSEnforcementMiddleware, settings=development_settings)

        @app.get("/test")
        def test_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(app, base_url="http://testserver")
        response = client.get("/test")
        assert response.status_code == status.HTTP_200_OK

    def test_enforcement_disabled_in_development(self, development_settings: Settings) -> None:
        """HTTPS enforcement should be disabled when enforce_https is False."""
        app = FastAPI()
        app.add_middleware(HTTPSEnforcementMiddleware, settings=development_settings)

        @app.get("/test")
        def test_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(app, base_url="http://testserver")
        response = client.get("/test")
        assert response.status_code == status.HTTP_200_OK


class TestSecurityHeadersMiddleware:
    """Test security headers for HIPAA compliance."""

    def test_hsts_header_added(self, app_with_security_headers: FastAPI) -> None:
        """HSTS header should be added when HTTPS enforcement is enabled."""
        client = TestClient(app_with_security_headers, base_url="https://testserver")
        response = client.get("/test")
        assert "Strict-Transport-Security" in response.headers
        hsts_value = response.headers["Strict-Transport-Security"]
        assert "max-age=31536000" in hsts_value
        assert "includeSubDomains" in hsts_value
        assert "preload" in hsts_value

    def test_hsts_without_subdomains(self) -> None:
        """HSTS header should exclude includeSubDomains when configured."""
        settings = Settings(
            environment="production",
            hsts_max_age=31536000,
            hsts_include_subdomains=False,
            hsts_preload=False,
        )
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, settings=settings)

        @app.get("/test")
        def test_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        client = TestClient(app, base_url="https://testserver")
        response = client.get("/test")
        hsts_value = response.headers["Strict-Transport-Security"]
        assert "max-age=31536000" in hsts_value
        assert "includeSubDomains" not in hsts_value
        assert "preload" not in hsts_value

    def test_content_type_options_header(self, app_with_security_headers: FastAPI) -> None:
        """X-Content-Type-Options header should prevent MIME sniffing."""
        client = TestClient(app_with_security_headers)
        response = client.get("/test")
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_frame_options_header(self, app_with_security_headers: FastAPI) -> None:
        """X-Frame-Options header should prevent clickjacking."""
        client = TestClient(app_with_security_headers)
        response = client.get("/test")
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_csp_header(self, app_with_security_headers: FastAPI) -> None:
        """Content-Security-Policy header should restrict resource loading."""
        client = TestClient(app_with_security_headers)
        response = client.get("/test")
        assert response.headers["Content-Security-Policy"] == "default-src 'self'"

    def test_referrer_policy_header(self, app_with_security_headers: FastAPI) -> None:
        """Referrer-Policy header should control referrer information."""
        client = TestClient(app_with_security_headers)
        response = client.get("/test")
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_permissions_policy_header(self, app_with_security_headers: FastAPI) -> None:
        """Permissions-Policy header should restrict browser features."""
        client = TestClient(app_with_security_headers)
        response = client.get("/test")
        assert response.headers["Permissions-Policy"] == "geolocation=(), microphone=(), camera=()"

    def test_all_headers_present(self, app_with_security_headers: FastAPI) -> None:
        """All security headers should be present on response."""
        client = TestClient(app_with_security_headers)
        response = client.get("/test")

        required_headers = [
            "Strict-Transport-Security",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Content-Security-Policy",
            "Referrer-Policy",
            "Permissions-Policy",
        ]

        for header in required_headers:
            assert header in response.headers, f"Missing required header: {header}"


class TestMiddlewareIntegration:
    """Test both middleware working together."""

    def test_both_middleware_work_together(self, app_with_both_middleware: FastAPI) -> None:
        """HTTPS enforcement and security headers should work together."""
        client = TestClient(app_with_both_middleware, base_url="https://testserver")
        response = client.get("/test")

        # Request should succeed
        assert response.status_code == status.HTTP_200_OK

        # Security headers should be present
        assert "Strict-Transport-Security" in response.headers
        assert "X-Content-Type-Options" in response.headers
        assert "X-Frame-Options" in response.headers

    def test_http_rejected_before_headers_added(self, app_with_both_middleware: FastAPI) -> None:
        """HTTP request should be rejected before security headers are added."""
        client = TestClient(app_with_both_middleware, base_url="http://testserver")
        response = client.get("/test")

        # Request should be rejected
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "HTTPS required" in response.json()["detail"]
