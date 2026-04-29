# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice schema provisioning — create and migrate practice schemas.

On first startup, creates the platform schema and a default practice schema.
For Pablo Practice edition, new practices get their own schemas on demand.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from ..utcnow import utc_now
from . import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA, _validate_schema_name
from .models import Base
from .platform_models import PlatformBase, PracticeRow

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# backend/alembic.ini relative to backend/app/db/provisioning.py.
_ALEMBIC_INI_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


def _now() -> datetime:
    return utc_now()


def ensure_schemas(engine: Engine) -> None:
    """Create platform + default practice schemas if they don't exist.

    Called on application startup when database_backend=postgres.
    Idempotent — safe to call on every boot.
    """
    with engine.connect() as conn:
        # Create platform schema and tables
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
        conn.commit()

    PlatformBase.metadata.create_all(engine)

    # Add columns that may not exist on older databases
    _migrate_platform_columns(engine)

    # Create default practice schema and tables
    create_practice_schema(engine, DEFAULT_PRACTICE_SCHEMA)

    # Migrate columns on all existing practice schemas
    with engine.connect() as conn:
        schemas = conn.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata"
                " WHERE schema_name LIKE 'practice_%'"
            )
        ).fetchall()
    for (schema,) in schemas:
        _migrate_practice_columns(engine, schema)

    # Ensure default practice exists in registry
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        session.execute(text(f"SET search_path = {PLATFORM_SCHEMA}, public"))
        existing = session.get(PracticeRow, "default")
        if not existing:
            session.add(
                PracticeRow(
                    id="default",
                    name="Default Practice",
                    schema_name=DEFAULT_PRACTICE_SCHEMA,
                    owner_email="",
                    owner_user_id="",
                    product="pablo",
                    created_at=_now(),
                )
            )
            session.commit()
            logger.info("Created default practice in registry")


def _migrate_platform_columns(engine: Engine) -> None:
    """Add new columns to existing platform tables.

    Uses ADD COLUMN IF NOT EXISTS so it's safe to run on every boot.
    """
    practices = f"{PLATFORM_SCHEMA}.practices"
    subs = f"{PLATFORM_SCHEMA}.subscriptions"
    migrations = [
        # practices: columns added over time
        f"ALTER TABLE {practices} ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(128) UNIQUE",
        f"ALTER TABLE {practices} ADD COLUMN IF NOT EXISTS"
        " owner_email VARCHAR(255) NOT NULL DEFAULT ''",
        f"ALTER TABLE {practices} ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'",
        # subscriptions: trial tracking
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS trial_start VARCHAR(50)",
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS trial_sessions_used INTEGER DEFAULT 0",
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS trial_sessions_limit INTEGER DEFAULT 15",
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS trial_days_limit INTEGER DEFAULT 0",
        # subscriptions: grace extension
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS grace_extension_used BOOLEAN DEFAULT FALSE",
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS grace_extension_expires_at VARCHAR(50)",
    ]

    # platform.users: new table columns (table created by create_all above)
    users = f"{PLATFORM_SCHEMA}.users"
    migrations.extend(
        [
            f"ALTER TABLE {users} ADD COLUMN IF NOT EXISTS is_platform_admin BOOLEAN DEFAULT FALSE",
            f"ALTER TABLE {users} ADD COLUMN IF NOT EXISTS baa_accepted_at VARCHAR(50)",
            f"ALTER TABLE {users} ADD COLUMN IF NOT EXISTS baa_version VARCHAR(10)",
            f"ALTER TABLE {users} ADD COLUMN IF NOT EXISTS baa_legal_name VARCHAR(255)",
            f"ALTER TABLE {users} ADD COLUMN IF NOT EXISTS baa_license_number VARCHAR(100)",
            f"ALTER TABLE {users} ADD COLUMN IF NOT EXISTS baa_license_state VARCHAR(2)",
            f"ALTER TABLE {users} ADD COLUMN IF NOT EXISTS baa_practice_name VARCHAR(255)",
            f"ALTER TABLE {users} ADD COLUMN IF NOT EXISTS baa_business_address VARCHAR(500)",
            f"ALTER TABLE {users} ADD COLUMN IF NOT EXISTS baa_full_text TEXT",
        ]
    )

    migrations.append(
        f"ALTER TABLE {practices} ADD COLUMN IF NOT EXISTS is_pentest"
        " BOOLEAN NOT NULL DEFAULT FALSE"
    )

    # --- Migrate VARCHAR datetime columns to TIMESTAMP WITH TIME ZONE ---
    etm = f"{PLATFORM_SCHEMA}.email_tenant_mappings"
    allowed = f"{PLATFORM_SCHEMA}.allowed_emails"

    def _alter_ts(table: str, col: str) -> str:
        return (
            f"ALTER TABLE {table} ALTER COLUMN {col} TYPE TIMESTAMP WITH TIME ZONE"
            f" USING CASE WHEN {col}::text = '' THEN NULL"
            f" ELSE {col}::text::timestamptz END"
        )

    migrations.extend(
        [
            _alter_ts(practices, "created_at"),
            _alter_ts(subs, "created_at"),
            _alter_ts(subs, "updated_at"),
            _alter_ts(subs, "trial_start"),
            _alter_ts(subs, "grace_extension_expires_at"),
            _alter_ts(etm, "created_at"),
            _alter_ts(users, "created_at"),
            _alter_ts(users, "mfa_enrolled_at"),
            _alter_ts(users, "baa_accepted_at"),
            _alter_ts(allowed, "added_at"),
        ]
    )

    with engine.connect() as conn:
        for stmt in migrations:
            savepoint = conn.begin_nested()
            try:
                conn.execute(text(stmt))
                savepoint.commit()
            except Exception:
                # Table/column may not exist in this edition (e.g. SaaS-only
                # tables like platform.subscriptions in the OSS build) — skip.
                savepoint.rollback()
        conn.commit()
    _ensure_pentest_tenant_guards(engine)
    logger.info("Platform column migrations applied")


