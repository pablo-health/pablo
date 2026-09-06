# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""audit_logs: let the retention purge see what it deletes

``audit_logs`` is protected twice over and the two protections were
never checked against each other. The ``audit_logs_append_only`` trigger
refuses every UPDATE and DELETE unless ``app.allow_audit_purge`` is
armed; the row policy decides which rows a statement can see at all.
The suite proved the trigger lets the purge through. Nothing proved the
policy did, and it did not.

:func:`app.jobs.audit_retention_cron._delete_expired` is not a principal
and has no identity to arm. It sets ``search_path``, arms the purge GUC
and issues one ``DELETE ... WHERE expires_at < :as_of`` spanning every
actor's rows. Under ``rls_audit_actor_access`` alone that statement
compares ``user_id`` against an unset ``app.current_user_id`` — NULL,
so the predicate is NULL for every row and the DELETE matches none of
them. No error, no refusal, rowcount 0, and a cron logging a successful
run having purged nothing. Rows outlive ``expires_at`` indefinitely,
which on this table means keeping access records forever.

The existing ``test_retention_path_delete_succeeds`` passes because it
arms ``app.current_user_id`` to the seeded row's own ``user_id`` before
deleting. The cron does not.

So the purge gets its own permissive policies, gated on the GUC the
trigger already treats as the authorization to delete. Permissive
policies are OR'd, so ``rls_audit_actor_access`` is untouched and keeps
covering every command — this widens, it does not replace, which is
what the previous revision's objection to per-command policies was
about.

No new trust boundary. Anything that can arm ``app.allow_audit_purge``
can already get a DELETE past the append-only trigger; RLS was hiding
the rows from it by accident rather than by design. An ordinary request
never sets that GUC and sees exactly what it saw before.

SELECT as well as DELETE. ``_count_expired`` backs ``--dry-run`` and
reads through the same predicate, so a DELETE-only fix would leave an
operator asking whether the purge has work being told "none" while the
real run deletes thousands — a worse failure than both reporting zero,
because it corroborates the wrong answer.

Deliberately NOT ``FOR ALL``: that would admit INSERT and UPDATE under
the purge GUC too, letting a purge-context session forge or rewrite
audit rows. The trigger blocks UPDATE, but the policy should not be the
thing depending on that.

Revision ID: e5c9f2d73a18
Revises: d4b8e1c62f07
Create Date: 2026-08-31
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e5c9f2d73a18"
down_revision: str | Sequence[str] | None = "d4b8e1c62f07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PURGE_ARMED = "current_setting('app.allow_audit_purge', true) = 'on'"


def _current_schema() -> str:
    return op.get_bind().execute(text("SELECT current_schema()")).scalar_one()


def upgrade() -> None:
    schema = _current_schema()
    for name, command in (
        ("rls_audit_purge_delete", "DELETE"),
        ("rls_audit_purge_select", "SELECT"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {name} ON {schema}.audit_logs")
        op.execute(
            f"CREATE POLICY {name} ON {schema}.audit_logs FOR {command} USING ({_PURGE_ARMED})"
        )


def downgrade() -> None:
    schema = _current_schema()
    for name in ("rls_audit_purge_delete", "rls_audit_purge_select"):
        op.execute(f"DROP POLICY IF EXISTS {name} ON {schema}.audit_logs")
