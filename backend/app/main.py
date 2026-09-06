# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Main FastAPI application for Pablo.
"""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .api_errors import register_exception_handlers
from .auth.route_security import truly_public
from .db import get_engine
from .db.middleware import DatabaseSessionMiddleware
from .db.provisioning import ensure_schemas
from .diagnostics.router import (
    diagnostic_assessments_router,
    diagnostic_definitions_router,
    patient_diagnostic_assessments_router,
)
from .logging_config import configure_logging
from .medications.router import medications_router
from .middleware import (
    DPoPMiddleware,
    HTTPSEnforcementMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from .notes import get_default_registry, register_builtin_note_types
from .outcome_measures.router import (
    outcome_measures_router,
    patient_outcome_measures_router,
)
from .routes import (
    admin,
    admin_pentest,
    auth,
    billing_queue,
    booking_links,
    calendar_import,
    chat,
    claims,
    compliance,
    coverage,
    dashboard,
    ehr_routes,
    ext_auth,
    ical_sync,
    internal_transcription,
    launch,
    note_types,
    notes,
    passkey,
    patient_documents,
    patient_payments,
    patients,
    payment_webhooks,
    practice_billing,
    public_booking,
    scheduling,
    sessions,
    supervision,
    users,
)
from .settings import get_settings, log_startup_posture
from .version_check import get_min_versions, get_server_version

configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"))

logger = logging.getLogger(__name__)
settings = get_settings()

# Security: warn loudly if development mode bypasses are active
if settings.is_development:
    logger.warning(
        "SECURITY: Running in development mode — "
        "MFA enforcement, admin checks, and HTTPS enforcement are DISABLED. "
        "Do NOT use ENVIRONMENT=development in production."
    )

# Say out loud whether reserved test addresses can register themselves.
# Off is the default; a deployment that turns it on should see it in the
# boot log every time, next to the project it applies to.
if settings.test_identity_signup_armed:
    logger.warning(
        "SECURITY: test-identity self-signup is ARMED for project %s — "
        "reserved pentestuser-/e2etest- addresses can register without an "
        "allowlist entry (ALLOW_TEST_IDENTITY_SIGNUP=true).",
        settings.gcp_project_id or "<unset>",
    )
else:
    logger.info("Test-identity self-signup is disarmed.")

log_startup_posture(settings, logger)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage background tasks across the application lifecycle."""
    from .services.llm_telemetry import init_llm_tracing

    # No-op unless a collector endpoint is configured (see settings).
    init_llm_tracing(settings)

    # app_url defaults to http://localhost:3000 so a local checkout works with
    # no .env. Outside development that default is silently wrong: the launch
    # router builds the companion handoff as "{app_url}/launch/{intent_id}",
    # so an unset APP_URL hands the desktop app a link to the therapist's own
    # machine and Start Session does nothing. It also drives the Stripe portal
    # return_url. Nothing failed loudly, so this went unnoticed in a deployed
    # environment — hence the startup check.
    if not settings.is_development and "localhost" in settings.app_url:
        logger.error(
            "APP_URL is unset or points at localhost (%s). Companion launch "
            "links and Stripe billing return URLs are broken in this "
            "environment. Set APP_URL to the public frontend origin "
            "(e.g. https://app.pablo.health).",
            settings.app_url,
        )

    task = None
    if settings.calendar_auto_sync_enabled and not settings.is_saas:
        from .background_sync import calendar_sync_loop

        task = asyncio.create_task(calendar_sync_loop())
        logger.info("Started background calendar sync (every 15 min)")
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title=settings.api_title,
    description=settings.api_description,
    version=get_server_version(),
    debug=settings.debug,
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
    openapi_url="/openapi.json" if settings.is_development else None,
    lifespan=lifespan,
)

register_exception_handlers(app)

# Populate the note-type registry with the built-in note types
# (SOAP + Narrative). Downstream consumers may register additional
# formats against the same default registry.
register_builtin_note_types(get_default_registry())

