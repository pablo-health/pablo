# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Move panel_applications from each practice schema to platform, with RLS

Pablo runs the panel applications, so the surface that reads them most is an
operator working across every practice at once. As a per-tenant table that
made the board a catalog scan plus a UNION with one branch per schema — on an
environment with a hundred-odd practices, a great deal of work to answer a
question about a few dozen rows. On platform it is one indexed query.

The isolation is kept rather than traded away. RLS is enabled here with the
same ``app.current_user_id`` predicate the practice schemas use, so a clinician
still sees only her own applications and the database is still what enforces
it. This is the first platform table with row security; nothing prevented it
before, it simply had not been needed.

The operator reaches across clinicians through a second policy naming
``pablo_credentialing_ops`` — created out of band, NOBYPASSRLS, and able to
reach this table and nothing else. The policy is created only when that role
exists, so self-hosted installs and CI are unaffected.

SAFE TO RUN NOW AND ONLY NOW: the per-tenant table has never held a row. It was
created by ``e7c9b21d4a86`` and nothing in the product could write to it —
``app.credentialing.panels`` is read-only by design and the operator surface
that writes did not exist yet. So this is a move with no data, no backfill and
no window where a row could be lost. Once the concierge pilot files anything,
the same change becomes a per-schema data migration.

Revision ID: c4d81e6a2f09
Revises: f4a2c8e91d37
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c4d81e6a2f09"
down_revision: str | Sequence[str] | None = "f4a2c8e91d37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- the per-tenant table goes ------------------------------------------
    # Unqualified, so it resolves through the search_path this migration runs
    # under — once per practice schema, which is how it was created.
    op.execute("DROP TABLE IF EXISTS panel_applications")

    # --- the platform table arrives -----------------------------------------
    # Guarded because this migration runs once per schema and the platform
    # table is a single object: the first pass creates it, the rest no-op.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.panel_applications (
            id uuid NOT NULL PRIMARY KEY,
            user_id uuid NOT NULL,
            practice_id character varying(128) NOT NULL,
            payer_id uuid NOT NULL,
            status character varying(24) NOT NULL DEFAULT 'researching',
            action_owner character varying(16) NOT NULL DEFAULT 'pablo',
            due_at timestamp with time zone,
            awaiting text,
            reference character varying(80),
            submitted_at timestamp with time zone,
            effective_at timestamp with time zone,
            notes text,
            created_at timestamp with time zone NOT NULL,
            updated_at timestamp with time zone NOT NULL,
            CONSTRAINT ck_panel_applications_status CHECK (status IN (
                'researching', 'caqh_ready', 'submitted', 'in_review',
                'info_requested', 'contract_received', 'effective',
                'closed_panel_appeal', 'denied', 'recredentialing'
            )),
            CONSTRAINT ck_panel_applications_action_owner CHECK (
                action_owner IN ('pablo', 'therapist')
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_panel_applications_user_id "
        "ON platform.panel_applications (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_panel_applications_practice_id "
        "ON platform.panel_applications (practice_id)"
    )

    # --- row security, the point of doing this properly ---------------------
    # FORCE as well as ENABLE: without FORCE the table owner is exempt, and the
    # app connects as the owner. The practice schemas force it for the same
    # reason.
    op.execute("ALTER TABLE platform.panel_applications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform.panel_applications FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS rls_panel_application_owner ON platform.panel_applications")
    op.execute(
        """
        CREATE POLICY rls_panel_application_owner ON platform.panel_applications
        USING (user_id::text = current_setting('app.current_user_id', true))
        WITH CHECK (user_id::text = current_setting('app.current_user_id', true))
        """
    )

    # --- and the operator's way across, when that role exists ---------------
    # The role name is a literal below rather than interpolated: it is a
    # constant, and spelling it out keeps this a plain string the linter can
    # read as one.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pablo_credentialing_ops') THEN
            EXECUTE 'GRANT USAGE ON SCHEMA platform TO pablo_credentialing_ops';
            EXECUTE 'GRANT SELECT, INSERT, UPDATE ON platform.panel_applications '
                 || 'TO pablo_credentialing_ops';
            EXECUTE 'DROP POLICY IF EXISTS panel_applications_operator '
                 || 'ON platform.panel_applications';
            EXECUTE 'CREATE POLICY panel_applications_operator '
                 || 'ON platform.panel_applications FOR ALL TO pablo_credentialing_ops '
                 || 'USING (true) WITH CHECK (true)';
          END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    # Puts the per-tenant table back, empty, and leaves the platform one in
    # place: dropping it would be the only destructive step in this migration
    # and there is no version of this change where losing rows is correct.
    op.create_table(
        "panel_applications",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("payer_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="researching"),
        sa.Column("action_owner", sa.String(16), nullable=False, server_default="pablo"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("awaiting", sa.Text(), nullable=True),
        sa.Column("reference", sa.String(80), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_panel_applications_user_id", "panel_applications", ["user_id"])
    op.create_index("ix_panel_applications_payer_id", "panel_applications", ["payer_id"])
