# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Platform index: which practice filed a given claim control number.

Revision ID: c4e8b1f7a2d9
Revises: f6a3c9d21b47
Create Date: 2026-09-10

A clearinghouse webhook names a transaction, not a practice. Without an index
the receiver has to open every practice in turn and ask whether any of its
clinicians can see the claim — a search that must be bounded to answer inside
the vendor's timeout, so a practice past the bound is never asked at all and
its claims silently stop moving (PABLO-ffw8).

Holds a control number and a practice id and nothing else: no PHI, no clinical
content, no patient identifier. The primary key refuses a control number
claimed by two practices, which is the case that would otherwise post a
payer's money into whichever practice a search reached first.

**Written idempotently, and expected to be a no-op.** ``alembic/env.py``
materializes the platform tables from the models with ``create_all`` in a
committed transaction BEFORE this chain runs, so on every path — fresh
database or existing one — this table already exists by the time we get here.
A plain ``op.create_table`` would therefore fail with ``DuplicateTable`` rather
than run. It is here because the DDL belongs somewhere a reviewer looks for it,
and because CLAUDE.md guardrail #4 asks a model change to arrive with one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c4e8b1f7a2d9"
down_revision: str | Sequence[str] | None = "f6a3c9d21b47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.claim_routes (
            control_number VARCHAR(17) NOT NULL PRIMARY KEY,
            practice_id VARCHAR(128) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_claim_routes_practice_id
            ON platform.claim_routes (practice_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS platform.ix_claim_routes_practice_id;")
    op.execute("DROP TABLE IF EXISTS platform.claim_routes;")