def _ensure_pentest_tenant_guards(engine: Engine) -> None:
    """Idempotent CHECK + trigger install for environments that bypass alembic."""
    import logging

    logger = logging.getLogger(__name__)

    statements = [
        "ALTER TABLE platform.practices"
        " DROP CONSTRAINT IF EXISTS practices_pentest_schema_name",
        "ALTER TABLE platform.practices"
        " ADD CONSTRAINT practices_pentest_schema_name"
        r" CHECK (is_pentest = FALSE OR schema_name LIKE 'practice\_pentest\_%' ESCAPE '\')",
        """
        CREATE OR REPLACE FUNCTION platform.practices_pentest_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.is_pentest IS DISTINCT FROM NEW.is_pentest THEN
                RAISE EXCEPTION
                    'is_pentest is immutable; drop and recreate the tenant'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """,
        "DROP TRIGGER IF EXISTS practices_pentest_immutable ON platform.practices",
        "CREATE TRIGGER practices_pentest_immutable"
        " BEFORE UPDATE OF is_pentest ON platform.practices"
        " FOR EACH ROW"
        " EXECUTE FUNCTION platform.practices_pentest_immutable()",
    ]

    with engine.connect() as conn:
        for stmt in statements:
            savepoint = conn.begin_nested()
            try:
                conn.execute(text(stmt))
                savepoint.commit()
            except Exception:
                logger.exception("Pentest guard step failed: %s", stmt.split()[0:3])
                savepoint.rollback()
        conn.commit()


