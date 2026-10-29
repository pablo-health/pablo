# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Refill requests carry when the client runs out; medications carry two flags

``refill_requests`` gains ``run_out_date``: the day the client expects to run
out, from their answer to "how many days do you have left?" when they ask.
NULL on every existing row and whenever the client leaves the question blank.

``patient_medications`` gains two clinician-set flags, both NULL by default,
where NULL means "use the engine's default":

* ``controlled`` -- whether the medication is a controlled substance. Read
  only when no prescription for it is on file; a prescription's own schedule
  says it otherwise.
* ``interruption_risk`` -- whether a gap in the medication is harmful. NULL
  falls back to the engine's cited default list
  (``app.models.refill_risk``); a clinician may set it either way.

Nothing is backfilled. Idempotent, like every revision in this chain.

Revision ID: f4b2c8e1a937
Revises: d3a7f1c58e20
Create Date: 2026-10-21
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f4b2c8e1a937"
down_revision: str | Sequence[str] | None = "d3a7f1c58e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute("ALTER TABLE refill_requests ADD COLUMN IF NOT EXISTS run_out_date DATE;")
    op.execute("ALTER TABLE patient_medications ADD COLUMN IF NOT EXISTS controlled BOOLEAN;")
    op.execute(
        "ALTER TABLE patient_medications ADD COLUMN IF NOT EXISTS interruption_risk BOOLEAN;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE patient_medications DROP COLUMN IF EXISTS interruption_risk;")
    op.execute("ALTER TABLE patient_medications DROP COLUMN IF EXISTS controlled;")
    op.execute("ALTER TABLE refill_requests DROP COLUMN IF EXISTS run_out_date;")
