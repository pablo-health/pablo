# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice schema provisioning — create and migrate practice schemas.

On first startup, creates the platform schema and a default practice schema.
For Pablo Practice edition, new practices get their own schemas on demand.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ..utcnow import utc_now
from . import (
    DEFAULT_PRACTICE_ID,
    DEFAULT_PRACTICE_OWN_SCHEMA,
    DEFAULT_PRACTICE_SCHEMA,
    PLATFORM_SCHEMA,
    _validate_schema_name,
)
from .platform_models import PlatformBase, PracticeRow

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

type PostProvisionHook = Callable[["Engine", str], None]

# Hooks invoked after a *fresh* tenant schema has been built from
# ``tenant_template.sql`` and stamped at the OSS alembic HEAD. A
# downstream deployment's overlay can register hooks here so every
# fresh-tenant code path (signup provisioning, pentest provisioning,
# …) gets its own per-tenant addendum applied atomically — without
# OSS importing from the overlay.
#
# Convention: hooks fire only on the fresh-template branch of
# :func:`create_practice_schema`. The legacy reconcile branch is used
# by ``migrate_tenants.upgrade_tenant_schema`` for pre-template tenants
# and skips hooks — an overlay's own addendum chain is applied to
# those tenants separately, by whatever deploy-time fan-out the
# overlay runs.
_post_provision_hooks: list[PostProvisionHook] = []


def register_post_provision_hook(hook: PostProvisionHook) -> None:
    """Register a callback to run after a fresh tenant schema is built.

    A downstream deployment's overlay registers this hook during
    application startup so that ``create_practice_schema`` callers
    (boot-time ``ensure_schemas``, ``PentestTenantService.provision``,
    future provisioning paths) automatically get the overlay's own
    per-tenant addendum applied without each call site re-implementing
    the wrapping.

    Idempotent: appending the same hook twice would invoke it twice;
    the overlay's startup code is responsible for guarding against
    re-registration on hot-reload.
    """
    _post_provision_hooks.append(hook)


def _run_post_provision_hooks(engine: Engine, schema_name: str) -> None:
    for hook in _post_provision_hooks:
        hook(engine, schema_name)


def reset_post_provision_hooks() -> None:
    """Clear all registered hooks. For tests; do not call from prod."""
    _post_provision_hooks.clear()


# backend/alembic.ini relative to backend/app/db/provisioning.py.
_ALEMBIC_INI_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


def _now() -> datetime:
    return utc_now()


# Arbitrary 64-bit key for the boot-time provisioning advisory lock. Any
# constant works — the value just needs to be stable so every booting
# instance picks the same lock. Generated once via random.randint to avoid
# colliding with locks the application may take elsewhere.
_PROVISIONING_LOCK_KEY = 7283194065831042197


def _has_any_practice(engine: Engine) -> bool:
    """Whether this deployment has registered a practice already.

    The question boot needs answered before it provisions a default one, and
    deliberately "any practice", not "the default practice". A deployment that
    provisions tenants explicitly may never register one called ``default`` —
    asking only about that id would conclude the database was empty and invent
    a schema alongside a hundred real ones.

    Returns False on a database that has no ``platform.practices`` yet, which
    is a first boot: the table is created moments earlier in the same
    function, so this is belt and braces rather than an expected path.
    """
    from sqlalchemy.orm import Session

    try:
        with Session(engine) as session:
            session.execute(text(f"SET search_path = {PLATFORM_SCHEMA}, public"))
            return session.query(PracticeRow.id).first() is not None
    except SQLAlchemyError:
        logger.warning(
            "Could not read the practice registry; treating this as a first boot",
            exc_info=True,
        )
        return False


