# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""launch_intents table

Creates ``platform.launch_intents``, the single-use store backing the
web→companion "Start Session" handoff. A row is created when a therapist
issues a launch intent and consumed when the desktop companion redeems
it. Only the SHA-256 hash of the opaque intent id is stored
(``intent_hash``) — never the raw id. ``consumed_at`` non-null marks the
intent spent; ``expires_at`` is the authoritative 180s expiry.

No PHI: ``appointment_id`` is an opaque pointer; no patient data is
stored here. Shares the ``platform`` schema (no RLS) with
``companion_devices``.

Revision ID: d4b7e9a1c305
Revises: b6e1d8c4a7f2
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d4b7e9a1c305"
down_revision: str | Sequence[str] | None = "b6e1d8c4a7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.launch_intents (
            intent_hash    VARCHAR(64)  PRIMARY KEY,
            -- UUID to match platform.users.id, which was converted to
            -- native uuid by revision c1d7e4a9f2b6 (this revision sits
            -- after it in the chain).
            user_id        UUID         NOT NULL
                REFERENCES platform.users(id) ON DELETE CASCADE,
            appointment_id VARCHAR(128) NOT NULL,
            created_at     TIMESTAMPTZ  NOT NULL,
            expires_at     TIMESTAMPTZ  NOT NULL,
            consumed_at    TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_launch_intents_user_id ON platform.launch_intents (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_launch_intents_expires_at "
        "ON platform.launch_intents (expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.launch_intents")
