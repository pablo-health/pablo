# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""portal sign-in state: invite challenges + the session revocation list

Adds the two per-tenant tables the magic-link flow needs in order to hold
the properties a signed token cannot:

* ``companion_auth_challenges`` — single-use and step-up attempt limiting,
  keyed on the invite token's ``jti``, holding the peppered hash of the
  one-time code.
* ``companion_sessions`` — the server-side revocation list, one row per live
  patient session, keyed on the session token's ``jti``.

Neither carries a ``practice_id``: isolation is the schema location. Neither
primary key is named ``id``, but both carry ``patient_id``, which is enough
for ``enable_rls_on_schema`` to reach them — so both are listed as
not-row-scoped in ``app.db._CORE_NOT_ROW_SCOPED``. See
``PortalInviteChallengeRow`` for why the clinician ``has_patient_access``
policy is the wrong shape here: redemption runs before any principal exists.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema, and a deployment may already carry these tables from a
prior extension that created them with this DDL.

Revision ID: b7e3f0c48d15
Revises: a71c5e09d4b3
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b7e3f0c48d15"
down_revision: str | Sequence[str] | None = "a71c5e09d4b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified
    # table refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS companion_auth_challenges (
            jti          VARCHAR(36)  PRIMARY KEY,
            patient_id   UUID         NOT NULL,
            otp_hash     TEXT         NOT NULL,
            created_at   TIMESTAMPTZ  NOT NULL,
            expires_at   TIMESTAMPTZ  NOT NULL,
            attempts     SMALLINT     NOT NULL DEFAULT 0,
            consumed     BOOLEAN      NOT NULL DEFAULT FALSE
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companion_auth_challenges_patient "
        "ON companion_auth_challenges (patient_id);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS companion_sessions (
            jti               VARCHAR(36)  PRIMARY KEY,
            patient_id        UUID         NOT NULL,
            issued_at         TIMESTAMPTZ  NOT NULL,
            expires_at        TIMESTAMPTZ  NOT NULL,
            chain_started_at  TIMESTAMPTZ  NOT NULL,
            revoked_at        TIMESTAMPTZ
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companion_sessions_patient "
        "ON companion_sessions (patient_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS companion_sessions CASCADE;")
    op.execute("DROP TABLE IF EXISTS companion_auth_challenges CASCADE;")
