# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Main FastAPI application for Pablo.
"""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
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
from .notes.practice_types import RepositoryPracticeNoteTypeSource
from .outcome_measures.router import (
    outcome_measures_router,
    patient_outcome_measures_router,
)
from .portal import account_routes as portal_account_routes
from .portal import practice_routes as portal_practice_routes
from .portal import recovery as portal_recovery
from .portal import routes as portal_routes
from .portal.resolver import register_portal_resolver
from .repositories import get_practice_note_type_repository
from .routes import (
    admin,
    admin_pentest,
    auth,
    billing_export,
    billing_queue,
    billing_report,
    booking_links,
    calendar_import,
    chat,
    claim_status,
    claim_webhooks,
    claims,
    claims_export,
    compliance,
    coverage,
    credentialing,
    dashboard,
    ehr_routes,
    ext_auth,
    ical_sync,
    instrument_licenses,
    intake_blank_forms,
    intake_documents,
    intake_packets,
    internal_transcription,
    launch,
    note_types,
    notes,
    passkey,
    patient_appointments,
    patient_booking,
    patient_chat,
    patient_documents,
    patient_intake,
    patient_intake_assignments,
    patient_intake_export,
    patient_intake_review,
    patient_messages,
    patient_payments,
    patient_profile,
    patient_statements,
    patient_write_offs,
    patients,
    payment_webhooks,
    practice_balances,
    practice_billing,
    public_booking,
    scheduling,
    sessions,
    superbills,
    supervision,
    telehealth,
    telehealth_webhooks,
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

    tasks: list[asyncio.Task[None]] = []
    if settings.calendar_auto_sync_enabled and not settings.is_saas:
        from .background_sync import calendar_sync_loop

        tasks.append(asyncio.create_task(calendar_sync_loop()))
        logger.info("Started background calendar sync (every 15 min)")
    if settings.claims_pipeline_enabled and not settings.is_saas:
        from .jobs.claims_pipeline import claims_pipeline_loop

        tasks.append(asyncio.create_task(claims_pipeline_loop()))
        logger.info(
            "Started the claims pipeline (every %d min)", settings.claims_pipeline_interval_minutes
        )
    yield
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # The clearinghouse SDK runs on a loop of its own, on a daemon thread, and
    # its clients hold sessions that have to be closed by that loop rather than
    # this one. Nothing starts it unless a clearinghouse call was actually
    # made, so on a deployment that files no claims this does nothing.
    from .claims.stedi_sdk import shutdown_sdk

    await asyncio.to_thread(shutdown_sdk)


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

# Populate the note-type registry with the built-in note types, and resolve
# each practice's own types from its schema. Downstream consumers may register
# additional formats against the same default registry.
register_builtin_note_types(get_default_registry())
get_default_registry().set_practice_source(
    RepositoryPracticeNoteTypeSource(get_practice_note_type_repository)
)

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
# The export router mounts ``/api/claims/export.csv`` and must come before
# the claims router, whose ``/api/claims/{claim_id}`` would otherwise match it.
app.include_router(claims_export.router)
app.include_router(claim_status.router)
app.include_router(claims.router)
app.include_router(claims.patient_claims_router)
app.include_router(claim_webhooks.router)
app.include_router(superbills.router)
app.include_router(patient_statements.router)
app.include_router(billing_queue.router)
app.include_router(practice_balances.router)
app.include_router(billing_export.router)
app.include_router(billing_report.router)
app.include_router(scheduling.router)


def portal_module_routers(modules: Iterable[str]) -> list[APIRouter]:
    """The patient-facing routers to mount for a given set of portal modules.

    A portal module whose name is not in ``PORTAL_MODULES`` has its
    patient-facing router left out of the application entirely, so its paths
    answer 404 to a patient holding a perfectly good session. That is the
    point: the shell draws its navigation from the capability document, and
    "the shell doesn't show it" is never the only thing standing in front of
    a route.

    ``app.portal.modules`` then reads the answer back off the ASSEMBLED route
    table rather than off the setting, so the capability document and the
    mounting cannot drift apart. This function is the one place the setting
    turns into routers, which is what makes that round trip testable without
    re-importing this module.

    Clinician-facing routers are not here and are mounted unconditionally: a
    practice's own staff reading their own chart data is not a portal module,
    and turning the patient side of messaging off must not take the
    practice's inbox with it.

    ``chat`` is absent for a different reason. It has a gate of its own that
    predates the portal (``enable_patient_chat``), and that flag is what
    decides whether this build serves patient chat at all; naming it in
    ``PORTAL_MODULES`` decides whether the portal offers it, which is the
    narrower question the capability document answers.
    """
    wanted = frozenset(modules)
    by_module: dict[str, APIRouter] = {
        "intake": patient_intake.router,
        "messaging": patient_messages.patient_messages_router,
        "appointments": patient_appointments.router,
    }
    return [router for name, router in by_module.items() if name in wanted]


_portal_module_routers = portal_module_routers(settings.portal_module_names)

# Same principal as the clinician routes above, and not a clinician — see
# each module's docstring. Mounted only when the module list names them.
for _router in _portal_module_routers:
    app.include_router(_router)

# Same principal, and the one place a patient WRITES. Two sessions per request,
# each single-principal — see the module docstring.
app.include_router(patient_booking.router)
app.include_router(sessions.router)
app.include_router(internal_transcription.router)
app.include_router(dashboard.router)
app.include_router(notes.router)
app.include_router(notes.patient_notes_router)
app.include_router(notes.internal_jobs_router)
app.include_router(patient_documents.patient_documents_router)
app.include_router(patient_documents.documents_router)
app.include_router(patient_documents.internal_jobs_router)
# The patient's own half of the same table. Unconditional rather than a
# portal module: it is the seam the modules that DO gate — sending in what
# an intake form asked for, attaching a file to a message — both upload
# through, so gating it here would turn one module off from under another.
# It answers nothing without a stepped-up patient principal.
app.include_router(patient_documents.patient_router)
# The practice's side of the same threads, and the clinician's chart view of
# them. Unconditional: staff reading their own inbox is not a portal module.
# The patient's half went up with the portal modules above.
app.include_router(patient_messages.patient_threads_router)
app.include_router(patient_messages.message_threads_router)
app.include_router(patient_payments.router)
app.include_router(patient_write_offs.router)
app.include_router(payment_webhooks.router)
app.include_router(telehealth.router)
app.include_router(telehealth_webhooks.router)
app.include_router(ehr_routes.route_router)
app.include_router(ehr_routes.navigate_router)
app.include_router(ical_sync.router)
app.include_router(calendar_import.router)
app.include_router(note_types.router)
app.include_router(compliance.router)
app.include_router(supervision.router)
app.include_router(credentialing.router)
app.include_router(outcome_measures_router)
app.include_router(patient_outcome_measures_router)
app.include_router(medications_router)
app.include_router(diagnostic_definitions_router)
app.include_router(diagnostic_assessments_router)
app.include_router(patient_diagnostic_assessments_router)
if settings.enable_patient_chat:
    app.include_router(chat.router)
    # Patient chat's mount gate is this flag and not the portal module list
    # — see ``portal_module_routers`` for why the two questions are
    # different. Naming "chat" in PORTAL_MODULES is what puts it in the
    # portal's navigation; this is what makes it exist.
    app.include_router(patient_chat.router)
# The patient's own half of intake went up with the portal modules above.
# The clinician's read of what that form collected is unconditional for a
# different reason: it sits behind the ordinary clinician door, and an
# already-collected clinical record should stay readable whatever else a
# deployment has turned off.
app.include_router(patient_intake.clinician_router)
# Building the form, as opposed to answering it. Unconditional and behind
# the ordinary clinician door: a practice editing its own paperwork touches
# no patient data and needs no patient front door to be open.
app.include_router(intake_packets.router)
# What the engine knows about each instrument, and what this practice is
# licensed to ask. Same door and same reason as the form builder: an
# instrument's rights are a fact about the instrument, and a practice's
# permission is its own record, so nothing here is anybody's chart.
app.include_router(instrument_licenses.router)
# The documents a practice asks people to sign, and the portal's read of
# one. Unconditional for the same two reasons as the pair above: the
# clinician half is ordinary practice paperwork behind the ordinary door,
# and the patient half answers 401 with no resolver registered.
app.include_router(intake_documents.router)
app.include_router(intake_documents.patient_router)
# The practice's own empty paperwork, and the portal's download of one.
# Same shape and same reasons as the pair above: practice-level rows on the
# clinician side, and a patient half that answers 401 with no resolver
# registered.
app.include_router(intake_blank_forms.router)
app.include_router(intake_blank_forms.patient_router)
# Sending a form to a patient and them filling it in. Both routers are
# unconditional for the reasons above: the patient half answers 401 with no
# resolver registered, and the clinician half sits behind the ordinary
# clinician door.
app.include_router(patient_intake_assignments.router)
app.include_router(patient_intake_assignments.clinician_router)
# Reading a form that came back and answering it: corrections, acceptance,
# and a value entered for somebody in the room. Clinician-only, so it is
# unconditional for the same reason the read beside it is.
app.include_router(patient_intake_review.clinician_router)
# The same form as one file to keep, print or hand over. Clinician-only,
# and mounted beside the review it is reached from.
app.include_router(patient_intake_export.clinician_router)
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
# Patient portal sign-in. Mounting it publishes a surface that mints
# credentials for callers who have none, so it waits for the deployment to
# say yes; with the flag off every path here answers 404. The resolver is
# registered alongside it, because a session nothing can resolve is not a
# session — and it registers on the process-wide registry, which is why this
# happens once, here, rather than per request.
if settings.enable_patient_portal:
    app.include_router(portal_routes.router)
    app.include_router(portal_practice_routes.router)
    # Sign-out, the capability document, and recovery. Same flag: all three
    # are the portal's own account surface, and recovery in particular mints
    # a credential for a caller who has none, which is exactly the decision
    # the flag exists to make.
    app.include_router(portal_account_routes.router)
    app.include_router(portal_recovery.router)
    register_portal_resolver()
# The patient's own demographics. Unconditional, like the intake form's
# clinician read and for the same reason: it sits behind a patient principal
# that only the portal can produce, so with no front door registered it
# answers 401 by itself. It is not a module — a portal with no modules at
# all still lets someone check the address on their chart.
app.include_router(patient_profile.router)


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
