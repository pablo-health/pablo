# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Security middleware for HIPAA compliance.

Implements TLS enforcement and security headers required for PHI transmission.
"""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ..settings import Settings

# Paths a platform health probe calls. Matched exactly, never by prefix.
#
# The health endpoint carries no client data: it reports status, the server
# version and the deployed commit. A platform health probe speaks plain HTTP
# to the container and cannot add forwarded headers, so HTTPS enforcement
# would answer every probe with 400 and no new instance would ever be marked
# ready. The same paths skip the no-store caching headers below.
PLATFORM_PROBE_PATHS: frozenset[str] = frozenset({"/api/health"})


class HTTPSEnforcementMiddleware(BaseHTTPMiddleware):
    """
    Enforce HTTPS for all requests in production.

    HIPAA Requirement: All PHI transmission must use TLS 1.2+.
    This middleware rejects HTTP requests in production environments.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self._trust_all = settings.trusted_proxy_ips == "*"
        self._trusted_ips: set[str] = set()
        if not self._trust_all and settings.trusted_proxy_ips:
            self._trusted_ips = {
                ip.strip() for ip in settings.trusted_proxy_ips.split(",") if ip.strip()
            }

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """
        Reject HTTP requests in production/staging environments.

        Development mode (environment="development"): HTTP allowed for local dev/testing
        Production/Staging mode: HTTPS strictly enforced for HIPAA compliance
        """
        # Allow HTTP in development mode for local development and testing
        if self.settings.is_development:
            return await call_next(request)

        # A platform health probe reaches the container over plain HTTP.
        if request.url.path in PLATFORM_PROBE_PATHS:
            return await call_next(request)

        # In production/staging, enforce HTTPS for HIPAA compliance
        if not self._is_secure(request):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "detail": "HTTPS required. HTTP requests are not allowed for PHI transmission."
                },
            )

        return await call_next(request)

    def _is_from_trusted_proxy(self, request: Request) -> bool:
        """Check if the request originates from a trusted proxy IP.

        - TRUSTED_PROXY_IPS="" (default): trust no proxies (secure default)
        - TRUSTED_PROXY_IPS="*": trust all proxies (Cloud Run / GKE)
        - TRUSTED_PROXY_IPS="10.0.0.1,10.0.0.2": trust specific IPs
        """
        if self._trust_all:
            return True
        if not self._trusted_ips:
            return False
        if not request.client:
            return False
        return request.client.host in self._trusted_ips

    def _is_secure(self, request: Request) -> bool:
        """
        Determine if request is using HTTPS.

        Only trusts forwarded headers (X-Forwarded-Proto, X-Forwarded-SSL)
        when the request comes from a trusted proxy IP.
        """
        # Direct HTTPS connection
        if request.url.scheme == "https":
            return True

        # Only trust forwarded headers from known proxies
        if self._is_from_trusted_proxy(request):
            # Check X-Forwarded-Proto header (reverse proxy, load balancer)
            forwarded_proto = request.headers.get("x-forwarded-proto")
            if forwarded_proto == "https":
                return True

            # Check X-Forwarded-SSL header
            forwarded_ssl = request.headers.get("x-forwarded-ssl")
            if forwarded_ssl == "on":
                return True

        return False


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all responses.

    Implements HSTS and other security best practices for HIPAA compliance.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Add security headers to response."""
        response = await call_next(request)

        # HSTS - HTTP Strict Transport Security (production/staging only)
        if not self.settings.is_development:
            hsts_value = f"max-age={self.settings.hsts_max_age}"
            if self.settings.hsts_include_subdomains:
                hsts_value += "; includeSubDomains"
            if self.settings.hsts_preload:
                hsts_value += "; preload"
            response.headers["Strict-Transport-Security"] = hsts_value

        # X-Content-Type-Options - Prevent MIME sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # X-Frame-Options - Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Content-Security-Policy - Basic CSP, unless the route set its own.
        # The only routes that do serve a practice's website files, with a
        # sandbox policy stricter than this one where it matters (see
        # app.sites.serving).
        if "content-security-policy" not in response.headers:
            response.headers["Content-Security-Policy"] = "default-src 'self'"

        # Referrer-Policy - Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions-Policy - Restrict browser features
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # Cache-Control — prevent caching of PHI responses (HIPAA §164.312)
        # Applied to all authenticated API responses; public assets (health
        # check, static files) are excluded so CDNs can still cache them.
        if request.url.path.startswith("/api/") and request.url.path not in PLATFORM_PROBE_PATHS:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"

        return response
