# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_message_threads and patient_messages tables

Adds the two per-tenant tables behind secure patient messaging: a thread
envelope and the messages in it. Storage and DDL only — the routes that
read and write them ship in the same change but touch no other schema.

``patient_id`` is UUID on both so the per-tenant ``has_patient_access``
policy applies to these rows directly, matching the other per-patient chart
tables. It is denormalized onto ``patient_messages`` deliberately: every
per-patient policy keys on a ``patient_id`` column, so carrying it here
means the message table needs no bespoke policy branch. The composite
foreign key to ``(id, patient_id)`` is what keeps that copy honest — hence
the unique constraint on the parent, which is not redundant with its
primary key but is the target the composite key names.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema, and a deployment may already carry these tables from a
prior extension that created them with this DDL.

Revision ID: a71c5e09d4b3
Revises: c9f4a1d78b02
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a71c5e09d4b3"
down_revision: str | Sequence[str] | None = "c9f4a1d78b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_message_threads (
            id               UUID         PRIMARY KEY,
            patient_id       UUID         NOT NULL,
            subject          VARCHAR(200),
            status           VARCHAR(16)  NOT NULL,
            created_at       TIMESTAMPTZ  NOT NULL,
            last_message_at  TIMESTAMPTZ  NOT NULL,
            CONSTRAINT ck_patient_message_threads_status
                CHECK (status IN ('open','closed')),
            CONSTRAINT uq_patient_message_threads_id_patient UNIQUE (id, patient_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_message_threads_patient_id "
        "ON patient_message_threads (patient_id);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_messages (
            id          UUID         PRIMARY KEY,
            thread_id   UUID         NOT NULL,
            patient_id  UUID         NOT NULL,
            sender      VARCHAR(16)  NOT NULL,
            body        TEXT         NOT NULL,
            created_at  TIMESTAMPTZ  NOT NULL,
            read_at     TIMESTAMPTZ,
            CONSTRAINT ck_patient_messages_sender
                CHECK (sender IN ('patient','clinician','practice')),
            CONSTRAINT fk_patient_messages_thread
                FOREIGN KEY (thread_id, patient_id)
                REFERENCES patient_message_threads (id, patient_id)
                ON DELETE CASCADE
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_messages_patient_id "
        "ON patient_messages (patient_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_messages_thread_created "
        "ON patient_messages (thread_id, created_at);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_messages CASCADE;")
    op.execute("DROP TABLE IF EXISTS patient_message_threads CASCADE;")
