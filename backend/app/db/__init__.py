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
    """Enable Row-Level Security on every patient-scoped table in the schema.

    Two policy shapes, picked by what columns the table has:

    * **user_id column** (clinician owns the row directly — patients,
      therapy_sessions, appointments, etc.): policy matches rows where
      ``user_id = current_setting('app.current_user_id', true)``. This
      is the original direct-ownership shape; preserves prior behavior
      so multi-clinician sharing on these tables remains a follow-up.
    * **patient_id column with no user_id** (row is owned indirectly
      via the patient — currently just ``notes``): policy matches rows
      where ``has_patient_access(patient_id, current_setting(...))``
      returns true. The function is defined by migration
      ``777b846ab944`` and looks up the ``patient_clinicians`` access
      table — supports primary, co-treating, supervisor, and coverage
      grants without further policy churn.

    Tables with neither column (audit_logs, clinician_profiles, etc.)
    are intentionally skipped; they're not patient-scoped and live
    behind the tenant-schema boundary plus application-layer checks.

    ``FORCE ROW LEVEL SECURITY`` applies the policy even to the table
    owner (defense-in-depth for HIPAA isolation). ``current_setting``
    with ``missing_ok=true`` returns NULL when the session variable is
    unset, so any query without a tenant-context middleware that set
    ``app.current_user_id`` sees zero rows — fail-closed.

    Idempotent: DROP POLICY IF EXISTS before each CREATE so the policy
    body always tracks the current code.
    """
    import logging

    logger = logging.getLogger(__name__)

    _validate_schema_name(schema_name)
    if schema_name == DEFAULT_PRACTICE_SCHEMA:
        logger.info("Skipping RLS on template schema '%s'", schema_name)
        return

    # One query per schema; gives us {table_name: {columns...}} and lets
    # us pick the right policy shape per table.
    column_rows = session.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = :schema "
            "AND column_name IN ('user_id', 'patient_id', 'id') "
            "AND table_name != 'alembic_version'"
        ),
        {"schema": schema_name},
    ).fetchall()

    tables: dict[str, set[str]] = {}
    for table_name, column_name in column_rows:
        tables.setdefault(table_name, set()).add(column_name)

    if not tables:
        logger.info(
            "No tables with user_id or patient_id in schema '%s' — nothing to do",
            schema_name,
        )
        return

    # `patient_clinicians` is the access table itself — applying the
    # access-function policy to its own backing table would cause an
    # infinite recursion in the policy evaluator. Direct ownership via
    # user_id is the correct shape: only the clinician whose grant row
    # this is can see it. Other grants for the same patient are
    # invisible to peers, which matches the v1 "primary clinician owns
    # the relationship" model.
    for table_name, columns in tables.items():
        qualified = f"{schema_name}.{table_name}"
        session.execute(text(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY"))
        session.execute(text(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY"))
        session.execute(text(f"DROP POLICY IF EXISTS rls_user_isolation ON {qualified}"))
        session.execute(text(f"DROP POLICY IF EXISTS rls_patient_access ON {qualified}"))

        # Pick the policy shape:
        #   * patients (the access target itself) — gate by id via the
        #     has_patient_access function.
        #   * Any other table with patient_id — gate by patient_id via
        #     has_patient_access.
        #   * Fallback to direct user_id ownership for tables that have
        #     a user_id column but no patient_id (e.g. availability_rules,
        #     google_calendar_tokens, ical_client_mappings).
        if table_name == "patients":
            session.execute(
                text(
                    f"CREATE POLICY rls_patient_access ON {qualified} "
                    f"USING (has_patient_access("
                    f"  id, current_setting('app.current_user_id', true)"
                    f"))"
                )
            )
            logger.info("RLS (patient_access on id) enabled on %s", qualified)
        elif "patient_id" in columns:
            session.execute(
                text(
                    f"CREATE POLICY rls_patient_access ON {qualified} "
                    f"USING (has_patient_access("
                    f"  patient_id, "
                    f"  current_setting('app.current_user_id', true)"
                    f"))"
                )
            )
            logger.info("RLS (patient_access) enabled on %s", qualified)
        elif "user_id" in columns:
            session.execute(
                text(
                    f"CREATE POLICY rls_user_isolation ON {qualified} "
                    f"USING (user_id = current_setting('app.current_user_id', true))"
                )
            )
            logger.info("RLS (user_id) enabled on %s", qualified)

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
