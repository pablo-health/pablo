# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice directory role reads four columns of payer_participations

For ``practice_enrollment_status`` (tenant revision ``935993fc0ba8``): the
status of the clinician who held a visit with the claim's payer, which a
biller needs for a colleague's session and the table's own-user policy hides.

The role gets USAGE on ``platform``, SELECT on ``user_id``, ``practice_id``,
``payer_id`` and ``status`` only, and one SELECT policy naming it alone. The
policy admits a row only while a clinician is armed and only when the row's
practice is on the reader's search path. Every directory function pins that
path to ``pg_catalog, <its schema>, pg_temp``, and the role cannot log in, so
a function in one practice sees no other practice's rows. The function also
filters on its own schema; this is the backstop.

The platform template does not carry the policy: the baseline that applies
it runs before ``d4a7e2c91b06`` makes the role, so this revision makes the
policy on a fresh install too (``regen_platform_schema.py`` leaves out any
policy naming a role).

Revision ID: a6248b0a751e
Revises: c3f8a1d6e5b2
Create Date: 2026-11-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "a6248b0a751e"
down_revision: str | Sequence[str] | None = "c3f8a1d6e5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literals, as in d4a7e2c91b06: revisions are not to change under a later
# rename of the constants in app.db.practice_directory.
_ROLE = "pablo_practice_directory"
_POLICY = "rls_practice_directory_read"
_TABLE = "platform.payer_participations"
_COLUMNS = "user_id, practice_id, payer_id, status"


def upgrade() -> None:
    op.execute(f"GRANT USAGE ON SCHEMA platform TO {_ROLE}")
    op.execute(f"GRANT SELECT ({_COLUMNS}) ON {_TABLE} TO {_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    op.execute(
        f"CREATE POLICY {_POLICY} ON {_TABLE} FOR SELECT TO {_ROLE} USING ("
        "coalesce(current_setting('app.current_user_id', true), '') <> '' "
        "AND practice_id = ANY (current_schemas(false)))"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    op.execute(f"REVOKE SELECT ({_COLUMNS}) ON {_TABLE} FROM {_ROLE}")  # noqa: S608 — constants
    op.execute(f"REVOKE USAGE ON SCHEMA platform FROM {_ROLE}")
