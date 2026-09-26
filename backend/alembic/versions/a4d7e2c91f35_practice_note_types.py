# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""practice_note_types

Note formats a practice defines for itself, one row per saved version,
plus the two columns a note needs to use one: ``note_type_version`` (which
version the content was written against) and ``note_inputs`` (the values
supplied for the type's declared inputs). Appointments carry
``note_inputs`` too, because that is where the note type is chosen and the
values are copied onto the note when the session starts.

Practice-level, so ``practice_note_types`` carries no ``patient_id`` and no
``user_id``; ``created_by`` names who saved a version rather than who owns
it. It is registered not-row-scoped in ``app.db`` for that reason — its
boundary is the schema.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: a4d7e2c91f35
Revises: e2c94f71b6a3
Create Date: 2026-09-23
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a4d7e2c91f35"
down_revision: str | Sequence[str] | None = "e2c94f71b6a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS practice_note_types (
            id          UUID         PRIMARY KEY,
            key         VARCHAR(30)  NOT NULL,
            version     INTEGER      NOT NULL,
            definition  JSONB        NOT NULL,
            created_by  UUID         NOT NULL,
            created_at  TIMESTAMPTZ  NOT NULL,
            retired_at  TIMESTAMPTZ,
            CONSTRAINT uq_practice_note_types_key_version UNIQUE (key, version)
        );
        """
    )
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS note_type_version INTEGER;")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS note_inputs JSONB;")
    op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS note_inputs JSONB;")


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS note_inputs;")
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS note_inputs;")
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS note_type_version;")
    op.execute("DROP TABLE IF EXISTS practice_note_types CASCADE;")
