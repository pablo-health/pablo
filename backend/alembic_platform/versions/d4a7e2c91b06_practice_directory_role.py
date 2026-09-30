# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The pablo_practice_directory role, which owns the practice client directory

Matching an outside record to a chart has to see every chart in the practice,
not only the ones the acting clinician holds a grant on. The tenant function
that does it (``practice_client_directory``, see ``app.db.practice_directory``)
is owned by this role, which may read a handful of columns and nothing else.

The role is cluster-wide, so it is made here, once, rather than per practice:

  * ``NOLOGIN NOBYPASSRLS`` — nobody connects as it, and it is policed like
    everyone else; a row policy naming it is the only way it sees a row.
  * The role running this is granted it ``WITH INHERIT FALSE, SET TRUE``. It
    can hand the role ownership of each practice's function, and it never
    acquires the role's privileges or policies on its own reads.

**It fails rather than skipping.** Without the role, matching stays limited to
each clinician's own charts and a colleague's client quietly becomes a second
chart. So a migrator without ``CREATEROLE`` stops here, naming the role and the
paragraph that says how to create it by hand. The checks at the end refuse a
role that could log in or bypass RLS, and a membership that inherits.

A superuser passes the membership check trivially, and is not RLS-bound anyway.

**Only the role running this is granted it.** Each practice's function is
handed to the directory role when that practice is provisioned, and
provisioning runs as the user the app connects as. When migrations run as a
different user (``postgres``, say), the app's user needs the same grant, made
by hand: ``GRANT pablo_practice_directory TO <app user> WITH INHERIT FALSE, SET
TRUE``. Without it provisioning stops, naming the role.

Revision ID: d4a7e2c91b06
Revises: b5f1c8d3a702
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "d4a7e2c91b06"
down_revision: str | Sequence[str] | None = "b5f1c8d3a702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: The whole revision. A module constant so a test can run it as a user who
#: may not create or be granted the role, and see it fail with the fix. The
#: role name is a literal, as in c3d9e5f14a80: a constant, spelled out.
ENSURE_ROLE_SQL = """
        DO $$
        DECLARE
          how_to constant text :=
            ' See docs/SELF_HOSTING_HIPAA_GUIDE.md, Database roles, to create it by hand.';
          me_super boolean;
        BEGIN
          IF current_setting('server_version_num')::int < 160000 THEN
            RAISE EXCEPTION USING MESSAGE =
              'Pablo needs PostgreSQL 16 or later for the role pablo_practice_directory.'
              || how_to;
          END IF;

          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pablo_practice_directory') THEN
            BEGIN
              CREATE ROLE pablo_practice_directory NOLOGIN NOBYPASSRLS;
            EXCEPTION WHEN insufficient_privilege THEN
              RAISE EXCEPTION USING MESSAGE =
                'Could not create the database role pablo_practice_directory: the role '
                'running migrations lacks CREATEROLE.' || how_to;
            END;
          END IF;

          SELECT rolsuper INTO me_super FROM pg_roles WHERE rolname = current_user;

          -- Granted again when it already inherits, too: a membership made by
          -- createrole_self_grant or by hand may carry INHERIT TRUE, and a
          -- grant from the same grantor replaces its options.
          IF NOT pg_has_role(current_user, 'pablo_practice_directory', 'SET')
             OR (NOT me_super
                 AND pg_has_role(current_user, 'pablo_practice_directory', 'USAGE')) THEN
            BEGIN
              EXECUTE 'GRANT pablo_practice_directory TO ' || quote_ident(current_user)
                   || ' WITH INHERIT FALSE, SET TRUE';
            EXCEPTION WHEN insufficient_privilege THEN
              RAISE EXCEPTION USING MESSAGE =
                'The role running migrations cannot be granted pablo_practice_directory.'
                || how_to;
            END;
          END IF;

          IF EXISTS (
            SELECT 1 FROM pg_roles
            WHERE rolname = 'pablo_practice_directory'
              AND (rolcanlogin OR rolbypassrls OR rolsuper)
          ) THEN
            RAISE EXCEPTION USING MESSAGE =
              'pablo_practice_directory must be NOLOGIN, NOBYPASSRLS and not a superuser.'
              || how_to;
          END IF;

          -- Still inheriting means another grantor's membership carries
          -- INHERIT TRUE, which this role cannot change.
          IF NOT me_super
             AND pg_has_role(current_user, 'pablo_practice_directory', 'USAGE') THEN
            RAISE EXCEPTION USING MESSAGE =
              'The role running migrations inherits pablo_practice_directory, which would '
              'widen its own reads of patients. Grant it WITH INHERIT FALSE.' || how_to;
          END IF;
        END
        $$
"""


def upgrade() -> None:
    op.execute(ENSURE_ROLE_SQL)


def downgrade() -> None:
    """Leave the role.

    It is cluster-wide: another database on the same server may use it, and
    every practice schema's directory function is owned by it until the
    tenant chain is rolled back too. Dropping it is an operator's decision.
    """
