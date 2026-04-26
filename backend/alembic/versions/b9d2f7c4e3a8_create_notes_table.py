"""create notes table + backfill from therapy_sessions

Extracts a first-class ``notes`` table from ``therapy_sessions`` (pa-0nx).
After this migration:

* ``notes`` exists with one row per session that has a generated note.
* Sessions whose ``note_content`` is NULL/empty get NO note row — those
  sessions never had a generated note, and we'd rather create a Note
  later (when one is actually generated) than carry empty placeholder
  rows. Documented in ``test_create_notes_table_migration`` so the rule
  is enforceable.
* No readers/writers use ``notes`` yet — that comes in pa-0nx.2. The
  ``soap_note`` / ``content`` columns on ``therapy_sessions`` stay
  populated through the dual-read window and only get dropped in
  pa-0nx.5.

The partial unique index ``ux_notes_session_id`` (WHERE session_id IS
NOT NULL) preserves today's 1:1 session↔note invariant while still
allowing standalone notes (session_id NULL).

Revision ID: b9d2f7c4e3a8
Revises: a7b3e5f2c8d1
Create Date: 2026-04-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b9d2f7c4e3a8"
down_revision: str | Sequence[str] | None = "a7b3e5f2c8d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("patient_id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column(
            "note_type",
            sa.String(length=30),
            nullable=False,
            server_default="soap",
        ),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "content_edited", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quality_rating", sa.Integer(), nullable=True),
        sa.Column("quality_rating_reason", sa.Text(), nullable=True),
        sa.Column(
            "quality_rating_sections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "export_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_queued",
        ),
        sa.Column("export_queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("export_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("export_reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "redacted_content", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "naturalized_content",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "redacted_export_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notes_patient_id"), "notes", ["patient_id"], unique=False)
    op.create_index(op.f("ix_notes_session_id"), "notes", ["session_id"], unique=False)
    op.create_index(
        "ix_notes_patient_finalized",
        "notes",
        ["patient_id", sa.text("finalized_at DESC")],
        unique=False,
    )
    op.create_index(
        "ux_notes_session_id",
        "notes",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )

    # Backfill from therapy_sessions. Skip rows with no generated note —
    # see module docstring for the rationale. Use the row's own id for
    # the note id so the backfill is idempotent (re-running the same
    # migration up against the same data is a no-op via ON CONFLICT).
    op.execute(
        sa.text(
            """
            INSERT INTO notes (
                id,
                patient_id,
                session_id,
                note_type,
                content,
                content_edited,
                finalized_at,
                quality_rating,
                quality_rating_reason,
                quality_rating_sections,
                export_status,
                export_queued_at,
                export_reviewed_at,
                export_reviewed_by,
                exported_at,
                redacted_content,
                naturalized_content,
                created_at,
                updated_at
            )
            SELECT
                ts.id,
                ts.patient_id,
                ts.id,
                COALESCE(ts.note_type, 'soap'),
                ts.note_content,
                ts.note_content_edited,
                ts.finalized_at,
                ts.quality_rating,
                ts.quality_rating_reason,
                ts.quality_rating_sections,
                COALESCE(ts.export_status, 'not_queued'),
                ts.export_queued_at,
                ts.export_reviewed_at,
                ts.export_reviewed_by,
                ts.exported_at,
                ts.redacted_soap_note,
                ts.naturalized_soap_note,
                ts.created_at,
                COALESCE(ts.updated_at, ts.created_at)
            FROM therapy_sessions AS ts
            WHERE ts.note_content IS NOT NULL
              AND ts.note_content::text <> '{}'::text
            ON CONFLICT (id) DO NOTHING;
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ux_notes_session_id", table_name="notes")
    op.drop_index("ix_notes_patient_finalized", table_name="notes")
    op.drop_index(op.f("ix_notes_session_id"), table_name="notes")
    op.drop_index(op.f("ix_notes_patient_id"), table_name="notes")
    op.drop_table("notes")
