# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""chart closure columns on patients (THERAPY-hek)

Adds ``chart_closed_at TIMESTAMPTZ NULL`` and
``chart_closure_reason TEXT NULL`` to ``patients``.

Why two timestamps instead of a new ``status`` enum value
======================================================
``patients.status`` stays in {active, inactive, on_hold} on purpose.
Chart closure is **orthogonal** to status:

  * A clinician may flag a patient as ``inactive`` (e.g. paused) without
    closing the chart, or close the chart while the row is ``active``
    (record retained for the post-care retention window).
  * Read paths (list/get) deliberately keep returning chart-closed
    patients — closure does not hide rows. Adding ``closed`` to the
    status enum would have forced every existing list filter to opt
    back in, and would have conflated a clinical-state field with a
    record-lifecycle event.
  * The day-30 hard-purge cron (THERAPY-cgy) keys off ``deleted_at``,
    not ``chart_closed_at``. Closing a chart must NOT advance the
    purge clock; that's why closure is its own pair of columns.

Together with ``deleted_at`` (THERAPY-nyb), the patient row carries
three lifecycle markers, all NULL by default:

    deleted_at         set by Remove-from-practice (soft-delete)
    chart_closed_at    set by Close-chart (THERAPY-hek)
    chart_closure_reason  free-text reason (PHI-adjacent — never copied
                          into audit_logs)

Migration shape
===============
Idempotent (``ADD COLUMN IF NOT EXISTS``); runs once per tenant via the
per-tenant fan-out in ``backend/alembic/env.py`` (search-path-scoped).
No backfill — existing rows keep ``chart_closed_at = NULL`` (chart
open) which is the correct historical default.

Revision ID: e5b91c34a72f
Revises: f3a59d308889
Create Date: 2026-05-05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e5b91c34a72f"
down_revision: str | Sequence[str] | None = "f3a59d308889"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS chart_closed_at TIMESTAMPTZ NULL")
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS chart_closure_reason TEXT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS chart_closure_reason")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS chart_closed_at")
