# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""audit_logs: an empty user_id is not an identity

Disarming a principal sets its GUC to ``''`` rather than dropping it —
``_disarm_other_principal`` and the ``after_begin`` listener both write
``user_id or ""``. So ``app.current_user_id`` is ``''`` on every
patient-request transaction and ``app.current_patient_id`` is ``''`` on
every clinician one. That is the ordinary state of every request.

Two docstrings defend that choice on the grounds that the ``::text``-cast
idiom every policy uses treats ``''`` as matching no row. That is true of
every other policy in the schema — every other ``user_id`` and
``patient_id`` column is a ``uuid``, and ``''`` fails the cast, so the
comparison is an error-free no-match.

``audit_logs.user_id`` is the exception, and the only one:
``character varying(128)``, kept wide because it also carries
non-clinician actors. For it ``''`` is a storable value and ``'' = ''``
is true, which turns the empty id into a bucket every principal shares
on the one table whose entire job is recording who did what:

  * a patient principal reads every ``user_id = ''`` row, though the
    policy comment states it reads nothing at all;
  * a patient principal writes rows attributed to
    ``actor_type = 'clinician'``, because the clinician arm's identity
    check reduces to ``'' = ''``;
  * no legitimate reader ever sees them afterwards — no real principal's
    GUC is ``''`` — and the retention purge runs with the GUC unset
    (NULL, not ``''``), so they never expire either.

A CHECK constraint rather than a third policy arm, for three reasons. It
holds however the GUCs are cleared, so it does not depend on getting the
disarm story right. It cannot be dropped by a future policy edit, which
is the failure mode this table has already had once. And it is one
column in one table — every other principal column is a ``uuid`` and is
already closed by the cast.

The constraint is validated rather than ``NOT VALID``: no code path
writes an empty ``user_id`` today, so there should be nothing to fail
on, and a tenant that *does* hold such a row is exactly the thing worth
learning about loudly during a migration rather than quietly never.

Revision ID: d4b8e1c62f07
Revises: b2d6f83a4c19
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d4b8e1c62f07"
down_revision: str | Sequence[str] | None = "b2d6f83a4c19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "audit_logs_user_id_not_empty"


def _current_schema() -> str:
    return op.get_bind().execute(text("SELECT current_schema()")).scalar_one()


def upgrade() -> None:
    schema = _current_schema()
    op.execute(f"ALTER TABLE {schema}.audit_logs DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(
        f"ALTER TABLE {schema}.audit_logs ADD CONSTRAINT {_CONSTRAINT} CHECK (user_id <> '')"
    )


def downgrade() -> None:
    schema = _current_schema()
    op.execute(f"ALTER TABLE {schema}.audit_logs DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
