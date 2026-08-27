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
    _current_patient_id,
    _current_tenant_schema,
    _current_user_id,
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


def _verify_and_stash_clinician_identity(request: Request) -> None:
    """Verify a bearer token as a clinician's and cache the result.

    Runs on **every** request carrying a bearer token, whether or not
    multi-tenancy is enabled. Two things depend on that being
    unconditional:

    * Clinician dependencies (``require_mfa``, ``get_current_user_id``,
      ``get_tenant_context``) reuse the stash instead of re-verifying, so
      doing it here costs nothing net — it moves the verification, it
      does not add one.
    * ``get_patient_context`` treats the presence of a stashed identity as
      "this credential belongs to a clinician" and refuses the request.
      That is the only structural thing keeping a clinician's token from
      being offered to a patient resolver, since both principals arrive
      as ``Authorization: Bearer``. Gating it on ``multi_tenancy_enabled``
      would silently disarm that guard on every single-tenant install —
      which is the default, and the configuration a self-hosted patient
      companion would run in.

    Errors are swallowed: this is a cache-priming step, and a bad token
    must be rejected by the auth dependencies with their specific error
    codes, not turned into a 500 here. A token that fails to verify simply
    leaves nothing stashed, which is the correct input to both consumers.
    """
    auth_header = request.headers.get("authorization", "")
    # Case-INSENSITIVE on the scheme, and it has to be. RFC 7235 says the
    # scheme is case-insensitive, FastAPI's own ``HTTPBearer`` compares
    # ``scheme.lower() != "bearer"``, and ``_credential_from_request``
    # lowercases it too — so ``Authorization: bearer <token>`` is a
    # perfectly working clinician credential everywhere else in the stack.
    # A case-SENSITIVE check here used to skip the stash for exactly that
    # header, which left ``get_patient_context``'s clinician guard with
    # nothing to read: one lowercased letter and a clinician's token got
    # offered to every patient resolver. The guard is only as good as this
    # parse, so this must stay at least as permissive as HTTPBearer's.
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return

    token = token.strip()
    try:
        from ..auth.service import verify_token

        identity = verify_token(token)

        # Cache the verified identity on request.state so downstream
        # dependencies (require_mfa, get_current_user_id, etc.) reuse it
        # instead of re-verifying. request.state is request-scoped — no
        # cross-request leakage. Firebase tokens also populate the legacy
        # decoded-token cache for the _get_cached_token fast-path.
        request.state.verified_identity = identity
        request.state.verified_identity_token = token
        if identity.provider == "firebase":
            request.state.decoded_firebase_token = identity.claims
            request.state.verified_firebase_token_raw = token
    except Exception:
        logger.debug("Middleware identity verification skipped (token parse failed)")


def _resolve_schema_from_request(request: Request) -> str | None:
    """Extract tenant schema from the Authorization header.

    Reuses the identity ``_verify_and_stash_clinician_identity`` already
    verified for this request, then resolves the practice schema from its
    email. Tenant resolution is email-based, so it is provider-agnostic.
    Returns None if unauthenticated or no mapping. Errors are swallowed —
    auth dependencies will reject bad tokens later.
    """
    identity = getattr(request.state, "verified_identity", None)
    if identity is None or not identity.email:
        return None

    try:
        from ..auth.service import _resolve_practice_from_email

        practice = _resolve_practice_from_email(identity.email)
        if practice:
            return practice[1]  # schema_name
    except Exception:
        logger.debug("Middleware schema resolution skipped (practice lookup failed)")
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

        # Verify the bearer token before any dependency runs, regardless of
        # tenancy mode. Deliberately outside the multi-tenancy branch below:
        # `get_patient_context` reads the stash to refuse clinician
        # credentials on patient routes, and that guard has to work on
        # single-tenant installs too. See the function's docstring.
        _verify_and_stash_clinician_identity(request)

        # Resolve tenant schema from auth token before any dependencies run.
        # This prevents race conditions where repo factories query the DB
        # before get_tenant_context sets the schema.
        schema = DEFAULT_PRACTICE_SCHEMA
        if settings.multi_tenancy_enabled:
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
            _current_tenant_schema.set(None)
            # Clear the RLS user id so a future request reusing this
            # ContextVar slot (only possible if a future code path
            # ever shares it across requests) sees a clean slate.
            # Belt-and-braces against cross-tenant identity leak.
            _current_user_id.set(None)
            # Same for the patient principal. This one matters more than
            # belt-and-braces: a patient id left armed would re-arm
            # app.current_patient_id on the next transaction through the
            # after_begin listener, so a leaked slot reads as "a patient
            # is calling" on a request that has no patient at all.
            _current_patient_id.set(None)

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
