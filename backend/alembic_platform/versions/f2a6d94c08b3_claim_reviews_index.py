# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.claim_reviews: which claims are waiting to be read before filing

Revision ID: f2a6d94c08b3
Revises: a7c4e9b21f58
Create Date: 2026-09-18

A claim held for review lives in its practice's schema and is row-policied to
the clinician who owns it, so listing every claim waiting on a reviewer means
opening each practice in turn — a search that has to be bounded, which makes it
wrong past the bound. The claims a bounded list omits are precisely the ones
nobody knows to release, and each is sitting on a filing deadline. Same problem
and same answer as ``platform.claim_routes``.

Holds the claim id, whose practice and clinician it is, the control number, the
PAYER's name, the reason codes, and when. No PHI: a payer is an insurance
company, and nothing here names a client, a diagnosis, a service or an amount.
A reviewer who needs those opens the claim in its own tenant session, where the
row policy still applies.

The primary key is the claim, so a claim cannot appear in the queue twice and
releasing it is a delete by key.

Lives in the PLATFORM chain, not the tenant chain, because the table lives in
one shared schema and there is one of it — ``claim_routes`` sits in the tenant
chain only because it predates this chain existing.

**Written idempotently, and expected to be a no-op.** The platform tables are
materialised from the models with ``create_all`` before this chain runs, so the
table already exists by the time this executes on every path; a plain
``op.create_table`` would fail with ``DuplicateTable``. It is here because the
DDL belongs somewhere a reviewer looks for it, and because a model change
arrives with a migration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f2a6d94c08b3"
down_revision: str | Sequence[str] | None = "a7c4e9b21f58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.claim_reviews (
            claim_id VARCHAR(64) NOT NULL PRIMARY KEY,
            practice_id VARCHAR(128) NOT NULL,
            user_id VARCHAR(128) NOT NULL,
            control_number VARCHAR(17) NOT NULL,
            payer_name VARCHAR(255) NOT NULL,
            reasons VARCHAR(255) NOT NULL,
            held_at TIMESTAMP WITH TIME ZONE NOT NULL
        );
        """
    )
    # The name follows the models' naming convention
    # (``ix_platform_<table>_<column>``), not the shorter form it would be
    # natural to type. The models create this index through ``create_all``
    # before this chain runs, so a differently-named ``CREATE INDEX IF NOT
    # EXISTS`` does not match the existing one and quietly builds a SECOND
    # index over the same column — which is what ``d8f3b6c04e17`` had to go
    # back and drop for ``claim_routes``. Matching the convention keeps
    # ``alembic check`` quiet too: autogenerate compares against the model's
    # name and reports a rename as drift.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_platform_claim_reviews_practice_id
            ON platform.claim_reviews (practice_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS platform.ix_platform_claim_reviews_practice_id;")
    op.execute("DROP TABLE IF EXISTS platform.claim_reviews;")
