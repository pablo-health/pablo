# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_email_senders: who a practice's client email is from

One row per practice that has changed the sender name, the mailbox name on its
own domain, or the reply-to address of the email its clients receive. Every
column but the key and the timestamps is nullable, and ``NULL`` means the
default; no row means all three defaults.

No PHI: a practice's name for itself, a mailbox name, and a staff address.

**Written idempotently, and expected to be a no-op** on the paths that
materialise the platform tables from the models before this chain runs — the
same reasoning as ``d1c7b94e3a26_portal_practice_slugs``.

Revision ID: b7e2c4f9a031
Revises: c5f1d7a93e28
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b7e2c4f9a031"
down_revision: str | Sequence[str] | None = "c5f1d7a93e28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.practice_email_senders (
            practice_id        VARCHAR(128) NOT NULL PRIMARY KEY,
            sender_name        VARCHAR(100),
            sender_local_part  VARCHAR(64),
            reply_to           VARCHAR(254),
            updated_at         TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_by         VARCHAR(128)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.practice_email_senders;")
