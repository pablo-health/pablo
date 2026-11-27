# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``d3a7c5e91f24`` widens ``session_type`` to 100 and narrows it back cleanly."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db import PLATFORM_SCHEMA
from app.db.migrate_tenants import _alembic_config_for
from app.db.provisioning import _ALEMBIC_INI_PATH, create_practice_schema, ensure_schemas
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_PARENT = "8668091951bb"
_UNDER_TEST = "d3a7c5e91f24"
_TABLES = ("therapy_sessions", "appointments", "waitlist_entries")


@pytest.fixture
def schema() -> Iterator[tuple[Engine, str]]:
    engine = create_engine(_db_url, pool_pre_ping=True)
    ensure_schemas(engine)
    name = f"practice_test_session_type_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, name)
    yield engine, name
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {name} CASCADE"))


def _migrate(engine: Engine, schema: str, step: Callable[[Config, str], None], target: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        step(cfg, target)


def _widths(engine: Engine, schema: str) -> dict[str, int]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, character_maximum_length FROM information_schema.columns"
                " WHERE table_schema = :s AND column_name = 'session_type'"
            ),
            {"s": schema},
        ).all()
    return {row[0]: row[1] for row in rows}


def test_parent_revision_is_still_the_one_this_test_assumes() -> None:
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI_PATH)))

    assert script.get_revision(_UNDER_TEST).down_revision == _PARENT


def test_the_revision_round_trips(schema: tuple[Engine, str]) -> None:
    engine, name = schema
    assert _widths(engine, name) == dict.fromkeys(_TABLES, 100)

    _migrate(engine, name, command.downgrade, _PARENT)
    assert _widths(engine, name) == dict.fromkeys(_TABLES, 30)

    _migrate(engine, name, command.upgrade, _UNDER_TEST)
    assert _widths(engine, name) == dict.fromkeys(_TABLES, 100)
