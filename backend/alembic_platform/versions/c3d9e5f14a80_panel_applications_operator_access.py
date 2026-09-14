# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Operator access to panel_applications, for the chain that now builds it

``platform.panel_applications`` is row-scoped to the clinician who owns the
application. The operator who actually files those applications reaches across
clinicians through a second policy naming ``pablo_credentialing_ops`` — a role
created out of band, NOBYPASSRLS, able to reach this table and nothing else.

That grant and policy already exist wherever the tenant chain has run:
``c4d81e6a2f09`` creates them, inside a ``DO`` block that fires only when the
role is present, so self-hosted installs and CI are unaffected. This revision is
not a change to any running database — it is the same DDL, in the chain that will
build this schema once the tenant chain stops carrying platform objects.

**Why it cannot come from the template instead.** The template is a pg_dump, and
this DDL is invisible to one twice over: ``--no-privileges`` strips GRANTs, and
the capture runs against a container where ``pablo_credentialing_ops`` does not
exist, so the role-targeted policy is never created to be dumped. Role-conditional
DDL has to live in a revision. Without this, a fresh install would get the table
with its owner policy and no operator access at all, and the operator surface
would read zero rows from a table that has them — RLS filtering silently, which
is what it is for.

Idempotent, and safe to run before or after ``c4d81e6a2f09``: both spell the same
``DROP POLICY IF EXISTS`` then ``CREATE POLICY``.

Revision ID: c3d9e5f14a80
Revises: b2c8d4e06f31
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "c3d9e5f14a80"
down_revision: str | Sequence[str] | None = "b2c8d4e06f31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Grant the operator role its way across, when that role exists."""
    # The role name is a literal rather than interpolated: it is a constant, and
    # spelling it out keeps this a plain string a reader and a linter can both
    # follow. Mirrors c4d81e6a2f09 exactly.
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
    """Drop the operator policy, leaving the grants.

    The policy is this revision's to remove. The grants are not: the role is
    created and managed out of band, and revoking a schema-level USAGE it may
    have been given for other reasons is not this migration's business.
    """
    op.execute("DROP POLICY IF EXISTS panel_applications_operator ON platform.panel_applications")
