# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Daily refresh of payer enrollment status from each practice's clearinghouse.

An enrollment request moves on the payer's schedule — days to weeks — and
the practice hears about it only by asking. This job asks once a day: for
every active practice with a clearinghouse configured, it lists the
account's enrollments and records what changed on ``payer_enrollments``
(``app.claims.enrollment.refresh_enrollments``), which also mirrors the
payer's overall status and raises the ``enrollment_action_required`` event
when the payer wants something from the practice.

Bounded twice: ``--max-tenants`` caps how many practices one run visits and
``--max-per-tenant`` caps how many open requests it looks at in each. Each
practice is one tenant-scoped session; a vendor error in one practice is
logged and the run moves on to the next.

Invoked from repo ``backend/``::

    python -m app.jobs.payer_enrollment_refresh
    python -m app.jobs.payer_enrollment_refresh --max-tenants 50 --dry-run

Exit codes:
    * 0 — success (including dry-run)
    * 1 — unexpected error
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from ..claims.clearinghouse import ClearinghouseError
from ..claims.enrollment import (
    MAX_REFRESH_PER_TENANT,
    OPEN_REQUEST_STATUSES,
    get_clearinghouse_client,
    refresh_enrollments,
)
from ..db import _validate_schema_name, create_standalone_session, get_engine
from ..db.migrate_tenants import list_active_practice_registry
from ..db.models import PayerEnrollmentRow

if TYPE_CHECKING:
    from ..claims.clearinghouse import ClearinghouseClient

logger = logging.getLogger(__name__)

DEFAULT_MAX_TENANTS = 500


def _count_open(schema: str) -> int:
    session = create_standalone_session(schema)
    try:
        return int(
            session.execute(
                select(func.count())
                .select_from(PayerEnrollmentRow)
                .where(PayerEnrollmentRow.status.in_(OPEN_REQUEST_STATUSES))
            ).scalar_one()
        )
    finally:
        session.close()


def refresh_tenant(schema: str, client: ClearinghouseClient, *, limit: int) -> int:
    """Refresh one practice's open requests in its own session; returns how many changed."""
    _validate_schema_name(schema)
    session = create_standalone_session(schema)
    try:
        changed = refresh_enrollments(session, client, limit=limit)
        session.commit()
        return changed
    except ClearinghouseError as exc:
        session.rollback()
        logger.warning(
            "payer_enrollment_refresh_tenant_failed schema=%s error=%s",
            schema,
            type(exc).__name__,
        )
        return 0
    finally:
        session.close()


def run(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parse_argv(argv)

    registry = list_active_practice_registry(get_engine())[: args.max_tenants]
    logger.info(
        "payer_enrollment_refresh_start tenants=%s max_per_tenant=%s dry_run=%s",
        len(registry),
        args.max_per_tenant,
        args.dry_run,
    )

    visited = 0
    changed = 0
    for schema, practice_id in registry:
        client = get_clearinghouse_client(practice_id)
        if client is None:
            continue
        visited += 1
        if args.dry_run:
            logger.info(
                "payer_enrollment_refresh_dry_run schema=%s open_requests=%s",
                schema,
                _count_open(schema),
            )
            continue
        changed += refresh_tenant(schema, client, limit=args.max_per_tenant)

    logger.info(
        "payer_enrollment_refresh_done tenants_visited=%s requests_changed=%s",
        visited,
        changed,
    )
    return 0


def _parse_argv(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-tenants",
        type=int,
        default=DEFAULT_MAX_TENANTS,
        help=f"How many practices one run visits, in schema order (default {DEFAULT_MAX_TENANTS}).",
    )
    parser.add_argument(
        "--max-per-tenant",
        type=int,
        default=MAX_REFRESH_PER_TENANT,
        help=f"How many open requests to refresh per practice (default {MAX_REFRESH_PER_TENANT}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count the open requests per practice without calling the clearinghouse.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run())
