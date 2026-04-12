# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL database engine, session factory, and tenant schema management.

Schema-per-practice multi-tenancy: each practice gets its own PostgreSQL schema
(practice_{id}) for HIPAA-grade data isolation. The `platform` schema stores
cross-practice data (practice registry, subscriptions, phone numbers).

Usage:
    from app.db import get_db_session, get_engine

    session = get_db_session()  # gets the request-scoped session from contextvar
"""

import re
from contextvars import ContextVar
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..settings import get_settings

_VALID_SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Request-scoped database session, set by DatabaseSessionMiddleware
_request_session: ContextVar[Session | None] = ContextVar("_request_session", default=None)

# Default practice schema for Pablo Solo (single practice)
DEFAULT_PRACTICE_SCHEMA = "practice"
PLATFORM_SCHEMA = "platform"


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Create and cache the SQLAlchemy engine."""
    settings = get_settings()
    if not settings.database_url:
        msg = "DATABASE_URL is required when database_backend=postgres"
        raise ValueError(msg)
    return create_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        echo=settings.debug,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Create and cache the session factory."""
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db_session() -> Session:
    """Get the current request-scoped database session.

    Set by DatabaseSessionMiddleware. Raises RuntimeError if called
    outside a request context (i.e., middleware hasn't run yet).
    """
    session = _request_session.get()
    if session is None:
        msg = (
            "No database session in context. "
            "Ensure DatabaseSessionMiddleware is installed and database_backend=postgres."
        )
        raise RuntimeError(msg)
    return session


def _validate_schema_name(name: str) -> None:
    """Validate a PostgreSQL schema name to prevent SQL injection.

    Schema names are interpolated into SET search_path statements and cannot
    use bind parameters, so we must validate the identifier strictly.
    """
    if not _VALID_SCHEMA_RE.match(name):
        raise ValueError(f"Invalid schema name: {name!r}")


def set_tenant_schema(session: Session, practice_schema: str = DEFAULT_PRACTICE_SCHEMA) -> None:
    """Set the search_path for a session to include the practice schema.

    This scopes all unqualified table references to the practice's schema,
    providing schema-level tenant isolation.
    """
    _validate_schema_name(practice_schema)
    session.execute(text(f"SET search_path = {practice_schema}, {PLATFORM_SCHEMA}, public"))


def create_standalone_session(practice_schema: str | None = None) -> Session:
    """Create a standalone session outside of request context.

    Useful for CLI scripts, migrations, and provisioning.
    Caller is responsible for commit/rollback/close.
    """
    session = get_session_factory()()
    if practice_schema:
        set_tenant_schema(session, practice_schema)
    return session
