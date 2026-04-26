"""drop legacy note columns from therapy_sessions

Removes the soap_note / quality_rating / export_* / note_type columns
from ``therapy_sessions`` after the notes table is the single source of
truth (see pa-0nx.2). No dual-write window: pa-0nx has zero production
users, so we drop directly rather than carry redundant state.

Pre-flight assertion: every TherapySessionRow with a non-empty
``note_content`` must already have a corresponding row in ``notes``
(joined by ``session_id``). The pa-0nx.1 backfill produced this state;
this assertion catches the case where someone landed a NoteRow change
before backfilling.

Revision ID: c8a9d3e4f206
Revises: f1c8d4a92b65
Create Date: 2026-04-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c8a9d3e4f206"
down_revision: str | Sequence[str] | None = "f1c8d4a92b65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Columns to drop from therapy_sessions. The note-flavored fields now
# live on the notes table (created in pa-0nx.1).
_DROPPED_COLUMNS: tuple[str, ...] = (
    "note_type",
    "note_content",
    "note_content_edited",
    "quality_rating",
    "quality_rating_reason",
    "quality_rating_sections",
    "finalized_at",
    "redacted_soap_note",
    "naturalized_soap_note",
    "export_status",
    "export_queued_at",
    "export_reviewed_at",
    "export_reviewed_by",
    "exported_at",
)


def upgrade() -> None:
    bind = op.get_bind()

    # Pre-flight: every session with content must already have a Note.
    missing = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM therapy_sessions ts
            WHERE ts.note_content IS NOT NULL
              AND ts.note_content::text <> '{}'::text
              AND NOT EXISTS (
                  SELECT 1 FROM notes n WHERE n.session_id = ts.id
              )
            """
        )
    ).scalar_one()
    if missing:
        raise RuntimeError(
            f"Refusing to drop legacy note columns: {missing} therapy_sessions row(s) "
            "have non-empty note_content but no matching row in `notes`. "
            "Run the pa-0nx.1 backfill (revision b9d2f7c4e3a8) first, or "
            "investigate the divergence."
        )

    for col in _DROPPED_COLUMNS:
        op.drop_column("therapy_sessions", col)


def downgrade() -> None:
    """Re-add the dropped columns as nullable.

    Data loss on downgrade is accepted: the canonical note state lives
    on the ``notes`` table after pa-0nx.2 lands, and the legacy columns
    are not re-populated. Down is for schema rollback only, not data
    rollback.
    """
    op.add_column(
        "therapy_sessions",
        sa.Column(
            "note_type",
            sa.String(length=30),
            nullable=False,
            server_default="soap",
        ),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column("note_content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column(
            "note_content_edited",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column("quality_rating", sa.Integer(), nullable=True),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column("quality_rating_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column(
            "quality_rating_sections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column(
            "redacted_soap_note",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column(
            "naturalized_soap_note",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column(
            "export_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_queued",
        ),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column("export_queued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column("export_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column("export_reviewed_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "therapy_sessions",
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
    )
