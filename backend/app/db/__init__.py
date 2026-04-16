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


def assert_tenant_schema_set() -> None:
    """Verify the session's search_path is NOT the default 'practice' schema.

    Call this before any write operation when multi_tenancy_enabled=True.
    Prevents accidental cross-tenant data leakage (HIPAA violation).
    Raises RuntimeError if the schema hasn't been switched from the default.
    """
    from ..settings import get_settings

    if not get_settings().multi_tenancy_enabled:
        return

    session = _request_session.get()
    if session is None:
        return

    result = session.execute(text("SHOW search_path"))
    search_path = result.scalar() or ""
    is_default = (
        search_path.startswith(DEFAULT_PRACTICE_SCHEMA + ",")
        or search_path == DEFAULT_PRACTICE_SCHEMA
    )
    if is_default:
        msg = (
            f"TENANT ISOLATION VIOLATION: search_path is '{search_path}' "
            f"(default schema) but multi_tenancy_enabled=True. "
            f"This would write data to the shared schema instead of the tenant's schema. "
            f"Ensure get_tenant_context() ran before this code path."
        )
        raise RuntimeError(msg)


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


def enable_rls_on_schema(session: Session, schema_name: str) -> None:
    """Enable Row-Level Security on all tables with a user_id column in the given schema.

    Creates a policy that restricts rows to those matching the session variable
    `app.current_user_id`. Uses FORCE ROW LEVEL SECURITY so the policy applies
    even to the table owner (defense-in-depth for HIPAA isolation).

    The `current_setting('app.current_user_id', true)` call returns NULL when the
    variable is unset, causing the policy to match zero rows — fail-closed.

    Idempotent: uses CREATE POLICY ... IF NOT EXISTS and is safe to re-run.
    """
    import logging

    logger = logging.getLogger(__name__)

    _validate_schema_name(schema_name)
    if schema_name == DEFAULT_PRACTICE_SCHEMA:
        logger.info("Skipping RLS on template schema '%s'", schema_name)
        return

    # Find all tables in this schema that have a user_id column
    rows = session.execute(
        text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND column_name = 'user_id'"
        ),
        {"schema": schema_name},
    ).fetchall()

    if not rows:
        logger.info("No tables with user_id in schema '%s' — nothing to do", schema_name)
        return

    for (table_name,) in rows:
        qualified = f"{schema_name}.{table_name}"

        # Enable RLS on the table (idempotent — no error if already enabled)
        session.execute(text(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY"))
        session.execute(text(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY"))

        # Create the policy (DROP + CREATE to ensure it's up to date)
        policy_name = "rls_user_isolation"
        session.execute(text(f"DROP POLICY IF EXISTS {policy_name} ON {qualified}"))
        session.execute(
            text(
                f"CREATE POLICY {policy_name} ON {qualified} "
                f"USING (user_id = current_setting('app.current_user_id', true))"
            )
        )
        logger.info("RLS enabled on %s", qualified)

    session.commit()


def enable_rls_on_all_practice_schemas(engine: Engine | None = None) -> None:
    """Apply RLS to every existing practice_* schema (excluding the template).

    Does NOT run automatically — call from a migration script or management command.
    Skips the base 'practice' template schema and the 'platform' schema.
    """
    import logging

    logger = logging.getLogger(__name__)

    if engine is None:
        engine = get_engine()

    with Session(engine) as session:
        schemas = session.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name LIKE 'practice_%'"
            )
        ).fetchall()

        for (schema_name,) in schemas:
            if schema_name == DEFAULT_PRACTICE_SCHEMA:
                continue
            logger.info("Applying RLS to schema '%s'", schema_name)
            enable_rls_on_schema(session, schema_name)
