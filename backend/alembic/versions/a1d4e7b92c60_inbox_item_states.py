# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""inbox_item_states — what a clinician did with an Inbox item

The Inbox lists items that live in their sources' own tables and copies none
of them. This table holds what belongs to the Inbox instead: dismissed,
snoozed until a time, marked handled, replied to. Keyed by
``(source_kind, source_id)``, one live row per clinician per item, and
superseded rather than updated so an undo keeps the history.

``user_id`` carries the row, so ``enable_rls_on_schema`` gives it the
direct-ownership policy; the tenant migrate job applies it after the chain
runs, and provisioning applies it to new schemas.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: a1d4e7b92c60
Revises: d6a2e9f4b1c8
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a1d4e7b92c60"
down_revision: str | Sequence[str] | None = "d6a2e9f4b1c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS inbox_item_states ("
        "id UUID PRIMARY KEY, "
        "user_id UUID NOT NULL, "
        "source_kind VARCHAR(40) NOT NULL, "
        "source_id VARCHAR(128) NOT NULL, "
        "disposition VARCHAR(40) NOT NULL, "
        "snoozed_until TIMESTAMP WITH TIME ZONE, "
        "resolved_by UUID, "
        "resolved_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(), "
        "superseded_by UUID, "
        "superseded_at TIMESTAMP WITH TIME ZONE)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_inbox_item_states_live "
        "ON inbox_item_states (user_id, source_kind, source_id) "
        "WHERE superseded_by IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS inbox_item_states")
