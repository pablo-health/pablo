# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""notes author and restricted

Two columns a note needs before one can be held to its author:

* ``author_user_id`` — who wrote it. Nullable, because every existing row
  predates the column and there is no honest value to backfill; no FK,
  since users live in the platform schema.
* ``restricted`` — readable by the author alone. Stamped from the note
  type's definition at creation (the psychotherapy note is the first such
  type). Every existing row is a progress note and backfills to false.

The row policy that reads these is not created here. ``notes`` takes its
policy from ``enable_rls_on_schema``, which the per-practice reconcile
re-runs after every upgrade, so an existing practice picks up the new
author-only arm on the same deploy that adds the columns, and a fresh one
gets it at provisioning. The policy guards on the column being present, so
the heal revision earlier in this chain still replays cleanly over a
schema that does not have it yet.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: c5d2e8f1a743
Revises: a4d7e2c91f35
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c5d2e8f1a743"
down_revision: str | Sequence[str] | None = "a4d7e2c91f35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS author_user_id UUID NULL")
    op.execute(
        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS restricted BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS restricted")
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS author_user_id")