def _migrate_practice_columns(engine: Engine, schema_name: str) -> None:
    """Add columns to existing practice schemas (idempotent)."""
    ical = f"{schema_name}.ical_sync_configs"
    gcal = f"{schema_name}.google_calendar_tokens"
    sessions = f"{schema_name}.therapy_sessions"
    migrations = [
        f"ALTER TABLE {ical} ADD COLUMN IF NOT EXISTS consecutive_error_count INTEGER DEFAULT 0",
        f"ALTER TABLE {gcal} ADD COLUMN IF NOT EXISTS consecutive_error_count INTEGER DEFAULT 0",
        f"ALTER TABLE {gcal} ADD COLUMN IF NOT EXISTS last_sync_error TEXT",
        f"ALTER TABLE {sessions} ADD COLUMN IF NOT EXISTS transcription_job_metadata JSONB",
    ]

    # --- Migrate VARCHAR datetime columns to TIMESTAMP WITH TIME ZONE ---
    patients = f"{schema_name}.patients"
    sessions = f"{schema_name}.therapy_sessions"
    prompts = f"{schema_name}.ehr_prompts"
    routes = f"{schema_name}.ehr_routes"
    appts = f"{schema_name}.appointments"
    rules = f"{schema_name}.availability_rules"
    mappings = f"{schema_name}.ical_client_mappings"
    profiles = f"{schema_name}.clinician_profiles"

    def _alter_ts(table: str, col: str) -> str:
        return (
            f"ALTER TABLE {table} ALTER COLUMN {col} TYPE TIMESTAMP WITH TIME ZONE"
            f" USING CASE WHEN {col}::text = '' THEN NULL"
            f" ELSE {col}::text::timestamptz END"
        )

    migrations.extend(
        [
            # patients
            _alter_ts(patients, "last_session_date"),
            _alter_ts(patients, "next_session_date"),
            _alter_ts(patients, "created_at"),
            _alter_ts(patients, "updated_at"),
            # therapy_sessions
            _alter_ts(sessions, "session_date"),
            _alter_ts(sessions, "created_at"),
            _alter_ts(sessions, "scheduled_at"),
            _alter_ts(sessions, "started_at"),
            _alter_ts(sessions, "ended_at"),
            _alter_ts(sessions, "updated_at"),
            _alter_ts(sessions, "processing_started_at"),
            _alter_ts(sessions, "processing_completed_at"),
            # ehr_prompts
            _alter_ts(prompts, "updated_at"),
            # ehr_routes
            _alter_ts(routes, "last_success"),
            _alter_ts(routes, "created_at"),
            _alter_ts(routes, "updated_at"),
            # appointments
            _alter_ts(appts, "start_at"),
            _alter_ts(appts, "end_at"),
            _alter_ts(appts, "created_at"),
            _alter_ts(appts, "updated_at"),
            # availability_rules
            _alter_ts(rules, "created_at"),
            _alter_ts(rules, "updated_at"),
            # google_calendar_tokens
            _alter_ts(gcal, "last_synced_at"),
            _alter_ts(gcal, "connected_at"),
            # ical_client_mappings
            _alter_ts(mappings, "created_at"),
            # ical_sync_configs
            _alter_ts(ical, "last_synced_at"),
            _alter_ts(ical, "connected_at"),
            # clinician_profiles
            _alter_ts(profiles, "joined_at"),
        ]
    )

    with engine.connect() as conn:
        for stmt in migrations:
            savepoint = conn.begin_nested()
            try:
                conn.execute(text(stmt))
                savepoint.commit()
            except Exception:
                # Table/column may not exist in this practice schema — skip safely
                savepoint.rollback()
        conn.commit()


def _stamp_alembic_at_head(engine: Engine, schema_name: str) -> None:
    """Insert ``alembic_version`` at current head for a freshly-provisioned tenant.

    Without this row, the per-tenant fan-out tool (pa-5in.1) has no version to
    upgrade FROM — every future migration would either no-op or error against
    the new schema. We stamp at head because the schema was just built from
    current SQLAlchemy models via ``Base.metadata.create_all`` and is
    definitionally at HEAD.

    Idempotent: ``MigrationContext.stamp`` deletes existing rows before
    inserting, so a retry after a partial provisioning is safe.
    """
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI_PATH)))
    head = script.get_current_head()
    if head is None:
        return
    with engine.begin() as conn:
        ctx = MigrationContext.configure(
            connection=conn,
            opts={
                "version_table_schema": schema_name,
                "version_table": "alembic_version",
            },
        )
        ctx.stamp(script, head)


def create_practice_schema(engine: Engine, schema_name: str) -> None:
    """Create a new practice schema with all practice tables.

    Idempotent — can be called on existing schemas.
    Enables Row-Level Security on tables with a user_id column
    (skipped for the base template schema).
    """
    _validate_schema_name(schema_name)
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
        conn.commit()

    # Create all practice-schema tables in the new schema
    # Temporarily rebind table metadata to the target schema
    for table in Base.metadata.sorted_tables:
        table.schema = schema_name

    Base.metadata.create_all(engine)

    # Reset schema to None (default) so future calls don't have stale schema
    for table in Base.metadata.sorted_tables:
        table.schema = None

    # Add columns that may not exist on older practice schemas
    _migrate_practice_columns(engine, schema_name)

    # Enable RLS on all tables with a user_id column (HIPAA defense-in-depth).
    # Skipped for the base 'practice' template schema.
    if schema_name != DEFAULT_PRACTICE_SCHEMA:
        from sqlalchemy.orm import Session as OrmSession

        from . import enable_rls_on_schema

        with OrmSession(engine) as session:
            enable_rls_on_schema(session, schema_name)

        # Stamp alembic_version on tenant schemas only. The 'practice' template's
        # version row is owned by the deploy-time `alembic upgrade head` job;
        # stamping it here would race with that flow.
        _stamp_alembic_at_head(engine, schema_name)

    logger.info("Practice schema '%s' ready", schema_name)
