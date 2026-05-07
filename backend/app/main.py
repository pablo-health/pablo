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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .api_errors import register_exception_handlers
from .db import get_engine
from .middleware import HTTPSEnforcementMiddleware, SecurityHeadersMiddleware
from .notes import get_default_registry, register_builtin_note_types
from .routes import (
    admin,
    admin_pentest,
    auth,
    compliance,
    ehr_routes,
    ext_auth,
    ical_sync,
    note_types,
    notes,
    patients,
    scheduling,
    sessions,
    users,
)
from .settings import get_settings
from .version_check import get_min_versions, get_server_version

logger = logging.getLogger(__name__)
settings = get_settings()

# Security: warn loudly if development mode bypasses are active
if settings.is_development:
    logger.warning(
        "SECURITY: Running in development mode — "
        "MFA enforcement, admin checks, and HTTPS enforcement are DISABLED. "
        "Do NOT use ENVIRONMENT=development in production."
    )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage background tasks across the application lifecycle."""
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

# Populate the note-type registry with OSS built-ins (SOAP + Narrative).
# SaaS overlays register premium formats against the same default registry.
register_builtin_note_types(get_default_registry())

# PostgreSQL session middleware (must be added before security middleware
# so it wraps the request lifecycle inside the security layer)
if settings.database_backend == "postgres":
    from .db import get_engine
    from .db.middleware import DatabaseSessionMiddleware
    from .db.provisioning import ensure_schemas

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
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Tenant-ID",
        "X-Client-Version",
        "X-Client-Platform",
    ],
)

# Core routes (always included)
app.include_router(auth.router)
app.include_router(ext_auth.router)
app.include_router(admin.router)
app.include_router(admin_pentest.router)
app.include_router(users.router)
app.include_router(patients.router)
app.include_router(scheduling.router)
app.include_router(sessions.router)
app.include_router(notes.router)
app.include_router(notes.patient_notes_router)
app.include_router(ehr_routes.route_router)
app.include_router(ehr_routes.navigate_router)
app.include_router(ical_sync.router)
app.include_router(note_types.router)
app.include_router(compliance.router)


@app.get("/api/health")
def health_check() -> dict[str, object]:
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
