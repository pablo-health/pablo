# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""clinician_away_periods table, and the away acknowledgment on patient_messages

``clinician_away_periods`` holds the dates a clinician is away: first and
last day away, the day they are back, an optional covering colleague, and the
acknowledgment text they reviewed, with a switch to turn it off. It is
registered not-row-scoped (``_CORE_NOT_ROW_SCOPED``): a client writing in
reads their clinician's period, so the schema is the boundary.

``patient_messages.away_period_id`` marks the acknowledgment a period sent,
and a partial unique index on ``(thread_id, away_period_id)`` keeps it to one
per thread per period.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: d2a7e4c91b58
Revises: c8e3a1f5d927
Create Date: 2026-10-24
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d2a7e4c91b58"
down_revision: str | Sequence[str] | None = "c8e3a1f5d927"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS clinician_away_periods (
            id                      UUID          PRIMARY KEY,
            user_id                 UUID          NOT NULL,
            start_date              DATE          NOT NULL,
            end_date                DATE          NOT NULL,
            return_date             DATE          NOT NULL,
            covering_user_id        UUID,
            acknowledgment_enabled  BOOLEAN       NOT NULL DEFAULT true,
            acknowledgment_text     TEXT          NOT NULL,
            ended_at                TIMESTAMPTZ,
            created_at              TIMESTAMPTZ   NOT NULL,
            updated_at              TIMESTAMPTZ   NOT NULL,
            CONSTRAINT ck_clinician_away_periods_dates CHECK (end_date >= start_date),
            CONSTRAINT ck_clinician_away_periods_return CHECK (return_date > end_date)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clinician_away_periods_user_dates "
        "ON clinician_away_periods (user_id, start_date, end_date);"
    )
    op.execute(
        "ALTER TABLE patient_messages ADD COLUMN IF NOT EXISTS away_period_id UUID "
        "REFERENCES clinician_away_periods (id) ON DELETE SET NULL;"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_messages_away_ack "
        "ON patient_messages (thread_id, away_period_id) WHERE away_period_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_patient_messages_away_ack;")
    op.execute("ALTER TABLE patient_messages DROP COLUMN IF EXISTS away_period_id;")
    op.execute("DROP TABLE IF EXISTS clinician_away_periods CASCADE;")
