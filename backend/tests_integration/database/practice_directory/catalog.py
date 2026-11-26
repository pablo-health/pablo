# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Directory catalog inspection and revision helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import command
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

from .helpers import (
    _ROLE,
)


def _config(engine: Engine, schema: str) -> list[str] | None:
    """The function's pinned settings, as Postgres stores them."""
    with engine.connect() as conn:
        config: list[str] | None = conn.execute(
            text("SELECT proconfig FROM pg_proc WHERE oid = to_regprocedure(:fn)"),
            {"fn": f"{schema}.practice_client_directory()"},
        ).scalar()
    return config


def _walk(engine: Engine, schema: str, direction: str, target: str) -> None:
    """Move one tenant schema along its chain, as the migrate fan-out does."""
    from app.db.migrate_tenants import _alembic_config_for

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        if direction == "upgrade":
            command.upgrade(cfg, target)
        else:
            command.downgrade(cfg, target)


def _owner(engine: Engine, schema: str) -> str | None:
    with engine.connect() as conn:
        owner: str | None = conn.execute(
            text("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid = to_regprocedure(:fn)"),
            {"fn": f"{schema}.practice_client_directory()"},
        ).scalar()
    return owner


def _directory_policies(engine: Engine, schema: str) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    "SELECT tablename FROM pg_policies "
                    "WHERE schemaname = :s AND policyname = 'rls_practice_directory_read'"
                ),
                {"s": schema},
            ).scalars()
        )


def _directory_access(engine: Engine, schema: str) -> dict[str, object]:
    """Everything the directory's access consists of, read from the catalog."""
    with engine.connect() as conn:
        functions = conn.execute(
            text(
                "SELECT p.proname, pg_get_userbyid(p.proowner), p.proconfig FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = :s AND p.proname LIKE 'practice\\_%' ORDER BY 1"
            ),
            {"s": schema},
        ).all()
        columns = conn.execute(
            text(
                "SELECT table_name, column_name, privilege_type "
                "FROM information_schema.column_privileges "
                "WHERE table_schema = :s AND grantee = :r ORDER BY 1, 2, 3"
            ),
            {"s": schema, "r": _ROLE},
        ).all()
        usage, create = conn.execute(
            text(
                "SELECT has_schema_privilege(:r, :s, 'USAGE'), "
                "has_schema_privilege(:r, :s, 'CREATE')"
            ),
            {"s": schema, "r": _ROLE},
        ).one()
        policies = conn.execute(
            text(
                "SELECT tablename, cmd, roles, qual FROM pg_policies "
                "WHERE schemaname = :s AND policyname = 'rls_practice_directory_read' ORDER BY 1"
            ),
            {"s": schema},
        ).all()
    return {
        "functions": [tuple(row) for row in functions],
        "columns": [tuple(row) for row in columns],
        "schema": (usage, create),
        "policies": [tuple(row) for row in policies],
    }
