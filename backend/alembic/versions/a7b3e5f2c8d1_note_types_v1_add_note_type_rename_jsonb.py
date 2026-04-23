"""note-types v1: add note_type column, rename soap_note JSONB columns

Adds ``therapy_sessions.note_type`` (default ``'soap'``) and renames the two
primary note JSONB columns to generic names so a single column can hold any
note shape (SOAP, DAP, BIRP, Narrative, Meeting...):

* ``soap_note`` → ``note_content``
* ``soap_note_edited`` → ``note_content_edited``

Idempotent — uses ``IF NOT EXISTS`` / column-existence checks so re-runs
against drifted dev databases don't fail (see pa-0rx). PII-redacted siblings
(``redacted_soap_note`` / ``naturalized_soap_note``) are intentionally left
as-is; they're out of scope for this bead.

Revision ID: a7b3e5f2c8d1
Revises: f1c8d4a92b65
Create Date: 2026-04-22
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a7b3e5f2c8d1"
down_revision: str | Sequence[str] | None = "f1c8d4a92b65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE therapy_sessions
            ADD COLUMN IF NOT EXISTS note_type VARCHAR NOT NULL DEFAULT 'soap';
        """
    )

    # Rename guarded on old-column existence so drifted DBs (already renamed,
    # or partially applied) don't error out.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'therapy_sessions'
                  AND column_name = 'soap_note'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'therapy_sessions'
                  AND column_name = 'note_content'
            ) THEN
                ALTER TABLE therapy_sessions
                    RENAME COLUMN soap_note TO note_content;
            END IF;

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'therapy_sessions'
                  AND column_name = 'soap_note_edited'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'therapy_sessions'
                  AND column_name = 'note_content_edited'
            ) THEN
                ALTER TABLE therapy_sessions
                    RENAME COLUMN soap_note_edited TO note_content_edited;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'therapy_sessions'
                  AND column_name = 'note_content_edited'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'therapy_sessions'
                  AND column_name = 'soap_note_edited'
            ) THEN
                ALTER TABLE therapy_sessions
                    RENAME COLUMN note_content_edited TO soap_note_edited;
            END IF;

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'therapy_sessions'
                  AND column_name = 'note_content'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'therapy_sessions'
                  AND column_name = 'soap_note'
            ) THEN
                ALTER TABLE therapy_sessions
                    RENAME COLUMN note_content TO soap_note;
            END IF;
        END $$;
        """
    )

    op.execute(
        "ALTER TABLE therapy_sessions DROP COLUMN IF EXISTS note_type;"
    )
