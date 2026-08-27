# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Database session middleware for PostgreSQL backend.

Creates a SQLAlchemy session per request, resolves the tenant schema
from the auth token, and handles commit/rollback/close at request boundaries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
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
    """Verify an ``Authorization`` credential as a clinician's and cache it.

    Runs on **every** request carrying one, whether or not multi-tenancy is
    enabled. Two things depend on that being unconditional:

    * Clinician dependencies (``require_mfa``, ``get_current_user_id``,
      ``get_tenant_context``) reuse the stash instead of re-verifying, so
      doing it here costs nothing net — it moves the verification, it
      does not add one.
    * ``get_patient_context`` treats the presence of a stashed identity as
      "this credential belongs to a clinician" and refuses the request.
      That is the only structural thing keeping a clinician's token from
      being offered to a patient resolver. Gating it on
      ``multi_tenancy_enabled`` would silently disarm that guard on every
      single-tenant install — which is the default, and the configuration
      a self-hosted patient companion would run in.

    Errors are swallowed: this is a cache-priming step, and a bad token
    must be rejected by the auth dependencies with their specific error
    codes, not turned into a 500 here. A token that fails to verify simply
    leaves nothing stashed, which is the correct input to both consumers.

    **A rejection and a failure are not the same thing**, though, and the
    difference matters to the patient guard. "No stash" would otherwise
    mean both "every verifier decided this is not a clinician's token" and
    "a verifier could not decide" — and the second is the interesting one,
    because Firebase's ``verify_id_token`` makes a network round trip
    (``check_revoked=True``), so a provider outage produces it while
    holding a credential that may well be a clinician's.
    ``VerifierRegistry.verify`` already draws exactly that line: a 401 is
    "not my token" and falls through to the next backend, while anything
    else propagates immediately. So a 401 leaves no marker and a patient
    resolver may see the credential; anything else sets
    ``request.state.clinician_verification_errored`` and
    ``get_patient_context`` refuses on it.

    **It ignores the scheme entirely, and that is deliberate.** The obvious
    version checks for ``bearer`` — and then the guard covers exactly one
    scheme while ``_credential_from_request`` builds a ``PatientCredential``
    out of *any* scheme it finds, keying the registry on it. A clinician's
    token sent as ``Authorization: Token <jwt>`` would be skipped here and
    handed to whatever resolver registers under ``"token"``, which is the
    single point of failure the guard exists to remove. That is also the
    exact shape of the bug this parse already had once, where a
    case-sensitive ``"Bearer "`` comparison let ``bearer`` through. The
    seam is deliberately scheme-open so a future front door can register a
    non-bearer kind; the guard has to be at least as open as the seam it
    guards, so it attempts verification on any credential value and lets
    the verifier decide. A non-clinician credential simply fails to verify
    and stashes nothing, which is the same outcome as skipping it.
    """
    auth_header = request.headers.get("authorization", "")
    # Mirror ``_credential_from_request``'s parse exactly — same split, same
    # strip, same emptiness test. The two must agree on what counts as a
    # credential, or the guard has a hole shaped like the difference.
    scheme, _, token = auth_header.partition(" ")
    if not scheme or not token.strip():
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
    except HTTPException as exc:
        # 401 is the registry's "not my token" — every verifier rejected it,
        # which is a *decision*, and the correct one to hand a patient route:
        # this credential is not a clinician's. Any other status means a
        # verifier failed for some reason of its own
        # (``VerifierRegistry.verify`` re-raises non-401s immediately rather
        # than falling through), so treat it as an undecided verification.
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            request.state.clinician_verification_errored = True
        logger.debug("Middleware identity verification rejected the credential")
    except Exception:
        # Could not decide. Firebase's ``verify_id_token`` does a network
        # round trip (``check_revoked=True``), so a provider outage lands
        # here — and without a marker it is indistinguishable from "this
        # was never a clinician's token". ``get_patient_context`` reads
        # this and refuses, which is the same rule
        # ``PatientResolverRegistry.resolve`` already applies one layer
        # down: an exception means could-not-decide, and letting a
        # could-not-decide fall through converts it into "someone else may
        # decide". Here that someone is every registered patient resolver,
        # holding a clinician's credential.
        request.state.clinician_verification_errored = True
        logger.debug("Middleware identity verification could not complete")


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