def _provision_core_schemas(engine: Engine) -> None:
    """The provisioning template, and the deployment's own practice if it has none.

    The template holds nothing — it is the shape every practice schema is
    cloned from — so every deployment builds it.

    The deployment's own practice is conditional, and the condition is the
    point. It used to be that the template WAS the live practice: boot
    registered ``platform.practices`` against ``practice`` itself, and
    ``enable_rls_on_schema`` returns early on exactly that name, so the live
    database ran with no row policies at all. One practice is the ordinary
    case, not a special one, so it gets a real ``practice_*`` schema with the
    same policies as any other — which is also what lets a patient principal
    authenticate, since its fence requires that prefix
    (``app.auth.patient_context._is_tenant_schema``).

    Doing that UNCONDITIONALLY creates an orphan on any deployment that
    provisions its own tenants. The extra schema is in the database and in
    nobody's registry, so every per-tenant migration — they all iterate
    ``platform.practices`` — skips it forever. It drifts quietly until
    something notices, and what notices is this same boot path: the next
    ``create_practice_schema`` re-runs the RLS guard over the accumulated
    staleness and refuses to start.

    That is not hypothetical. A retired table (``booking_policy``) was dropped
    from every REGISTERED tenant, survived in one unregistered schema, and took
    a deployment down at boot on 2026-09-09. The guard was right; the schema
    should never have existed.

    Asking the registry rather than reading a flag keeps it one rule for
    everyone: a first boot on an empty database gets its practice with no
    operator step, and a database that already has practices is left alone.
    """
    create_practice_schema(engine, DEFAULT_PRACTICE_SCHEMA)

    if _has_any_practice(engine):
        logger.debug(
            "Practices already registered; not provisioning '%s'",
            DEFAULT_PRACTICE_OWN_SCHEMA,
        )
        return
    create_practice_schema(engine, DEFAULT_PRACTICE_OWN_SCHEMA)


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
    * **Platform column evolution** — owned by whatever deployment-level
      alembic chain a downstream overlay chooses to run against the
      platform schema (its own ``alembic_version`` bookkeeping).
      Historically lived in a runtime patch
      (``_migrate_platform_columns``) that ran on every boot; that
      patch was absorbed into a deployment's own migration chain and
      removed from this file. OSS itself has no platform alembic
      chain, so a self-hosted install with no overlay migration chain
      relies on ``create_all`` matching the ORM shape.
    * **Tenant column evolution** — owned by the OSS tenant chain
      (``backend/alembic/``) and fanned out per-tenant by the
      deployment's own migration entrypoint /
      ``app.db.migrate_tenants.fan_out``.

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

            # Platform-schema column evolution lives in a downstream
            # deployment's own alembic chain, fanned out at deploy time by
            # that deployment's migration entrypoint. The boot path used to
            # run a runtime ``_migrate_platform_columns`` helper that
            # issued ~17 ALTER TABLE statements with bare-except savepoints;
            # that logic was absorbed into a deployment migration chain and
            # deleted here.

            # Pentest CHECK + immutability trigger on ``platform.practices``.
            # Declarative DB guards, not column evolution — kept here until
            # they can move into a deployment's own alembic chain alongside
            # the ``is_pentest`` column itself.
            _ensure_pentest_tenant_guards(engine)

            _provision_core_schemas(engine)

            # Per-tenant schema evolution belongs in the alembic chain
            # (``backend/alembic/versions/``), fanned out at deploy time
            # via the deployment's own migration entrypoint /
            # ``app.db.migrate_tenants``. The boot path historically
            # iterated ``practice_*`` schemas and ran a runtime
            # column-patch helper; that left freshly-
            # provisioned tenants broken until the next backend revision
            # restarted, and silently swallowed failures via savepoints.
            # Removed in b7de65c29385 — see that revision's docstring.

            # Ensure default practice exists in registry
            from sqlalchemy.orm import Session

            with Session(engine) as session:
                session.execute(text(f"SET search_path = {PLATFORM_SCHEMA}, public"))
                existing = session.get(PracticeRow, DEFAULT_PRACTICE_ID)
                # Only register the default practice when this boot actually
                # provisioned its schema. Writing the row on a deployment that
                # has other practices would point the registry at a schema
                # nothing created — the mirror image of the orphan above, and
                # worse, because a registered-but-absent practice fails at the
                # first request rather than at boot.
                if existing is None:
                    if not _has_any_practice(engine):
                        session.add(
                            PracticeRow(
                                id=DEFAULT_PRACTICE_ID,
                                name="Default Practice",
                                schema_name=DEFAULT_PRACTICE_OWN_SCHEMA,
                                owner_email="",
                                product="pablo",
                                created_at=_now(),
                            )
                        )
                        session.commit()
                        logger.info("Created default practice in registry")
                elif existing.schema_name == DEFAULT_PRACTICE_SCHEMA:
                    # An install from before the template and the live practice
                    # were separated. Its charts are in the template schema, so
                    # re-pointing the row here would silently orphan every one
                    # of them — moving the data is a migration with a pre-flight
                    # (``app.db.single_practice_migration``), not a line in a
                    # boot path.
                    #
                    # This used to warn and carry on. Carrying on means serving
                    # a database whose chart schema has no row policies at all,
                    # and a warning in a startup log is not a control: nobody is
                    # reading it, and the deployment looks healthy. Refusing is
                    # the honest state — the operator gets one actionable line
                    # instead of an install that works until someone notices it
                    # never had isolation.
                    raise RuntimeError(
                        f"Practice '{DEFAULT_PRACTICE_ID}' is still registered against the "
                        f"template schema '{DEFAULT_PRACTICE_SCHEMA}', which carries no row "
                        f"policies. Refusing to start: serving from it would run this "
                        f"deployment without row-level security over live charts.\n\n"
                        f"    python backend/bin/migrate_default_practice.py --check\n"
                        f"    python backend/bin/migrate_default_practice.py\n\n"
                        f"The first reports what would happen and changes nothing; the second "
                        f"moves the data onto '{DEFAULT_PRACTICE_OWN_SCHEMA}' and applies the "
                        f"policies."
                    )
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
    ``tenant_template.sql`` (the canonical DDL at alembic-HEAD) and is
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

    ``tenant_template.sql`` is the single source of truth for tenant DDL.
    Behavior is picked by whether the target schema already has tables:

    1. **Empty schema → apply tenant_template.sql + stamp HEAD.** Same
       path for the default ``practice`` template AND for any new
       per-tenant schema (``PentestTenantService.provision`` or
       future per-customer provisioning). The template is the canonical
       DDL at alembic-HEAD, so every schema starts byte-identical.

    2. **Already-populated schema.** If it carries an ``alembic_version``
       row it was provisioned from the template and the alembic chain
       owns it from here — no-op. If it has tables but *no*
       ``alembic_version`` (a pre-template tenant), raise: the old
       ``Base.metadata.create_all`` reconcile was the drift vector behind
       the 2026-05-21 prod incident (``chat_messages`` in prod's
       ``practice`` lost every CHECK constraint), so such a schema must be
       re-provisioned from the template or reconciled by hand, never
       silently rebuilt from the ORM.

    RLS policies still live outside the template (column introspection
    in Python). Skipped for the default template — single-tenancy OSS
    deployments use ``practice`` as their runtime schema without the
    multi-tenant middleware that sets ``app.current_user_id``, and
    RLS-without-user-id fails closed (zero rows). Per-tenant schemas
    always get RLS.
    """
    _validate_schema_name(schema_name)

    # Serialize concurrent provisioning of the same schema (THERAPY-da7t).
    # A session-scoped advisory lock on ``hashtext(schema_name)`` blocks
    # any second caller for this exact schema until the first commits its
    # template + RLS DDL. Different schemas don't contend (they hash to
    # different keys), so unrelated tenant provisioning runs in parallel.
    # Belt for the failure mode where a retried background task races
    # itself, a double-clicked signup fires two requests, or any other
    # concurrent caller appears -- the second caller sees a populated
    # schema and takes the idempotent legacy-reconcile path.
    with engine.connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(hashtext(:s))"), {"s": schema_name})
        try:
            _create_practice_schema_locked(engine, schema_name)
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext(:s))"), {"s": schema_name})
            lock_conn.commit()


def _create_practice_schema_locked(engine: Engine, schema_name: str) -> None:
    """Body of :func:`create_practice_schema` once the advisory lock is held."""
    is_default_template = schema_name == DEFAULT_PRACTICE_SCHEMA

    if _schema_has_tables(engine, schema_name):
        # Already provisioned. ``tenant_template.sql`` is the only way a
        # tenant schema is built, and that path stamps ``alembic_version``,
        # so a populated, stamped schema is at a known revision and the
        # alembic chain carries it forward — nothing to do here. A populated
        # schema *without* an ``alembic_version`` row is a pre-template
        # tenant; the old ``Base.metadata.create_all`` reconcile (the drift
        # vector behind the 2026-05-21 prod incident — ``chat_messages`` in
        # prod's ``practice`` lost every CHECK constraint) is gone, so
        # surface it loudly rather than silently mutating it from the ORM.
        if not _has_alembic_version(engine, schema_name):
            raise RuntimeError(
                f"Schema '{schema_name}' has tables but no alembic_version row "
                "(pre-template tenant). The create_all reconcile path has been "
                "removed — re-provision the schema from tenant_template.sql, or "
                "stamp it at the matching revision and migrate it manually."
            )
        logger.info("Practice schema '%s' already provisioned; nothing to do", schema_name)
    else:
        _apply_tenant_template(engine, schema_name)
        _stamp_alembic_at_head(engine, schema_name)
        # Hooks fire only on the fresh-template path. A downstream
        # deployment's overlay hook lays down its own per-tenant
        # addendum + stamps its own tenant-chain bookkeeping so the
        # schema is at HEAD on both chains before any caller starts
        # using it.
        _run_post_provision_hooks(engine, schema_name)

    if not is_default_template:
        from sqlalchemy.orm import Session as OrmSession

        from . import enable_rls_on_schema

        with OrmSession(engine) as session:
            session.execute(text(f"SET search_path = {schema_name}, {PLATFORM_SCHEMA}, public"))
            enable_rls_on_schema(session, schema_name)

    logger.info("Practice schema '%s' ready", schema_name)


def _has_alembic_version(engine: Engine, schema_name: str) -> bool:
    """Return True if ``schema_name`` carries an ``alembic_version`` table.

    A schema built from ``tenant_template.sql`` is stamped at HEAD, so the
    presence of this table distinguishes a properly-provisioned tenant from
    a pre-template one. (Local copy rather than importing from
    ``migrate_tenants`` to avoid the import cycle — that module imports
    :func:`create_practice_schema` from here.)
    """
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = 'alembic_version'"
            ),
            {"s": schema_name},
        ).scalar()
    return bool(count and count > 0)


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
