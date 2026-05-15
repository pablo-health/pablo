# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Typed API error hierarchy and FastAPI exception handlers.

Routes raise these (or service exceptions that inherit from them), and a
single handler emits the standard JSON envelope:

    {"error": {"code": "...", "message": "...", "details": {...}}}

This replaces the inline `raise HTTPException(detail={"error": ...})`
boilerplate that was scattered across the routes layer.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .request_context import extract_request_context

_auth_logger = logging.getLogger("pablo.auth")

# Codes that look like "auth failed" but are part of the expected
# first-login flow rather than a brute-force / bad-token signal.
# Excluded from the auth_failed counter so the spike alert isn't
# polluted by users who simply haven't enrolled MFA yet.
_AUTH_FAILED_EXEMPT_CODES: frozenset[str] = frozenset({"MFA_REQUIRED"})


class APIError(Exception):
    """Base class for API errors that map to a JSON envelope response."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "INTERNAL_ERROR"

    default_message: str = "An error occurred"

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        *,
        code: str | None = None,
    ) -> None:
        self.message = message if message is not None else self.default_message
        self.details = details or {}
        if code is not None:
            self.code = code
        super().__init__(self.message)


class BadRequestError(APIError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "BAD_REQUEST"


class UnauthorizedError(APIError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"


class ForbiddenError(APIError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"


class NotFoundError(APIError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ConflictError(APIError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class UnprocessableEntityError(APIError):
    status_code = 422
    code = "UNPROCESSABLE_ENTITY"


class ServerError(APIError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "INTERNAL_ERROR"


def _envelope(exc: APIError) -> dict[str, Any]:
    return {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
    }


def _extract_error_code(detail: object) -> str:
    """Pull the envelope error.code out of an HTTPException detail, if present.

    Auth raises ``HTTPException(detail={"error": {"code": "TOKEN_EXPIRED", ...}})``
    today, but some legacy sites pass a plain string. Tolerate both.
    """
    if isinstance(detail, dict):
        err = detail.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            if isinstance(code, str):
                return code
    return "UNKNOWN"


def _log_auth_failed(request: Request, exc: StarletteHTTPException) -> None:
    """Emit a structured ``event=auth_failed`` record for 401/403 responses.

    Feeds the auth-failure-spike Cloud Monitoring alert (THERAPY-8uww):
    >20 occurrences with the same ``source_ip`` in 5 min trips the
    notification channel. Stays PHI-free — the only identifiers are
    the rejection reason code and the request source IP.

    Best-effort: a failure to log must not turn a 401 into a 500, so
    any exception inside the emit is swallowed.
    """
    try:
        code = _extract_error_code(exc.detail)
        if code in _AUTH_FAILED_EXEMPT_CODES:
            return
        ip, ua = extract_request_context(request)
        _auth_logger.warning(
            "auth failed: %s",
            code,
            extra={
                "event": "auth_failed",
                "reason": code,
                "source_ip": ip,
                "user_agent": ua,
                "status_code": exc.status_code,
            },
        )
    except Exception:
        # Don't shadow the original auth failure if logging itself breaks.
        _auth_logger.exception("auth_failed emit raised")


def register_exception_handlers(app: FastAPI) -> None:
    """Wire APIError subclasses to the JSON-envelope response."""

    @app.exception_handler(APIError)
    async def _api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_envelope(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """Pass-through that also emits ``event=auth_failed`` on 401/403.

        Preserves FastAPI's default response shape by delegating rendering
        to :func:`fastapi.exception_handlers.http_exception_handler` — we
        only piggy-back logging onto it. Registered for
        ``StarletteHTTPException`` so it catches both raw Starlette and
        FastAPI ``HTTPException`` (FastAPI's is a subclass).
        """
        if exc.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ):
            _log_auth_failed(request, exc)
        # http_exception_handler returns Response | JSONResponse;
        # mypy is happy with the type because both inherit from Response.
        response = await http_exception_handler(request, exc)
        return response  # type: ignore[return-value]
