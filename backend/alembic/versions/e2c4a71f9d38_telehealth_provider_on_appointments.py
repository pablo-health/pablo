# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""telehealth provider on appointments, and per-clinician video connections

Five columns and a table.

``provider`` is the normalised key of whatever made the meeting link;
``meeting_external_id`` is the vendor's own handle for it, indexed because a
vendor webhook arrives carrying that handle and nothing else. The three
timestamps are what a verified vendor webhook may say about the call: that
the patient arrived, that it started, that it ended. Each is written once —
the first write wins — which is what lets a redelivery be a no-op without a
ledger of event ids beside it.

``telehealth_connections`` holds a clinician's OAuth grant for a video
service that issues meetings through an API. Keyed by the pair rather than
by the clinician, because a clinician may connect more than one service and
a second service should be a row. The grant is encrypted at rest before it
reaches the column, exactly as the calendar grant is.

The user id stays a plain UUID column. Users live in ``platform``, and no
per-tenant table references them.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: e2c4a71f9d38
Revises: d5e1b7a93c46
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e2c4a71f9d38"
down_revision: str | Sequence[str] | None = "d5e1b7a93c46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        ALTER TABLE appointments
            ADD COLUMN IF NOT EXISTS provider                 VARCHAR(16),
            ADD COLUMN IF NOT EXISTS meeting_external_id      VARCHAR(128),
            ADD COLUMN IF NOT EXISTS telehealth_checked_in_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS telehealth_started_at    TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS telehealth_ended_at      TIMESTAMPTZ;
        """
    )
    # The name is the one SQLAlchemy generates for ``index=True`` on this
    # column, which is what the regenerated tenant template carries — the two
    # producers of this schema have to agree down to the index name.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_appointments_meeting_external_id
            ON appointments (meeting_external_id);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS telehealth_connections (
            user_id          UUID         NOT NULL,
            provider         VARCHAR(16)  NOT NULL,
            encrypted_tokens TEXT         NOT NULL,
            account_handle   VARCHAR(255),
            connected_at     TIMESTAMPTZ,
            last_error       TEXT,
            CONSTRAINT telehealth_connections_pkey PRIMARY KEY (user_id, provider)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS telehealth_connections;")
    op.execute("DROP INDEX IF EXISTS ix_appointments_meeting_external_id;")
    op.execute(
        """
        ALTER TABLE appointments
            DROP COLUMN IF EXISTS telehealth_ended_at,
            DROP COLUMN IF EXISTS telehealth_started_at,
            DROP COLUMN IF EXISTS telehealth_checked_in_at,
            DROP COLUMN IF EXISTS meeting_external_id,
            DROP COLUMN IF EXISTS provider;
        """
    )
