# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""profile_basics_completed_at on platform.users

Adds ``profile_basics_completed_at TIMESTAMPTZ NULL`` to ``platform.users``.

Set when the user explicitly submits the profile-basics onboarding step
(name / title / credentials / phone). NULL means the user has not yet
completed that step via the wizard — even if they already have a display
name from Google auth. The onboarding wizard gates on this timestamp so
Google-auth users who never set credentials/phone still see the step.

Revision ID: b2e7d4c91f05
Revises: f3a9c1e84b27
Create Date: 2026-06-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2e7d4c91f05"
down_revision: str | Sequence[str] | None = "c4e8d1f6a2b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "profile_basics_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_column("users", "profile_basics_completed_at", schema="platform")
