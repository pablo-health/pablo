# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Database session middleware for PostgreSQL backend.

Creates a SQLAlchemy session per request, sets the tenant schema,
and handles commit/rollback/close at request boundaries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

from . import DEFAULT_PRACTICE_SCHEMA, _request_session, get_session_factory, set_tenant_schema

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class DatabaseSessionMiddleware(BaseHTTPMiddleware):
    """Manage SQLAlchemy session lifecycle per HTTP request.

    Creates a session, sets the practice schema search_path,
    commits on success, rolls back on error, and closes afterward.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        session = get_session_factory()()
        _request_session.set(session)

        # For now, always use the default practice schema.
        # Multi-practice: look up practice_id from the authenticated user
        # and set schema accordingly.
        set_tenant_schema(session, DEFAULT_PRACTICE_SCHEMA)

        try:
            response = await call_next(request)
            session.commit()
            return response
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            _request_session.set(None)
