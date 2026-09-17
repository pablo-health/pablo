# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Alembic environment for the ``platform`` schema.

Distinct from the tenant chain in ``backend/alembic/``. Stamps land in
``platform.alembic_version_platform``, so the two chains extend independently
without sharing a graph or a version row.

**Why this chain exists.** The platform schema used to have no chain at all: it
was built by ``PlatformBase.metadata.create_all``, which meant the ORM models
were its only source of truth. ``create_all`` emits tables, columns and indexes
and nothing else — a row policy, a trigger, a CHECK constraint, a grant and a
foreign key are all invisible to it. So every platform object needing one of
those had to exist twice, once in a hand-written patch bolted onto the boot path
and once nowhere at all, and a column whose type changed on a table that already
existed simply never changed. See ``PABLO-k7it``.

Scope:

* Operates on the ``platform`` schema only. Tenant schemas belong to the tenant
  chain and its per-tenant fan-out; no revision here should touch ``practice``
  or any ``practice_*`` schema.
* Creates the schema itself if it is absent, because a chain that cannot run on
  an empty database is not a bootstrap. It creates no tables outside its own
  revisions.

Autogenerate is restricted to this one schema by :func:`_include_name`. Without
that restriction, reflecting with ``include_schemas=True`` would compare tenant
tables against a metadata that deliberately does not describe them, and
autogenerate would propose dropping every one of them.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text

# Add backend to sys.path so we can import app modules. Mirrors the tenant
# chain's env.py — alembic's ``prepend_sys_path`` resolves against the working
# directory, which is not reliably ``backend/`` for every caller.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import PLATFORM_SCHEMA
from app.db.platform_models import PlatformBase
from app.settings import get_settings

config = context.config

if config.config_file_name is not None:
    # ``disable_existing_loggers`` defaults to True, which switches off every
    # logger that already exists when alembic configures logging — including
    # the caller's. The tenant chain's env.py carries the same override and the
    # same reason: on 2026-09-09 an OSS deploy exited 1 after a clean upgrade
    # with no message of any kind, because the code path whose whole job is to
    # say why it is blocking a rollout had had its logger switched off.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = PlatformBase.metadata

settings = get_settings()

#: Bookkeeping table for this chain, in the platform schema. Deliberately not
#: ``alembic_version``: an overlay's platform chain keeps
#: ``alembic_version_saas`` in the same schema, and the tenant chain's plain
#: ``alembic_version`` lives in each practice schema. Three chains, three names,
#: no collisions.
VERSION_TABLE = "alembic_version_platform"


def _include_name(
    name: str | None,
    type_: str,
    parent_names: dict[str, str | None],  # noqa: ARG001 — alembic's callback signature
) -> bool:
    """Restrict reflection and autogenerate to the platform schema.

    ``include_schemas=True`` is required for alembic to see schema-qualified
    tables at all, but it also makes reflection sweep up every other schema in
    the database — every ``practice_*`` tenant, and the tenant template. Those
    tables are described by the *tenant* chain's metadata, not this one, so
    autogenerate would read them as tables that exist in the database and not in
    the model, and propose a migration that drops all of them.
    """
    if type_ == "schema":
        return name == PLATFORM_SCHEMA
    return True


def _configure(**kwargs: object) -> None:
    """Shared ``context.configure`` arguments for both modes."""
    context.configure(
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        version_table_schema=PLATFORM_SCHEMA,
        include_schemas=True,
        include_name=_include_name,
        # Type changes are the drift this chain exists to catch: ``create_all``
        # never altered a column on a table that already existed, so several
        # platform columns differ between a fresh database and one that has been
        # running since before the column's type changed.
        compare_type=True,
        compare_server_default=True,
        **kwargs,  # type: ignore[arg-type]
    )


def run_migrations_offline() -> None:
    """Emit SQL without a live connection (``--sql``)."""
    _configure(
        url=settings.database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database.

    Two modes, matching the tenant chain's shape:

    * Default: open an engine from settings, ensure the platform schema exists,
      then migrate.
    * Injected connection: a caller (tests, or an orchestrating migrate
      entrypoint) passes a live ``connection`` via ``config.attributes`` and owns
      the transaction. No schema bootstrap — the caller has already done it.
    """
    injected_connection = config.attributes.get("connection")

    if injected_connection is not None:
        _configure(connection=injected_connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.database_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # The schema, in its own committed transaction. Mixing a manual commit with
    # alembic's ``context.begin_transaction()`` on one connection gets the final
    # stamp write rolled back under SQLAlchemy 2.x — the tenant chain's env.py
    # carries the same note.
    with connectable.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))

    with connectable.connect() as connection:
        # Unqualified references inside function bodies, defaults and check
        # constraints resolve against this, not against whatever schema the
        # pool's connection last touched.
        connection.execute(text(f"SET search_path = {PLATFORM_SCHEMA}, public"))
        connection.commit()

        _configure(connection=connection)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
