"""note_signatures and note_addenda: sign and lock a note, add to it after

``note_signatures`` holds one row per signed version of a note: the body as it
was signed, the signer's name and credentials as they were entered, and — once
the note is unlocked to correct an error — when, by whom and why. A version is
never overwritten; signing again adds the next one.

``note_addenda`` holds what a clinician added to a signed note afterwards,
each with its own signature, append-only and hash-chained.

Both carry ``patient_id`` and ``note_id``; the reconcile pass that follows
every migration gives them the note-child row policy, so each is readable
exactly when its note is. See ``NoteSignatureRow`` / ``NoteAddendumRow``.

Additive only. Idempotent, like every revision in this chain: it is fanned out
once per practice schema.

Revision ID: d5b8e2a71c94
Revises: a3e7c5f19d42
Create Date: 2026-10-05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d5b8e2a71c94"
down_revision: str | Sequence[str] | None = "a3e7c5f19d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS note_signatures (
            id                 UUID        PRIMARY KEY,
            note_id            UUID        NOT NULL
                REFERENCES notes (id) ON DELETE CASCADE,
            patient_id         UUID        NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            version            INTEGER     NOT NULL,
            note_type          VARCHAR(30) NOT NULL,
            note_type_version  INTEGER,
            content            JSONB,
            content_edited     JSONB,
            digest             VARCHAR(64) NOT NULL,
            signed_by          UUID        NOT NULL,
            signer_name        TEXT        NOT NULL,
            signer_credentials TEXT,
            signed_at          TIMESTAMPTZ NOT NULL,
            unlocked_at        TIMESTAMPTZ,
            unlocked_by        UUID,
            unlock_reason      TEXT,
            CONSTRAINT uq_note_signatures_note_version UNIQUE (note_id, version),
            CONSTRAINT ck_note_signatures_unlock_reason
                CHECK ((unlocked_at IS NULL) = (unlock_reason IS NULL))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_note_signatures_note_id ON note_signatures (note_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_note_signatures_patient_id ON note_signatures (patient_id);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS note_addenda (
            id                 UUID        PRIMARY KEY,
            note_id            UUID        NOT NULL
                REFERENCES notes (id) ON DELETE CASCADE,
            patient_id         UUID        NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            text               TEXT        NOT NULL,
            signer_name        TEXT        NOT NULL,
            signer_credentials TEXT,
            digest             VARCHAR(64) NOT NULL,
            prev_digest        VARCHAR(64),
            created_by         UUID        NOT NULL,
            created_at         TIMESTAMPTZ NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_note_addenda_note_id ON note_addenda (note_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_note_addenda_patient_id ON note_addenda (patient_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS note_addenda CASCADE;")
    op.execute("DROP TABLE IF EXISTS note_signatures CASCADE;")
