# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""google_calendar_tokens.titling_attested_account — which account was attested

Writing a client's name onto a calendar rests on the therapist confirming
that calendar's account is covered by an agreement their practice holds.
That confirmation is about one account, so it cannot follow the
preference to a different one.

This records the account it was made about, so the account currently
connected can be checked against it before any name is written. Existing
rows get the empty default, which reads as "nothing attested" — and since
the only preference those rows can hold is the generic wording, nothing
about them changes.

``google_calendar_tokens`` is per-tenant, so this runs once per schema
through the tenant fan-out and the tenant template must be regenerated.

Revision ID: e4f9a0c7b52d
Revises: c8b16f24e07d
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e4f9a0c7b52d"
down_revision: str | Sequence[str] | None = "c8b16f24e07d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE google_calendar_tokens "
        "ADD COLUMN IF NOT EXISTS titling_attested_account VARCHAR(255) NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE google_calendar_tokens DROP COLUMN IF EXISTS titling_attested_account")
