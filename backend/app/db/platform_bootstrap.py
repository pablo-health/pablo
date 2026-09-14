# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deciding whether a database needs the platform baseline run, or just stamped.

The platform chain's baseline applies ``platform_template.sql`` with plain
``CREATE TABLE``. That is deliberate — a baseline written with
``IF NOT EXISTS`` would run green against a table whose shape had already
drifted, which is the failure the chain exists to end — but it means the baseline
must not be *run* against a database that already has the schema.

Almost every database does. The platform schema has existed since long before
the chain, built by ``PlatformBase.metadata.create_all``, so the ordinary case is
"the schema is here, the bookkeeping is not". That database is stamped at the
baseline and picks up later revisions normally. Only a genuinely empty database
runs the baseline for real.

**Why this is automatic rather than an operator step.** The alternative is a
manual stamp that every existing deployment needs exactly once, discovered by
hitting a failed migration — including for a self-hoster who has no idea the step
exists. ``bin/migrate.py`` already carries the same argument for the
single-practice migration: the migrate job runs before the rollout, with the
database in front of it and its output in the log, which is the right place for a
decision that has to be made once and made correctly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text

from . import PLATFORM_SCHEMA

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

#: The platform chain's first revision. Must match the ``revision`` in
#: ``backend/alembic_platform/versions/a1b7c3d95e24_platform_schema_baseline.py``;
#: an integration test asserts the two agree, because a stamp pointing at a
#: revision the chain does not contain fails later and far from here, as
#: ``Can't locate revision identified by …`` on the next upgrade.
BASELINE_REVISION = "a1b7c3d95e24"

#: The chain's bookkeeping table, in the platform schema.
VERSION_TABLE = "alembic_version_platform"


def _platform_table_count(engine: Engine) -> int:
    """How many base tables the platform schema has, the version table aside."""
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema = :schema AND table_type = 'BASE TABLE'"
                    " AND table_name <> :version_table"
                ),
                {"schema": PLATFORM_SCHEMA, "version_table": VERSION_TABLE},
            ).scalar_one()
        )


def _has_version_table(engine: Engine) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema = :schema AND table_name = :version_table"
                ),
                {"schema": PLATFORM_SCHEMA, "version_table": VERSION_TABLE},
            ).scalar_one()
        )


def needs_baseline_stamp(engine: Engine) -> bool:
    """True when the platform schema exists but the chain has never been recorded.

    That is the pre-chain database: built by ``create_all``, evolved by raw SQL
    in the tenant chain, and carrying no platform bookkeeping of its own. It must
    be stamped rather than migrated from base, because the baseline would try to
    create tables it already has.

    False in the two other cases, which both want no stamp:

    * An empty database — nothing to preserve, so the baseline runs for real.
    * A database already carrying ``alembic_version_platform`` — the chain owns
      it, and alembic knows where it stands.
    """
    if _has_version_table(engine):
        return False
    return _platform_table_count(engine) > 0
