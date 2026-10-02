# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_sites and platform.practice_site_versions: a practice's static website

``practice_sites`` holds one row per practice that has uploaded a website: the
published version visitors are served, the next version number, the current
draft and its preview address (the token's hash only), and who last published.
``practice_site_versions`` holds one row per retained published version, which
is what the practice can roll back to.

Platform-scoped, like ``practice_domains``: a request for a website host is
resolved to a practice and its live version before any tenant is known.

No PHI: a practice's public website, counts, sizes and who published it.

Revision ID: a9d3f6c21e87
Revises: c5f1d7a93e28
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a9d3f6c21e87"
down_revision: str | Sequence[str] | None = "c5f1d7a93e28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.practice_sites (
            practice_id         VARCHAR(128) NOT NULL PRIMARY KEY,
            live_version        INTEGER,
            next_version        INTEGER      NOT NULL DEFAULT 1,
            draft_id            VARCHAR(32),
            draft_file_count    INTEGER,
            draft_bytes         BIGINT,
            draft_uploaded_at   TIMESTAMP WITH TIME ZONE,
            draft_uploaded_by   VARCHAR(128),
            preview_token_hash  VARCHAR(64),
            preview_expires_at  TIMESTAMP WITH TIME ZONE,
            published_at        TIMESTAMP WITH TIME ZONE,
            published_by        VARCHAR(128),
            updated_at          TIMESTAMP WITH TIME ZONE NOT NULL
        );
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_practice_sites_preview_token_hash
            ON platform.practice_sites (preview_token_hash)
            WHERE preview_token_hash IS NOT NULL;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.practice_site_versions (
            practice_id   VARCHAR(128) NOT NULL
                REFERENCES platform.practice_sites (practice_id) ON DELETE CASCADE,
            version       INTEGER      NOT NULL,
            file_count    INTEGER      NOT NULL,
            total_bytes   BIGINT       NOT NULL,
            published_at  TIMESTAMP WITH TIME ZONE NOT NULL,
            published_by  VARCHAR(128) NOT NULL,
            PRIMARY KEY (practice_id, version)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.practice_site_versions;")
    op.execute("DROP TABLE IF EXISTS platform.practice_sites;")
