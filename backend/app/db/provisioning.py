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


# Arbitrary 64-bit key for the boot-time provisioning advisory lock. Any
# constant works — the value just needs to be stable so every booting
# instance picks the same lock. Generated once via random.randint to avoid
# colliding with locks the application may take elsewhere.
_PROVISIONING_LOCK_KEY = 7283194065831042197


def ensure_schemas(engine: Engine) -> None:
    """Create platform + default practice schemas if they don't exist.

    Called on application startup when database_backend=postgres.
    Idempotent — safe to call on every boot.

    Concurrency: when Cloud Run starts multiple container instances
    simultaneously (deployment rollout + min-instance warm-up overlap),
    every instance races through this function. ``create_all`` checks
    ``has_table`` before each CREATE, but the check-then-create window is
    not atomic, so two instances can both observe "table missing" and
    both emit ``CREATE TABLE`` — the loser gets ``DuplicateTable`` and
    exits, failing the deploy. We serialize the mutation phase behind a
    session-scoped Postgres advisory lock so only one instance runs the
    create/migrate work at a time. The lock auto-releases when the
    connection closes.
    """
    with engine.connect() as conn:
        # pg_advisory_lock blocks until acquired. Cheap (in-memory in PG),
        # held only for the duration of provisioning (sub-second).
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _PROVISIONING_LOCK_KEY})
        try:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
            conn.commit()

            PlatformBase.metadata.create_all(engine)

            # Add columns that may not exist on older databases
            _migrate_platform_columns(engine)

            # Create default practice schema and tables
            create_practice_schema(engine, DEFAULT_PRACTICE_SCHEMA)

            # Per-tenant schema evolution belongs in the alembic chain
            # (``backend/alembic/versions/``), fanned out at deploy time
            # via ``saas.bin.migrate`` / ``app.db.migrate_tenants``. The
            # boot path historically iterated ``practice_*`` schemas and
            # ran a runtime column-patch helper; that left freshly-
            # provisioned tenants broken until the next backend revision
            # restarted, and silently swallowed failures via savepoints.
            # Removed in b7de65c29385 — see that revision's docstring.

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
        finally:
            conn.execute(
                text("SELECT pg_advisory_unlock(:k)"),
                {"k": _PROVISIONING_LOCK_KEY},
            )
            conn.commit()


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
    migrations.extend(
        [
            f"ALTER TABLE {practices} ADD COLUMN IF NOT EXISTS"
            " audio_retention_days INTEGER NOT NULL DEFAULT 365",
            f"ALTER TABLE {practices} ADD COLUMN IF NOT EXISTS"
            " offboard_scheduled_at TIMESTAMP WITH TIME ZONE",
            f"ALTER TABLE {practices} ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE",
        ]
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
                # Table/column may not exist in this deployment (e.g.
                # overlay-managed tables like platform.subscriptions
                # when the overlay is not installed) — skip.
                savepoint.rollback()
        conn.commit()
    _ensure_pentest_tenant_guards(engine)
    logger.info("Platform column migrations applied")


def _ensure_pentest_tenant_guards(engine: Engine) -> None:
    """Idempotent CHECK + trigger install for environments that bypass alembic."""
    import logging

    logger = logging.getLogger(__name__)

    statements = [
        "ALTER TABLE platform.practices DROP CONSTRAINT IF EXISTS practices_pentest_schema_name",
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


# Path to the canonical tenant SQL — the `pg_dump --schema=practice`
# output from an alembic-head database, with `practice.` placeholders.
# Regenerate via `backend/scripts/regen_tenant_template.py` after any
# alembic revision that touches DDL.
_TENANT_TEMPLATE_SQL_PATH = Path(__file__).resolve().parent / "tenant_template.sql"
_TENANT_SCHEMA_PLACEHOLDER = "__TENANT_SCHEMA__"


def create_practice_schema(engine: Engine, schema_name: str) -> None:
    """Create or reconcile a practice schema.

    Two paths now, picked by whether the target schema already has
    tables:

    1. **Empty schema → apply tenant_template.sql + stamp HEAD.** Same
       path for the default ``practice`` template AND for any new
       per-tenant schema (``PentestTenantService.provision`` or
       future per-customer provisioning). Eliminates today's drift
       class: the ``practice`` template used to be built via
       ``Base.metadata.create_all`` while tenants were built from
       SQL — two paths, two slightly different end states. The
       2026-05-21 prod-promote hit that drift (``chat_messages`` in
       prod's ``practice`` was missing every CHECK constraint while
       per-tenant copies had them). Now both paths produce byte-
       identical state because they apply the same SQL.

    2. **Already-populated schema → legacy reconcile via create_all.**
       Only ``migrate_tenants.upgrade_tenant_schema`` should hit this
       branch — when a pre-template-era tenant is being brought up to
       the current ORM shape. The caller stamps alembic afterwards
       and column shape evolution carries forward through the chain.

    RLS policies still live outside the template (column introspection
    in Python). Skipped for the default template — single-tenancy OSS
    deployments use ``practice`` as their runtime schema without the
    multi-tenant middleware that sets ``app.current_user_id``, and
    RLS-without-user-id fails closed (zero rows). Per-tenant schemas
    always get RLS.
    """
    _validate_schema_name(schema_name)

    is_default_template = schema_name == DEFAULT_PRACTICE_SCHEMA
    schema_already_populated = _schema_has_tables(engine, schema_name)

    if schema_already_populated:
        _create_practice_schema_legacy(engine, schema_name)
    else:
        _apply_tenant_template(engine, schema_name)
        _stamp_alembic_at_head(engine, schema_name)

    if not is_default_template:
        from sqlalchemy.orm import Session as OrmSession

        from . import enable_rls_on_schema

        with OrmSession(engine) as session:
            session.execute(text(f"SET search_path = {schema_name}, {PLATFORM_SCHEMA}, public"))
            enable_rls_on_schema(session, schema_name)

    logger.info("Practice schema '%s' ready", schema_name)


def _create_practice_schema_legacy(engine: Engine, schema_name: str) -> None:
    """Pre-template provisioning path — ``create_all`` only.

    Used for the default ``practice`` template schema (alembic owns
    its DDL end-to-end; we just need ``create_all`` on first-boot DBs
    where alembic hasn't run yet) and for reconciling per-tenant
    schemas that already have tables from a pre-template provisioning.

    Column shape evolution lives entirely in the alembic chain after
    revision ``b7de65c29385`` — for the default template, alembic
    runs at deploy time; for legacy tenant reconcile, the caller
    (``migrate_tenants.upgrade_tenant_schema``) stamps the schema and
    subsequent ``alembic upgrade head`` invocations carry it forward.
    """
    with engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
        conn.commit()

    for table in Base.metadata.sorted_tables:
        table.schema = schema_name

    Base.metadata.create_all(engine)

    for table in Base.metadata.sorted_tables:
        table.schema = None


def _apply_tenant_template(engine: Engine, schema_name: str) -> None:
    """Apply :file:`tenant_template.sql` to a fresh tenant schema.

    The template is the canonical DDL for a tenant at alembic-HEAD —
    every table, function, index, and FK that the migration chain
    produces. Substituting ``__TENANT_SCHEMA__`` here makes every
    object live in the new tenant's namespace, so unqualified
    references inside function bodies and policies resolve via the
    tenant's own search_path at query time.
    """
    sql = _TENANT_TEMPLATE_SQL_PATH.read_text().replace(_TENANT_SCHEMA_PLACEHOLDER, schema_name)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
        # Set search_path before the template runs so any unqualified
        # references *inside* function bodies, defaults, or check
        # constraints resolve to the new tenant — not whichever schema
        # the pool's connection last touched.
        conn.execute(text(f"SET search_path = {schema_name}, {PLATFORM_SCHEMA}, public"))
        # pg_dump orders objects so functions are created before the
        # tables their bodies reference (e.g. ``has_patient_access``
        # SELECTs from ``patient_clinicians``). Defer body validation
        # until function execution, mirroring what pg_restore does.
        conn.execute(text("SET check_function_bodies = off"))
        # exec_driver_sql sends the multi-statement string straight to
        # psycopg2 — SQLAlchemy's ``text()`` would try to parse bind
        # params and trip on dollar-quoted function bodies.
        conn.exec_driver_sql(sql)


def _schema_has_tables(engine: Engine, schema_name: str) -> bool:
    """Return True if ``schema_name`` already contains any base table.

    Used to choose between the canonical-template apply (fresh
    schemas) and the legacy reconcile path (existing schemas).
    """
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = :s AND table_type = 'BASE TABLE'"
            ),
            {"s": schema_name},
        ).scalar()
    return bool(count and count > 0)
