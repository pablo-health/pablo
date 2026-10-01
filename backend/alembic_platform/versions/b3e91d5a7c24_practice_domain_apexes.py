# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_domain_apexes: the registrable domains a practice's hosts sit under

One row per registrable domain (``example.co.uk`` for
``portal.example.co.uk``), keyed on it, so a domain and every host under it
belong to one practice. The row carries what is proved or set up once per
domain rather than once per host: the token for the ownership TXT record at
``_pablo-verify.<apex>`` and when it was last found, and the state and DKIM
tokens of the domain's email sending identity.

``practice_domains`` gains ``cert_auth_value``: the per-host value of the
``_acme-challenge`` CNAME a certificate is authorised with, the part before
``.authorize.certificatemanager.goog``.

On a fresh install the baseline has already built both at their current shape
and every statement here is a no-op, the same as
``f8c2a61d4e97_practice_domains``. Existing hosts get their domain row the
first time the practice adds a host under it or checks its DNS.

No PHI: public domain names, a public verification token and setup state.

Revision ID: b3e91d5a7c24
Revises: f8c2a61d4e97
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b3e91d5a7c24"
down_revision: str | Sequence[str] | None = "f8c2a61d4e97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.practice_domain_apexes (
            apex                   VARCHAR(253) NOT NULL PRIMARY KEY,
            practice_id            VARCHAR(128) NOT NULL,
            verify_token           VARCHAR(64)  NOT NULL,
            verified_at            TIMESTAMP WITH TIME ZONE,
            email_identity_status  VARCHAR(20),
            email_dkim_tokens      JSONB,
            created_at             TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at             TIMESTAMP WITH TIME ZONE,
            CONSTRAINT practice_domain_apexes_email_identity_status_check
                CHECK (email_identity_status IN ('pending', 'verified', 'failed'))
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_practice_domain_apexes_practice_id
            ON platform.practice_domain_apexes (practice_id);
        """
    )
    op.execute(
        """
        ALTER TABLE platform.practice_domains
            ADD COLUMN IF NOT EXISTS cert_auth_value VARCHAR(255);
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.practice_domains DROP COLUMN IF EXISTS cert_auth_value;")
    op.execute("DROP TABLE IF EXISTS platform.practice_domain_apexes;")
