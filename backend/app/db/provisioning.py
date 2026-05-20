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

    Schema evolution split:

    * **Bootstrap (here)** — ``CREATE SCHEMA IF NOT EXISTS`` for the
      platform schema and ``PlatformBase.metadata.create_all`` so a
      fresh DB has every table the current ORM expects. ``create_all``
      is a no-op against tables that already exist; it does NOT alter
      column types on tables that exist with stale shapes.
    * **Platform column evolution** — owned by the SaaS overlay's
      ``backend/saas/db/alembic/`` chain (``alembic_version_saas``
      bookkeeping). Historically lived in a runtime patch
      (``_migrate_platform_columns``) that ran on every boot; absorbed
      into the alembic chain in SaaS revision ``f7d2a3e8b194`` and
      removed from this file. OSS itself has no platform alembic
      chain — only the SaaS overlay does — so installs without the
      overlay rely on ``create_all`` matching the ORM shape.
    * **Tenant column evolution** — owned by the OSS tenant chain
      (``backend/alembic/``) and fanned out per-tenant by
      ``saas.bin.migrate`` / ``app.db.migrate_tenants.fan_out``.

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

            # Platform-schema column evolution lives in the SaaS overlay's
            # alembic chain (``backend/saas/db/alembic/``), fanned out at
            # deploy time via ``saas.bin.migrate``. The boot path used to
            # run a runtime ``_migrate_platform_columns`` helper that
            # issued ~17 ALTER TABLE statements with bare-except savepoints;
            # absorbed into SaaS revision ``f7d2a3e8b194`` and deleted.

            # Pentest CHECK + immutability trigger on ``platform.practices``.
            # Declarative DB guards, not column evolution — kept here until
            # they can move into the SaaS alembic chain alongside the
            # ``is_pentest`` column itself.
            _ensure_pentest_tenant_guards(engine)

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

    There are three callers to keep in mind:

    1. ``ensure_schemas`` at boot — passes ``DEFAULT_PRACTICE_SCHEMA``
       (``practice``). That schema is the canonical template; alembic
       owns its DDL and ``alembic_version`` row. We use ``create_all``
       (the legacy path) so this works on a fresh DB where alembic
       hasn't run yet.
    2. ``PentestTenantService.provision`` (and any future
       per-tenant provisioning) — passes a brand-new
       ``practice_<id>`` schema name. The schema doesn't exist yet, so
       we apply :file:`tenant_template.sql` to it and stamp at HEAD.
       The template captures **everything** alembic emits, including
       raw-SQL objects like ``has_patient_access`` that
       ``Base.metadata.create_all`` couldn't reproduce. The 2026-05-17
       pentest hit a fresh tenant that was stamped at HEAD without
       having ``has_patient_access`` installed — the template closes
       that gap.
    3. ``migrate_tenants.upgrade_tenant_schema`` for legacy tenants
       missing ``alembic_version`` — calls this function with a schema
       that *already* has tables. Applying the template would crash on
       ``CREATE TABLE``; fall through to the legacy path so the schema
       is reconciled to the ORM shape via ``create_all`` and then the
       caller's subsequent alembic stamp/upgrade carries column shape
       evolution from there.

    Idempotent for cases (1) and (3); fresh tenants in (2) error if
    the template is applied twice (intentional — re-applying canonical
    DDL is a schema upgrade, not a provisioning step).
    """
    _validate_schema_name(schema_name)

    is_default_template = schema_name == DEFAULT_PRACTICE_SCHEMA
    schema_already_populated = _schema_has_tables(engine, schema_name)

    if is_default_template or schema_already_populated:
        _create_practice_schema_legacy(engine, schema_name)
    else:
        _apply_tenant_template(engine, schema_name)
        _stamp_alembic_at_head(engine, schema_name)

    # RLS policies live outside the template — they're created by
    # ``enable_rls_on_schema`` in Python because the policy shape
    # depends on a per-table column introspection that's awkward to
    # express in raw SQL. Skipping for the default template preserves
    # prior behavior (alembic's job, not provisioning's). Idempotent
    # for tenant schemas via ``DROP POLICY IF EXISTS`` inside
    # ``enable_rls_on_schema``.
    if not is_default_template:
        from sqlalchemy.orm import Session as OrmSession

        from . import enable_rls_on_schema

        with OrmSession(engine) as session:
            # Pin search_path so the unqualified ``has_patient_access``
            # reference inside each policy resolves to **the new
            # tenant's own copy** (installed by the template),
            # independent of the connection-pool's prior state. This
            # makes provisioning deterministic; before, success
            # depended on a pooled connection happening to carry
            # ``search_path = practice, …`` from a prior request.
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
