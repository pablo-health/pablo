# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""google_calendar_tokens.granted_capabilities — what a connection actually holds

Connecting a calendar asks for one permission per thing Pablo will do, and
reading event content is asked for later, when an import is actually run.
So a connection can hold any subset, and a feature has to be able to tell
"never asked for" from "asked and refused" without a round trip to the
provider.

The column records that subset as a comma-separated list. Its server
default is ``push,import`` because every row predating the per-capability
connect flow was granted event write and event read together — the default
describes those rows truthfully, and every row written from now on carries
its own set explicitly.

``google_calendar_tokens`` is per-tenant, so this runs once per schema
through the tenant fan-out and the tenant template must be regenerated.

Revision ID: a9e4c72d13b6
Revises: d5c81b40e9a7
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a9e4c72d13b6"
down_revision: str | Sequence[str] | None = "d5c81b40e9a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE google_calendar_tokens "
        "ADD COLUMN IF NOT EXISTS granted_capabilities VARCHAR(255) "
        "NOT NULL DEFAULT 'push,import'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE google_calendar_tokens DROP COLUMN IF EXISTS granted_capabilities")
