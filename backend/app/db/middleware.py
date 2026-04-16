# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Database session middleware for PostgreSQL backend.

Creates a SQLAlchemy session per request, resolves the tenant schema
from the auth token, and handles commit/rollback/close at request boundaries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

from ..settings import get_settings
from . import (
    DEFAULT_PRACTICE_SCHEMA,
    _request_session,
    get_session_factory,
    set_tenant_schema,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response
    from sqlalchemy.orm import Session
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


def _resolve_schema_from_request(request: Request) -> str | None:
    """Extract tenant schema from the Authorization header.

    Decodes the Firebase token to get the user's email, then resolves
    the practice schema. Returns None if unauthenticated or no mapping.
    Errors are swallowed — auth dependencies will reject bad tokens later.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]
    try:
        from ..auth.service import (
            _extract_email,
            _resolve_practice_from_email,
            verify_firebase_token,
        )

        decoded = verify_firebase_token(token)

        # Cache the decoded token on request.state so downstream dependencies
        # (require_mfa, get_current_user_id, etc.) can skip re-verification.
        # request.state is request-scoped — no cross-request leakage.
        request.state.decoded_firebase_token = decoded
        request.state.verified_firebase_token_raw = token

        email = _extract_email(decoded)
        if not email:
            return None
        practice = _resolve_practice_from_email(email)
        if practice:
            return practice[1]  # schema_name
    except Exception:
        logger.debug("Middleware schema resolution skipped (token parse failed)")
    return None


class DatabaseSessionMiddleware(BaseHTTPMiddleware):
    """Manage SQLAlchemy session lifecycle per HTTP request.

    Creates a session, resolves the tenant schema from the auth token,
    and sets the search_path BEFORE any route handler or dependency runs.
    This eliminates race conditions where repo dependencies resolve before
    get_tenant_context sets the schema.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        session = get_session_factory()()
        _request_session.set(session)

        settings = get_settings()

        # Resolve tenant schema from auth token before any dependencies run.
        # This prevents race conditions where repo factories query the DB
        # before get_tenant_context sets the schema.
        schema = DEFAULT_PRACTICE_SCHEMA
        if settings.database_backend == "postgres" and settings.multi_tenancy_enabled:
            resolved = _resolve_schema_from_request(request)
            if resolved:
                schema = resolved
        set_tenant_schema(session, schema)

        try:
            response = await call_next(request)
            # Guard: refuse to commit if the session still points at the
            # default 'practice' schema and multi-tenancy is on.  This
            # catches any code path that forgot to call set_tenant_schema
            # before writing tenant data — a HIPAA-grade safety net.
            if session.dirty or session.new or session.deleted:
                self._assert_tenant_isolation(session)
            session.commit()
            return response
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            _request_session.set(None)

    @staticmethod
    def _assert_tenant_isolation(session: Session) -> None:
        """Prevent commits to the default practice schema when multi-tenancy is on."""
        settings = get_settings()
        if not settings.multi_tenancy_enabled:
            return

        from sqlalchemy import text

        result = session.execute(text("SHOW search_path"))
        search_path = result.scalar() or ""
        # The default schema is "practice, platform, public".
        # A properly scoped tenant session looks like "practice_xxx, platform, public".
        first_schema = search_path.split(",")[0].strip().strip('"')
        if first_schema == DEFAULT_PRACTICE_SCHEMA:
            logger.error(
                "TENANT ISOLATION VIOLATION blocked: attempted commit to default "
                "'%s' schema with multi_tenancy_enabled=True. "
                "Dirty=%d New=%d Deleted=%d search_path='%s'",
                DEFAULT_PRACTICE_SCHEMA,
                len(session.dirty),
                len(session.new),
                len(session.deleted),
                search_path,
            )
            session.rollback()
            msg = (
                "Tenant isolation violation: data would be written to the shared "
                "schema instead of the tenant's schema. This request has been blocked."
            )
            raise RuntimeError(msg)
