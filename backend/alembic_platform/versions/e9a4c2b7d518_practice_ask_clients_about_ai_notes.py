# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices.ask_clients_about_ai_notes: on by default

Whether the practice's clinicians ask each client to agree to AI-assisted
notes. On, the session surface offers a read-aloud script and session notes
show the client's answer from their consent record; off, neither appears.

Every practice starts with it on, existing ones included: the column default
fills every current row, so there is no separate backfill.

No PHI: a single practice preference.

**Written idempotently, and expected to be a no-op** on the paths that
materialise the platform tables from the models before this chain runs — the
same reasoning as ``d1c7b94e3a26_portal_practice_slugs``.

Revision ID: e9a4c2b7d518
Revises: c5d1e8a7b204
Create Date: 2026-10-05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e9a4c2b7d518"
down_revision: str | Sequence[str] | None = "c5d1e8a7b204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practices"
        " ADD COLUMN IF NOT EXISTS ask_clients_about_ai_notes BOOLEAN NOT NULL DEFAULT true;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.practices DROP COLUMN IF EXISTS ask_clients_about_ai_notes;")
