# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Typed API error hierarchy and FastAPI exception handlers.

Routes raise these (or service exceptions that inherit from them), and a
single handler emits the standard JSON envelope:

    {"error": {"code": "...", "message": "...", "details": {...}}}

This replaces the inline `raise HTTPException(detail={"error": ...})`
boilerplate that was scattered across the routes layer.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


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


def register_exception_handlers(app: FastAPI) -> None:
    """Wire APIError subclasses to the JSON-envelope response."""

    @app.exception_handler(APIError)
    async def _api_error_handler(_request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_envelope(exc))
