# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""google_calendar_tokens.event_titling — how a pushed session reads

A therapist can now choose what a session looks like on their own
calendar: the generic wording, the client's initials, or their name. The
choice belongs to the connection, because it describes what gets written
to that particular calendar.

Existing rows default to ``generic``, which is what they have been
pushing. Defaulting them to initials instead would change what an
already-connected therapist's calendar says about their clients without
anyone asking them — a preference nobody set is not consent, and the whole
point of the higher rungs is that they are chosen. New connections pick a
value while connecting.

``google_calendar_tokens`` is per-tenant, so this runs once per schema
through the tenant fan-out and the tenant template must be regenerated.

Revision ID: c8b16f24e07d
Revises: a9e4c72d13b6
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c8b16f24e07d"
down_revision: str | Sequence[str] | None = "a9e4c72d13b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE google_calendar_tokens "
        "ADD COLUMN IF NOT EXISTS event_titling VARCHAR(16) NOT NULL DEFAULT 'generic'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE google_calendar_tokens DROP COLUMN IF EXISTS event_titling")