# DPoP proof-validation middleware. Added BEFORE DatabaseSessionMiddleware
# so it ends up *inside* it at request time (add_middleware is
# outermost-last): the device lookup needs the request-scoped DB session,
# and the user resolution reuses the identity the DB-session middleware
# already verified+cached. Hard no-op unless ENABLE_DPOP_VALIDATION is on.
# See docs/design/companion-dpop-binding.md § Stage 2.
app.add_middleware(DPoPMiddleware, settings=settings)

# Database session middleware (must be added before security middleware
# so it wraps the request lifecycle inside the security layer)
ensure_schemas(get_engine())
app.add_middleware(DatabaseSessionMiddleware)

# Security middleware - HIPAA TLS enforcement (order matters: security first)
app.add_middleware(SecurityHeadersMiddleware, settings=settings)
app.add_middleware(HTTPSEnforcementMiddleware, settings=settings)

# CORS configuration
# Parse CORS origins (comma-separated string to list)
cors_origins = [origin.strip() for origin in settings.cors_origins.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Tenant-ID",
        "X-Client-Version",
        "X-Client-Platform",
    ],
)

# Request-context middleware — added last so it wraps every other layer
# as the outermost middleware. request_id is then set before any
# downstream logging (including HTTPS-rejected responses) and the id
# survives onto the X-Request-Id response header for clients.
app.add_middleware(RequestContextMiddleware)

# Core routes (always included)
app.include_router(auth.router)
app.include_router(passkey.router)
app.include_router(ext_auth.router)
app.include_router(admin.router)
app.include_router(admin_pentest.router)
app.include_router(users.router)
app.include_router(patients.router)
app.include_router(practice_billing.router)
app.include_router(coverage.payers_router)
app.include_router(coverage.router)
app.include_router(coverage.jobs_router)
app.include_router(claims.router)
app.include_router(claims.patient_claims_router)
app.include_router(billing_queue.router)
app.include_router(scheduling.router)
app.include_router(sessions.router)
app.include_router(internal_transcription.router)
app.include_router(dashboard.router)
app.include_router(notes.router)
app.include_router(notes.patient_notes_router)
app.include_router(notes.internal_jobs_router)
app.include_router(patient_documents.patient_documents_router)
app.include_router(patient_documents.documents_router)
app.include_router(patient_documents.internal_jobs_router)
app.include_router(patient_payments.router)
app.include_router(payment_webhooks.router)
app.include_router(ehr_routes.route_router)
app.include_router(ehr_routes.navigate_router)
app.include_router(ical_sync.router)
app.include_router(calendar_import.router)
app.include_router(note_types.router)
app.include_router(compliance.router)
app.include_router(supervision.router)
app.include_router(outcome_measures_router)
app.include_router(patient_outcome_measures_router)
app.include_router(medications_router)
app.include_router(diagnostic_definitions_router)
app.include_router(diagnostic_assessments_router)
app.include_router(patient_diagnostic_assessments_router)
if settings.enable_patient_chat:
    app.include_router(chat.router)
# Companion launch-intent handoff. Mounted only when the flag is on so
# /api/launch/* returns 404 until the desktop companions ship the
# verified-link redemption path. See docs/design/companion-thin-client.md.
if settings.enable_launch_intent:
    app.include_router(launch.router)
# Public booking links (docs/design/public-booking.md). Management CRUD is
# always mounted; the unauthenticated /api/public/* surface only when the
# deployment opts in.
app.include_router(booking_links.router)
if settings.public_booking_enabled:
    app.include_router(public_booking.router)


@app.get("/api/health")
def health_check(_public: None = Depends(truly_public)) -> dict[str, object]:
    """Health check endpoint.

    Returns server status, deployed git SHA, and minimum required
    client versions. Verifies DB connectivity — a failed SELECT 1
    bubbles up as 5xx so deploy smoke tests catch broken bindings.
    """
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return {
        "status": "healthy",
        "server_version": get_server_version(),
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "min_client_versions": get_min_versions(),
    }
