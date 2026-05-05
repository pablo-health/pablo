# Copyright (c) 2026 Pablo Health, LLC. All rights reserved under AGPL-3.0.

"""Audit-log retention purge cron (THERAPY-agx).

Implements the deletion side of the audit-log TTL contract referenced by
:class:`~app.repositories.postgres.audit.PostgresAuditRepository` — rows
in ``audit_logs`` whose ``expires_at`` has passed are deleted by this job.
Retention is governed by
:data:`app.models.audit.AUDIT_LOG_RETENTION_DAYS` (HIPAA 7y).

The job fans across every active tenant schema returned by
:func:`~app.db.migrate_tenants.list_active_tenant_schemas` and issues a
single ``DELETE FROM <schema>.audit_logs WHERE expires_at < :as_of`` per
schema. Rows-purged is logged per schema; PHI never appears in logs.

Invoked from repo ``backend/``::

    python -m app.jobs.audit_retention_cron --dry-run
    python -m app.jobs.audit_retention_cron --as-of 2026-01-01T00:00:00Z

Cloud Run Job wiring (hosted) lives in pablo-saas
``.github/workflows/deploy.yml``; this module is OSS and self-hostable.

Exit codes:
    * 0 — success (including dry-run)
    * 1 — DB or other unexpected error
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

from ..db import PLATFORM_SCHEMA, _validate_schema_name, get_engine
from ..db.migrate_tenants import list_active_tenant_schemas

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _parse_as_of(raw: str | None) -> datetime:
    """Parse the ``--as-of`` CLI argument into an aware UTC datetime.

    Defaults to ``datetime.now(UTC)`` when ``raw`` is None.
    """
    if raw is None:
        return datetime.now(UTC)
    normalized = raw.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_of_timestamp(as_of: datetime) -> str:
    return as_of.isoformat().replace("+00:00", "Z")


def _count_expired(engine: Engine, schema: str, as_of: datetime) -> int:
    """Count audit_logs rows that *would* be deleted at ``as_of``."""
    _validate_schema_name(schema)
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        result = conn.execute(
            text("SELECT COUNT(*) FROM audit_logs WHERE expires_at < :as_of"),
            {"as_of": as_of},
        ).scalar_one()
    return int(result or 0)


def _delete_expired(engine: Engine, schema: str, as_of: datetime) -> int:
    """Delete expired audit_logs rows in ``schema``; returns rows deleted."""
    _validate_schema_name(schema)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        result = conn.execute(
            text("DELETE FROM audit_logs WHERE expires_at < :as_of"),
            {"as_of": as_of},
        )
    return int(result.rowcount or 0)


def run(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parse_argv(argv)

    as_of = _parse_as_of(args.as_of_raw)
    dry_run_marker = " dry_run=true" if args.dry_run else ""
    logger.info(
        "audit_retention_start as_of=%s%s",
        _as_of_timestamp(as_of),
        dry_run_marker,
    )

    try:
        engine = get_engine()
        schemas = list_active_tenant_schemas(engine)
    except Exception:
        logger.exception("audit_retention_bootstrap_failed")
        return 1

    total_purged = 0
    for schema in schemas:
        try:
            if args.dry_run:
                rows = _count_expired(engine, schema, as_of)
            else:
                rows = _delete_expired(engine, schema, as_of)
        except Exception:
            logger.exception("audit_retention_schema_failed schema=%s", schema)
            return 1
        total_purged += rows
        # No PHI: schema name + count only.
        logger.info(
            "audit_retention_schema_done schema=%s rows_purged=%s dry_run=%s",
            schema,
            rows,
            args.dry_run,
        )

    logger.info(
        "audit_retention_done schemas=%s rows_purged_total=%s dry_run=%s",
        len(schemas),
        total_purged,
        args.dry_run,
    )
    return 0


def _parse_argv(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        dest="as_of_raw",
        default=None,
        help=(
            "ISO-8601 cutoff (UTC). Rows whose expires_at is strictly before "
            "this instant are deleted. Default: NOW()."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count eligible rows without modifying the database.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
