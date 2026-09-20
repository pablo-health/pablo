# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.companion_practice_slugs: the public address of a practice's portal

The portal shell's first call is unauthenticated and carries only the slug out
of its own URL, so the practice has to be resolved FROM the slug before any
session exists or any tenant schema can be chosen. That inversion is why this
lives in the shared schema rather than in a practice's own, and it is the same
reason ``booking_links`` is here.

``practice_id`` is UNIQUE: the clinician-facing mint is an idempotent
get-or-create keyed on the practice, so a practice has exactly one address and
re-asking returns the one it already has.

No PHI. A slug, a practice id, and the practice's own display name — which is a
business name the practice already shows the people it treats.

**Written idempotently, and expected to be a no-op.** The platform tables are
materialised from the models with ``create_all`` before this chain runs, so the
table already exists by the time this executes on every path; a plain
``op.create_table`` would fail with ``DuplicateTable``. It is here because the
DDL belongs somewhere a reviewer looks for it, and because a model change
arrives with a migration.

Revision ID: d1c7b94e3a26
Revises: f2a6d94c08b3
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d1c7b94e3a26"
down_revision: str | Sequence[str] | None = "f2a6d94c08b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.companion_practice_slugs (
            slug          VARCHAR(63)  NOT NULL PRIMARY KEY,
            practice_id   VARCHAR(128) NOT NULL UNIQUE,
            display_name  VARCHAR(255) NOT NULL,
            enabled       BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMP WITH TIME ZONE NOT NULL
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.companion_practice_slugs;")
