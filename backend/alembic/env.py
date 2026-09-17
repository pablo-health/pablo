"""Alembic environment configuration for schema-per-practice migrations.

Migrations run against the practice schema template. When deploying,
a provisioning step applies migrations to all existing practice schemas.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.orm import Session

# Add backend to sys.path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA
from app.db.models import Base
from app.db.platform_bootstrap import require_platform_schema
from app.diagnostics.seed import seed_diagnostic_reference_data
from app.settings import get_settings

config = context.config

if config.config_file_name is not None:
    # ``disable_existing_loggers`` defaults to True, which switches off every
    # logger that already exists when alembic configures logging — including
    # the ones belonging to whatever invoked it.
    #
    # That is not theoretical. ``bin/migrate.py`` creates its module logger at
    # import, runs ``alembic upgrade head``, and then runs the single-practice
    # migration and reports what it found. With the default, everything that
    # second half logs — the pre-flight report, and the explanation when it
    # REFUSES and fails the deploy — went nowhere. On 2026-09-09 the OSS deploy
    # job exited 1 after a clean upgrade with no message of any kind: twenty
    # lines of alembic INFO, then nothing. The one code path whose whole job is
    # to say why it is blocking a rollout was the one that could not speak.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
#: Deliberately absent: a ``platform_metadata`` alias for ``PlatformBase.metadata``.
#: This chain no longer builds the platform schema from ORM metadata — see the
#: bootstrap block in ``run_migrations_online``.

settings = get_settings()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL without a live connection."""
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database.

    Two modes:

    * Default (deploy-time): bootstrap the platform + ``practice`` template
      schemas, then run migrations with ``version_table_schema=practice``.
    * Per-tenant fan-out (pa-5in.1): caller passes a live ``connection`` and
      ``target_schema`` via ``config.attributes``. env.py skips the bootstrap
      and runs migrations against the supplied connection with the tenant's
      version table.
    """
    injected_connection = config.attributes.get("connection")
    target_schema = config.attributes.get("target_schema") or DEFAULT_PRACTICE_SCHEMA

    if injected_connection is not None:
        # Per-tenant fan-out path. The caller owns the connection and the
        # transaction; alembic must not bootstrap platform tables here
        # (they already exist) and must use the tenant's alembic_version.
        context.configure(
            connection=injected_connection,
            target_metadata=target_metadata,
            version_table_schema=target_schema,
        )
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

    # Bootstrap schemas in their own committed transaction, then open a fresh
    # connection for alembic. Mixing manual connection.commit() with alembic's
    # context.begin_transaction() on the same connection causes the final
    # run_migrations()/stamp write to be rolled back under SQLAlchemy 2.x.
    with connectable.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DEFAULT_PRACTICE_SCHEMA}"))

    # This chain depends on the platform chain having run, and always has:
    # nothing in backend/alembic/versions/ creates ``platform.users``, but
    # several revisions declare foreign keys into it. ``create_all`` used to
    # satisfy that dependency here as a side effect, which is how the platform
    # schema came to be built from ORM metadata — tables, columns and indexes,
    # and none of the policies, triggers or constraints that were supposed to
    # come with them.
    #
    # The dependency is now stated rather than met in passing. It is deliberately
    # NOT met by running the platform chain from here: alembic's ``context`` and
    # ``op`` are module-level proxies, so a nested ``command.upgrade`` tears down
    # the outer environment's globals on exit and the whole run dies with
    # ``KeyError: 'config'``. Every caller therefore runs the platform chain
    # first — ``bin/migrate.py``, the template regen, the Makefile, the tests —
    # and this check is what makes forgetting it say so.
    require_platform_schema(connectable)

    # Seed bundled diagnostic reference data into the platform tables
    # (idempotent). Runs only on the deploy-time bootstrap path, not the
    # per-tenant fan-out (which returns early above), and only once the
    # platform chain has built the tables it writes to.
    with connectable.begin() as connection, Session(bind=connection) as seed_session:
        seed_diagnostic_reference_data(seed_session)
        seed_session.flush()

    with connectable.connect() as connection:
        connection.execute(text(f"SET search_path = {target_schema}, {PLATFORM_SCHEMA}, public"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=target_schema,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
