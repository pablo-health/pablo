# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""booking_links table

Creates ``platform.booking_links``, the registry behind public booking
links (docs/design/public-booking.md). Platform-scoped because slug
resolution must happen before a tenant schema can be selected.

No PHI: slug, owner, public display copy, and duration only. Shares the
``platform`` schema (no RLS) with the other cross-practice tables.

Revision ID: c8d2f4a71e93
Revises: 9deb5409153a
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c8d2f4a71e93"
down_revision: str | Sequence[str] | None = "9deb5409153a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # This revision used to create indexes on a platform table here; the
    # platform chain creates them now, from platform_template.sql. Each was a
    # duplicate — the same table and columns already carried an index under
    # the ORM naming convention, because ``CREATE INDEX IF NOT EXISTS``
    # matches on name and so never noticed the twin standing beside it.
    # alembic_platform b2c8d4e06f31 drops them; leaving the CREATEs here
    # would put them straight back on every fresh install.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.booking_links (
            id               UUID         PRIMARY KEY,
            slug             VARCHAR(64)  NOT NULL UNIQUE,
            user_id          UUID         NOT NULL
                REFERENCES platform.users(id) ON DELETE CASCADE,
            practice_id      VARCHAR(128)
                REFERENCES platform.practices(id) ON DELETE CASCADE,
            host_name        VARCHAR(255) NOT NULL,
            title            VARCHAR(255) NOT NULL,
            description      TEXT,
            duration_minutes INTEGER      NOT NULL
                CONSTRAINT ck_booking_links_duration
                CHECK (duration_minutes BETWEEN 5 AND 480),
            session_type     VARCHAR(20)  NOT NULL DEFAULT 'individual',
            is_active        BOOLEAN      NOT NULL DEFAULT true,
            created_at       TIMESTAMPTZ  NOT NULL,
            updated_at       TIMESTAMPTZ  NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.booking_links")
