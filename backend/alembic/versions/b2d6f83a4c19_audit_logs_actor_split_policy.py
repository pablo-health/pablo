# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""audit_logs: split the row policy by who acted

``audit_logs`` carries ``user_id``, so it has been taking the ordinary
direct-ownership policy: a row is visible and writable when its
``user_id`` matches ``app.current_user_id``. That is right for a
clinician, and wrong for a patient acting on their own record — a
patient principal arms ``app.current_patient_id`` and never the user
GUC, so under the NOBYPASSRLS role every audit row their own action
produced was refused at INSERT.

Refused audit writes are the one failure this table cannot have. The
action still happens; nothing records it; and § 164.312(b) is a record
of accesses, not of the accesses we found convenient to store.

So the policy splits on ``actor_type``, added by ``a91c5d3e7b28``:

  * anything that is not a patient keeps exactly the predicate it had
    before — which is what leaves clinician rows, the anonymous rows the
    public booking surface writes, and system rows behaving identically;
  * a patient actor is checked against the patient GUC, and against
    their own id, so one patient cannot write a row in another's name.

Reads stay clinician-side: a clinician sees rows they are the actor of,
plus patient-actor rows for patients they treat (``has_patient_access``,
the same predicate the rest of the patient surfaces use). A patient
principal reads nothing here. Showing someone their own audit trail is a
surface in its own right, and this migration does not open it.

It stays one ALL-command policy, like the one it replaces: giving SELECT
and INSERT their own policies would leave UPDATE and DELETE with none,
and a command with no policy matches no rows rather than erroring — which
would turn a tampering attempt into a silent no-op and strand the
retention purge.

The policy is created here rather than left to the next provisioning
pass so there is no window in which the table has RLS forced with no
policy — that is a silent deny-all, and on this table it would mean
dropping audit rows rather than failing loudly.

``enable_rls_on_schema`` creates the identical policy on its next run;
both sides drop before creating, so re-running either is a no-op.

The append-only triggers block UPDATE, DELETE and TRUNCATE — not ALTER
and not policy DDL — so they are unaffected.

Revision ID: b2d6f83a4c19
Revises: e4f9a0c7b52d
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b2d6f83a4c19"
down_revision: str | Sequence[str] | None = "e4f9a0c7b52d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INSERT_CHECK = (
    "(actor_type IS DISTINCT FROM 'patient' AND "
    "user_id::text = current_setting('app.current_user_id', true)) OR "
    "(actor_type = 'patient' AND "
    "user_id::text = current_setting('app.current_patient_id', true))"
)

_SELECT_USING = (
    "user_id::text = current_setting('app.current_user_id', true) OR "
    "(actor_type = 'patient' AND has_patient_access("
    "  patient_id, current_setting('app.current_user_id', true)"
    "))"
)

_LEGACY_USER_ISOLATION = "user_id::text = current_setting('app.current_user_id', true)"


def _current_schema() -> str:
    return op.get_bind().execute(text("SELECT current_schema()")).scalar_one()


def upgrade() -> None:
    schema = _current_schema()
    op.execute(f"DROP POLICY IF EXISTS rls_user_isolation ON {schema}.audit_logs")
    op.execute(f"DROP POLICY IF EXISTS rls_audit_actor_access ON {schema}.audit_logs")
    op.execute(
        f"CREATE POLICY rls_audit_actor_access ON {schema}.audit_logs "
        f"USING ({_SELECT_USING}) WITH CHECK ({_INSERT_CHECK})"
    )


def downgrade() -> None:
    schema = _current_schema()
    op.execute(f"DROP POLICY IF EXISTS rls_audit_actor_access ON {schema}.audit_logs")
    op.execute(
        f"CREATE POLICY rls_user_isolation ON {schema}.audit_logs "
        f"USING ({_LEGACY_USER_ISOLATION}) WITH CHECK ({_LEGACY_USER_ISOLATION})"
    )
