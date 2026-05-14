# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""onboarding_state column on platform.users (THERAPY-i8sy.13)

Adds ``onboarding_state VARCHAR(20) NULL`` to ``platform.users``.

Values are ``"in_progress" | "later" | "completed"`` — enforced
application-side by the Pydantic Literal on ``UpdateUserRequest``.
NULL is meaningful: it means "grandfathered" (the row existed before
this column was introduced) and is treated by the SaaS overlay as
"already completed" — no banner, no redirect. Forward, the wizard
sets ``in_progress`` on first entry and ``completed`` once every
registered step's gate is satisfied. ``later`` is set when the user
clicks Later on an optional step (no optional steps exist yet, but
the field is part of the contract).

Self-hosted (OSS) deployments do not surface the onboarding wizard;
the column exists in OSS because the User model is OSS, and the SaaS
overlay is the only consumer today.

Revision ID: e7c4f1a8d293
Revises: d5a3e9b71f6c
Create Date: 2026-05-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e7c4f1a8d293"
down_revision: str | Sequence[str] | None = "d5a3e9b71f6c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS onboarding_state VARCHAR(20) NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS onboarding_state")
