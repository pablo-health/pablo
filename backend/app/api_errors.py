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
_logger = logging.getLogger("pablo.unhandled")

#: Sent when the request carried no credential at all. Not a code any raiser
#: uses — this module infers it, because the distinction it draws is the one
#: that separates an attacker from a browser (see :func:`_log_auth_failed`).
NO_CREDENTIALS = "NO_CREDENTIALS"

# Codes that look like "auth failed" but are expected, benign session
# states rather than a brute-force / bad-token signal — excluded from the
# auth_failed counter so the spike alert isn't polluted by ordinary users:
#   - MFA_REQUIRED: the first-login state before MFA enrolment.
#   - IDLE_TIMEOUT: the activity heartbeat lapsed; the session aged out.
#   - TOKEN_EXPIRED: the client fell behind on ID-token refresh. An expired
#     token carries no attack value (it is rejected on its own), and a
#     single dashboard load fans out enough parallel requests to trip the
#     per-IP spike threshold by itself the moment the token expires.
#   - NO_CREDENTIALS: nothing was presented to reject. A browser opening the
#     app asks "am I signed in?" before it can know, and the answer is a 401;
#     counting that as an attack counts the front door for being a door.
#   - FEATURE_NOT_ENABLED / SUBSCRIPTION_REQUIRED: authorisation denials about
#     ENTITLEMENT, not identity. The caller proved who they are and was told
#     this feature is not theirs, which is a gate working, not a credential
#     being guessed.
# Genuinely suspicious codes (INVALID_TOKEN, TOKEN_REVOKED, USER_DISABLED)
# still count — those are the signal the alert exists to catch.
_AUTH_FAILED_EXEMPT_CODES: frozenset[str] = frozenset(
    {
        "MFA_REQUIRED",
        "IDLE_TIMEOUT",
        "TOKEN_EXPIRED",
        NO_CREDENTIALS,
        "FEATURE_NOT_ENABLED",
        "SUBSCRIPTION_REQUIRED",
    }
)


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
        if code == "UNKNOWN" and not request.headers.get("authorization"):
            # Nothing was presented, so nothing was rejected. Credential
            # attacks require a credential: stuffing, spraying and replay all
            # SEND something and see what sticks. A request with no
            # Authorization header is a client that has not signed in yet, and
            # on this deployment that is the overwhelming majority of 401s —
            # the app asks /api/auth/session whether it has a session before it
            # can possibly know, and every page boot fans out a handful more.
            #
            # Inferred rather than raised because the raisers here are
            # framework-level: fastapi's own security dependency answers with a
            # plain-string detail, which carries no envelope code and so
            # arrived as UNKNOWN — matching nothing in the exempt set below and
            # counting every unauthenticated page load as attack signal.
            #
            # Auth on this deployment is bearer-only (no cookie is ever read
            # for it), so the header's absence is the whole test.
            code = NO_CREDENTIALS
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


def _unhandled_log_extra(request: Request) -> dict[str, str]:
    """Fields for the unhandled-exception record, including the route.

    ``JSONFormatter`` normally fills ``route_template`` from a contextvar, but
    that contextvar is empty for a whole class of failures: a sync route runs
    in a threadpool, which does not inherit the request's context, so anything
    raised under ``run_sync_in_worker_thread`` logged its traceback with no
    route on it at all. The most useful line we emit could not be attributed to
    an endpoint.

    The route is right here on the request, so read it here. The formatter
    skips an ``extra`` key it has already filled from a contextvar, so this
    fills the gap without overriding the request-scoped value where that works.

    The TEMPLATE, never ``request.url.path`` — the concrete path carries record
    ids, and this line lands in a log stream read outside the request.
    """
    extra = {"http_method": request.method}
    template = getattr(request.scope.get("route"), "path", None)
    if isinstance(template, str) and template:
        extra["route_template"] = template
    return extra


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

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Last-resort handler for anything not raised as an APIError/HTTPException.

        Without this, an unexpected exception escapes to Starlette's
        ``ServerErrorMiddleware``, which prints a multi-line traceback to
        stderr — each line lands as a separate Cloud Logging entry, and none
        carry the request context. Logging here instead routes through the
        configured ``JSONFormatter`` (see ``logging_config``): the whole
        traceback rides one record's ``exc_info`` field, alongside
        ``error_class`` and the request-scoped ``request_id`` /
        ``route_template`` / ``user_id`` from contextvars — one structured
        entry per 500. The response body stays a generic envelope so internals
        never leak to the client. ``route_template`` (the matched path
        pattern, not the concrete URL) is what gets logged, so no
        patient-level id from the path reaches the log.
        """
        _logger.error(
            "unhandled_exception",
            exc_info=exc,
            extra=_unhandled_log_extra(request),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An internal error occurred",
                    "details": {},
                }
            },
        )
