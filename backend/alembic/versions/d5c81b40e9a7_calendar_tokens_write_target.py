# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""google_calendar_tokens.write_target — which calendar a connection writes to

Connecting a calendar now offers a choice: let Pablo make a calendar of its
own and write only there, or write into the therapist's own calendar
alongside everything else. The two are reached by different grants, so a
connection has to remember which one it holds.

Existing rows were all made before the choice existed, under a grant that
writes into the therapist's own calendar — hence the ``primary`` server
default, which keeps them meaning exactly what they meant, with no
backfill and no re-consent. New connections default to the calendar Pablo
makes, but that is the connect flow's decision, not the column's.

``google_calendar_tokens`` is per-tenant, so this runs once per schema
through the tenant fan-out and the tenant template must be regenerated.

Revision ID: d5c81b40e9a7
Revises: b3f7d2c9a15e
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d5c81b40e9a7"
down_revision: str | Sequence[str] | None = "b3f7d2c9a15e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE google_calendar_tokens "
        "ADD COLUMN IF NOT EXISTS write_target VARCHAR(32) NOT NULL DEFAULT 'primary'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE google_calendar_tokens DROP COLUMN IF EXISTS write_target")
