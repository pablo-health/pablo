# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""intake_documents table

The consent documents a practice asks people to read and sign, one row per
version of one document.

Practice-level, like the three form-builder tables it sits beside: the text
is the same whoever it is sent to, so there is no ``patient_id`` to key a
row policy on and the tenant schema is the boundary. ``intake_documents``
is registered not-row-scoped in ``app.db`` for that reason — it carries an
``id``, so without the registration ``enable_rls_on_schema`` would force
row-level security on it and leave it with no policy, which is a silent
deny-all.

**Two indexes carry rules rather than performance.**

``uq_intake_documents_version`` says a document has exactly one row per
version number, which is what makes "the version somebody signed" a
question with one answer.

``uq_intake_documents_draft`` is partial on ``published_at IS NULL`` and
says a document has at most one unpublished draft. A practice can reach
"start a new version" from more than one screen, and two drafts of the same
document is a state nothing downstream knows how to read. Partial, so it
has to be an index rather than a table constraint.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Originally cut on ``c5f80a214d9e`` and re-pointed at ``d4a7b1e93c26`` after
the fact. Both were cut off the same parent and landed within minutes of
each other, which left the chain with two heads — a state git merges
without complaint and alembic refuses to run at all. Landing order is
chain order, so the one that merged second re-points; the table this
creates is untouched either way, because nothing in it depends on what
came before.

Revision ID: d1b6a934f7c0
Revises: d4a7b1e93c26
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d1b6a934f7c0"
down_revision: str | Sequence[str] | None = "d4a7b1e93c26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_documents (
            id                 UUID         PRIMARY KEY,
            document_key       UUID         NOT NULL,
            title              VARCHAR(160) NOT NULL,
            body_markdown      TEXT         NOT NULL,
            version            INTEGER      NOT NULL,
            digest             VARCHAR(64)  NOT NULL,
            published_at       TIMESTAMPTZ,
            published_by       UUID,
            requires_signature BOOLEAN      NOT NULL DEFAULT TRUE,
            signer_roles       JSONB        NOT NULL DEFAULT '["patient"]'::jsonb,
            created_at         TIMESTAMPTZ  NOT NULL,
            CONSTRAINT uq_intake_documents_version UNIQUE (document_key, version)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_intake_documents_document_key "
        "ON intake_documents (document_key);"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_intake_documents_draft "
        "ON intake_documents (document_key) WHERE published_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS intake_documents CASCADE;")
