"""Alembic environment configuration for schema-per-practice migrations.

Migrations run against the practice schema template. When deploying,
a provisioning step applies migrations to all existing practice schemas.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text

# Add backend to sys.path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA
from app.db.models import Base
from app.db.platform_models import PlatformBase
from app.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
platform_metadata = PlatformBase.metadata

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

    # Bootstrap schemas + platform tables in their own committed transaction,
    # then open a fresh connection for alembic. Mixing manual connection.commit()
    # with alembic's context.begin_transaction() on the same connection causes
    # the final run_migrations()/stamp write to be rolled back under SQLAlchemy 2.x.
    with connectable.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DEFAULT_PRACTICE_SCHEMA}"))
        platform_metadata.create_all(connection)

    with connectable.connect() as connection:
        connection.execute(
            text(f"SET search_path = {target_schema}, {PLATFORM_SCHEMA}, public")
        )
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
