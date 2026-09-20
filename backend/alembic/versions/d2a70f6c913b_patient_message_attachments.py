# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_message_attachments: files on a secure message

One per-tenant table linking a message to a document already on the
patient's chart. No bytes here and no new bucket: an attachment is a
``patient_documents`` row of category ``message``, uploaded through the
same two-phase signed-URL path as everything else on that table, and this
records that one of them was sent on one message.

``patient_id`` is denormalized from the message so the per-patient row
policies apply to these rows directly, the way they do to
``patient_messages``. Two composite foreign keys keep that copy honest,
and they are the two ways it could drift — a link cannot name a message
belonging to one patient and a document belonging to another. Both need a
UNIQUE ``(id, patient_id)`` on the parent to point at, so this revision
adds those to ``patient_messages`` and ``patient_documents``; neither is
redundant with the primary key beside it, both are the target a composite
key names.

``document_id`` is UNIQUE across the table rather than the pair
``(message_id, document_id)``. The rule the send routes enforce is "not
already attached to another message", and the pair cannot express it — it
would admit the same file on two different messages, which is one row of
the chart appearing in two conversations. The single-column constraint is
what makes that a database fact instead of a route convention.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: d2a70f6c913b
Revises: c5e1a9f0d248
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d2a70f6c913b"
down_revision: str | Sequence[str] | None = "c5e1a9f0d248"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    #
    # The two parent constraints first: the table below cannot be created
    # until the keys it points at exist. ``ADD CONSTRAINT`` has no
    # ``IF NOT EXISTS``, so each one is guarded by a catalog lookup rather
    # than by dropping and re-adding — a DROP here would take the child
    # foreign key with it on a re-run.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_patient_messages_id_patient'
                  AND conrelid = 'patient_messages'::regclass
            ) THEN
                ALTER TABLE patient_messages
                    ADD CONSTRAINT uq_patient_messages_id_patient UNIQUE (id, patient_id);
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_patient_documents_id_patient'
                  AND conrelid = 'patient_documents'::regclass
            ) THEN
                ALTER TABLE patient_documents
                    ADD CONSTRAINT uq_patient_documents_id_patient UNIQUE (id, patient_id);
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_message_attachments (
            id           UUID        PRIMARY KEY,
            message_id   UUID        NOT NULL,
            document_id  UUID        NOT NULL,
            patient_id   UUID        NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL,
            CONSTRAINT uq_patient_message_attachments_document UNIQUE (document_id),
            CONSTRAINT fk_patient_message_attachments_message
                FOREIGN KEY (message_id, patient_id)
                REFERENCES patient_messages (id, patient_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_patient_message_attachments_document
                FOREIGN KEY (document_id, patient_id)
                REFERENCES patient_documents (id, patient_id)
                ON DELETE RESTRICT
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_message_attachments_patient_id "
        "ON patient_message_attachments (patient_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_message_attachments_message "
        "ON patient_message_attachments (message_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_message_attachments CASCADE;")
    op.execute(
        "ALTER TABLE patient_documents DROP CONSTRAINT IF EXISTS uq_patient_documents_id_patient;"
    )
    op.execute(
        "ALTER TABLE patient_messages DROP CONSTRAINT IF EXISTS uq_patient_messages_id_patient;"
    )
