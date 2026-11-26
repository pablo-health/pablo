# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice built the way the template regen builds its scratch schema.

The tests of the policy planner (``infer_rls_on_schema``) run in the planner's
own world: the template's schema half, then the planner, then the practice
directory's access. Not a schema provisioning built: provisioning makes a
practice match the template through the run-time pass, which records its own
bookkeeping on a few policies, and the planner reads that as not its own.
The two are never run over one schema outside these tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.db import RlsReconcileCounts
    from sqlalchemy.engine import Engine


def build_planner_practice(engine: Engine, schema: str) -> None:
    """Create ``schema`` from the template's schema half and give it the planner's policies."""
    from app.db.practice_directory import apply_practice_directory_access
    from app.db.provisioning import (
        _apply_tenant_template,
        _stamp_alembic_at_head,
    )

    _apply_tenant_template(engine, schema)
    _stamp_alembic_at_head(engine, schema)
    run_planner(engine, schema)
    with engine.begin() as conn:
        apply_practice_directory_access(conn, schema)


def run_planner(engine: Engine, schema: str, *, strict: bool = True) -> RlsReconcileCounts:
    """``infer_rls_on_schema`` over ``schema`` on its own session, as the regen runs it."""
    from app.db import PLATFORM_SCHEMA, infer_rls_on_schema

    with Session(engine) as session:
        session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        counts = infer_rls_on_schema(session, schema, strict=strict)
        session.commit()
        return counts
