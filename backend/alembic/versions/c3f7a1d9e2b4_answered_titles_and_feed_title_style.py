# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The title an answer was given under, and how a feed titles its events

Two nullable columns, nothing else changes.

``patient_source_mappings.answered_title`` is a keyed digest of the event
title a remembered answer was given under (``answered_title_digest``). A
calendar provider's series id stays the same when the series is handed to
another client — editing every event of a Google series to a new name keeps
the id — so a remembered answer for a series books without asking only while
its title is still the one it was answered under. Rows from before this
column have no title on record; the next event of such a series is asked
about once, pre-filled with the remembered client, and the answer records the
title. The digest is keyed under a server-side secret because the title is
often a client's name: nothing here is reversible by someone holding the
practice's client list.

``ical_sync_configs.title_style`` records how a followed calendar feed names
clients as of its last read: ``initials``, ``names`` or ``codes``. Calendar
settings say so when it is initials, because initials never identify one
client and every such session has to be asked about.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema, and each step is guarded.

Revision ID: c3f7a1d9e2b4
Revises: b8e1f5a3c7d2
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c3f7a1d9e2b4"
down_revision: str | Sequence[str] | None = "b8e1f5a3c7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE patient_source_mappings
            ADD COLUMN IF NOT EXISTS answered_title TEXT;
        ALTER TABLE ical_sync_configs
            ADD COLUMN IF NOT EXISTS title_style VARCHAR(16);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE ical_sync_configs DROP COLUMN IF EXISTS title_style;
        ALTER TABLE patient_source_mappings DROP COLUMN IF EXISTS answered_title;
        """
    )
