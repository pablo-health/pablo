# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice schema provisioning — create and migrate practice schemas.

On first startup, creates the platform schema and a default practice schema.
For Pablo Practice edition, new practices get their own schemas on demand.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

from ..utcnow import utc_now_iso
from . import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA, _validate_schema_name
from .models import Base
from .platform_models import PlatformBase, PracticeRow

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _now() -> str:
    return utc_now_iso()


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
        f"ALTER TABLE {practices} ADD COLUMN IF NOT EXISTS"
        " tenant_id VARCHAR(128) UNIQUE",
        f"ALTER TABLE {practices} ADD COLUMN IF NOT EXISTS"
        " owner_email VARCHAR(255) NOT NULL DEFAULT ''",
        f"ALTER TABLE {practices} ADD COLUMN IF NOT EXISTS"
        " status VARCHAR(20) DEFAULT 'active'",
        # subscriptions: trial tracking
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS"
        " trial_start VARCHAR(50)",
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS"
        " trial_sessions_used INTEGER DEFAULT 0",
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS"
        " trial_sessions_limit INTEGER DEFAULT 15",
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS"
        " trial_days_limit INTEGER DEFAULT 0",
        # subscriptions: grace extension
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS"
        " grace_extension_used BOOLEAN DEFAULT FALSE",
        f"ALTER TABLE {subs} ADD COLUMN IF NOT EXISTS"
        " grace_extension_expires_at VARCHAR(50)",
        # Widen all timestamp columns from VARCHAR(30) to 50
        f"ALTER TABLE {practices}"
        " ALTER COLUMN created_at TYPE VARCHAR(50)",
        f"ALTER TABLE {subs}"
        " ALTER COLUMN created_at TYPE VARCHAR(50)",
        f"ALTER TABLE {subs}"
        " ALTER COLUMN updated_at TYPE VARCHAR(50)",
        f"ALTER TABLE {PLATFORM_SCHEMA}.email_tenant_mappings"
        " ALTER COLUMN created_at TYPE VARCHAR(50)",
    ]
    with engine.connect() as conn:
        for stmt in migrations:
            conn.execute(text(stmt))
        conn.commit()
    logger.info("Platform column migrations applied")


def create_practice_schema(engine: Engine, schema_name: str) -> None:
    """Create a new practice schema with all practice tables.

    Idempotent — can be called on existing schemas.
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

    logger.info("Practice schema '%s' ready", schema_name)
