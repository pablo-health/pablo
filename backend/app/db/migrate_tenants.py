# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Per-tenant alembic fan-out — migrate every tenant schema to head.

Iterates ``platform.practices.schema_name`` for active practices and runs
``alembic upgrade head`` against each schema with
``version_table_schema=<schema>``. The ``practice`` template is owned by the
deploy-time ``alembic upgrade head`` job and is skipped here.

Tenants without an ``alembic_version`` row (provisioned before pa-5in.2
landed) are auto-stamped at HEAD with a clear log line. Schemas built from
``Base.metadata.create_all`` are by definition at HEAD, so stamping is safe
and avoids forcing manual operator intervention.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from . import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA, _validate_schema_name
from .provisioning import _ALEMBIC_INI_PATH

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


class TenantStatus(StrEnum):
    SUCCESS = "success"
    ALREADY_AT_HEAD = "already-at-head"
    STAMPED = "stamped"
    FAILED = "failed"


@dataclass(frozen=True)
class TenantResult:
    schema: str
    status: TenantStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is not TenantStatus.FAILED


class _AlembicRunner(Protocol):
    """Callable that upgrades a single tenant schema to head.

    Extracted so tests can substitute a fake without spinning up alembic.
    """

    def __call__(self, engine: Engine, schema: str) -> TenantResult: ...


def list_active_tenant_schemas(engine: Engine) -> list[str]:
    """Return schema names for active practices, excluding the template."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT schema_name FROM {PLATFORM_SCHEMA}.practices"  # noqa: S608
                " WHERE is_active = TRUE"
                " ORDER BY schema_name"
            )
        ).fetchall()
    return [row[0] for row in rows if row[0] != DEFAULT_PRACTICE_SCHEMA]


def _alembic_head() -> str | None:
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI_PATH)))
    return script.get_current_head()


def _has_alembic_version(engine: Engine, schema: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables"
                " WHERE table_schema = :s AND table_name = 'alembic_version'"
            ),
            {"s": schema},
        ).fetchone()
    return row is not None


def _stamp_at_head(engine: Engine, schema: str, head: str) -> None:
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI_PATH)))
    with engine.begin() as conn:
        ctx = MigrationContext.configure(
            connection=conn,
            opts={"version_table_schema": schema, "version_table": "alembic_version"},
        )
        ctx.stamp(script, head)


def _current_revision(engine: Engine, schema: str) -> str | None:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            connection=conn,
            opts={"version_table_schema": schema, "version_table": "alembic_version"},
        )
        return ctx.get_current_revision()


def _alembic_config_for(schema: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI_PATH))
    # env.py reads version_table_schema via attributes when present;
    # we set it on the X argument so an extension can pick it up. The
    # actual switch happens via MigrationContext below. Keep a single
    # config object to satisfy alembic.command.upgrade's signature.
    cfg.attributes["target_schema"] = schema
    return cfg


def upgrade_tenant_schema(engine: Engine, schema: str) -> TenantResult:
    """Run ``alembic upgrade head`` against a single tenant schema.

    Auto-stamps tenants missing ``alembic_version`` (legacy tenants
    provisioned before pa-5in.2). Returns a structured result rather than
    raising so a single bad tenant doesn't abort the fan-out.
    """
    _validate_schema_name(schema)
    head = _alembic_head()
    if head is None:
        return TenantResult(schema, TenantStatus.FAILED, "alembic has no head revision")

    try:
        if not _has_alembic_version(engine, schema):
            logger.info(
                "tenant %s missing alembic_version — auto-stamping at head %s",
                schema,
                head,
            )
            _stamp_at_head(engine, schema, head)
            return TenantResult(schema, TenantStatus.STAMPED, head)

        current = _current_revision(engine, schema)
        if current == head:
            return TenantResult(schema, TenantStatus.ALREADY_AT_HEAD, head)

        with engine.begin() as conn:
            conn.execute(
                text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public")
            )
            cfg = _alembic_config_for(schema)
            cfg.attributes["connection"] = conn
            cfg.attributes["version_table_schema"] = schema
            command.upgrade(cfg, "head")

        return TenantResult(schema, TenantStatus.SUCCESS, f"{current} → {head}")
    except Exception as exc:
        logger.exception("tenant %s migration failed", schema)
        return TenantResult(schema, TenantStatus.FAILED, str(exc))


def fan_out(
    engine: Engine,
    schemas: list[str],
    runner: _AlembicRunner = upgrade_tenant_schema,
) -> list[TenantResult]:
    """Apply ``runner`` to each schema, continuing past failures."""
    results: list[TenantResult] = []
    for schema in schemas:
        result = runner(engine, schema)
        results.append(result)
        logger.info("tenant=%s status=%s %s", result.schema, result.status.value, result.detail)
    return results


def aggregate_exit_code(results: list[TenantResult]) -> int:
    """Exit 0 if every tenant succeeded (or was already at head); 1 otherwise."""
    return 0 if all(r.ok for r in results) else 1


def summarize(results: list[TenantResult]) -> str:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    parts = [f"{k}={v}" for k, v in sorted(counts.items())]
    failed = [r.schema for r in results if r.status is TenantStatus.FAILED]
    line = f"tenants={len(results)} " + " ".join(parts)
    if failed:
        line += " failed_schemas=" + ",".join(failed)
    return line
