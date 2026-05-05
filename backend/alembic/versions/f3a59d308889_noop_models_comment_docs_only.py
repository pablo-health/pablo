# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""No-op revision — satisfies models+migrations guardrail for comment-only edits.

``backend/app/db/models.py`` gained documentation-only updates (soft-delete /
retention wording). No DDL change — ``upgrade`` / ``downgrade`` are empty so
tenant fan-out stays a cheap stamp.

Revision ID: f3a59d308889
Revises: d4f8a1c92e35
Create Date: 2026-05-05

"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f3a59d308889"
down_revision: str | Sequence[str] | None = "d4f8a1c92e35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No DDL — docs-only churn on ORM columns."""


def downgrade() -> None:
    """No DDL."""
