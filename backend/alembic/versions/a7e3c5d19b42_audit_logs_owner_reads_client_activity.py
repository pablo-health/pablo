# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""audit_logs: the practice owner reads the activity on their clients' records

Until now an actor read only the rows they were the actor of. That kept a
clinician out of everyone else's trail, and it also kept the practice out
of the one review it is responsible for: who touched a client's record.
A client's own portal actions are written with the client as the actor,
so no clinician could read them at all.

This adds a second, SELECT-only policy. Policies are OR'd, so it widens
reads and nothing else: a row is also visible when

  * it is about a client (``patient_id`` is set),
  * the reader is the practice's owner — ``platform.practices.owner_user_id``
    for the practice whose schema holds this table, and
  * the reader holds a live grant to that client in ``patient_clinicians``
    — the same test ``has_patient_access`` makes.

Every name in the policy is schema-qualified. It is evaluated wherever an
audit row is read, which includes the RETURNING of an INSERT made from a
session that is not scoped to this tenant, so it cannot depend on the
caller's search_path. That is why it spells the grant check out instead
of calling ``has_patient_access``, whose body finds ``patient_clinicians``
unqualified.

INSERT, UPDATE and DELETE are untouched: ``rls_audit_actor_access`` still
decides who may write a row in whose name, and the append-only trigger
still refuses every UPDATE and DELETE outside the retention purge.

The practice is found through the table's own schema rather than a schema
name written into the policy. The tenant template is captured from the
``practice`` schema and rewrites only ``practice.``-qualified names, so a
literal schema name would land in every new tenant as ``'practice'`` and
match nothing. ``'<schema>.audit_logs'::regclass`` is deparsed qualified,
so it is rewritten like any other reference.

``enable_rls_on_schema`` creates the identical policy on its next run;
both drop before creating, so re-running either is a no-op.

Revision ID: a7e3c5d19b42
Revises: c49085e0d415
Create Date: 2026-10-13
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a7e3c5d19b42"
down_revision: str | Sequence[str] | None = "c49085e0d415"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "rls_audit_owner_client_read"


def owner_client_read_using(schema: str) -> str:
    """The policy's USING clause for the audit table in ``schema``.

    ``schema`` is ``current_schema()`` of the migration's own connection,
    never input.
    """
    return (
        "patient_id IS NOT NULL "  # noqa: S608
        f"AND EXISTS (SELECT 1 FROM {schema}.patient_clinicians pc "
        f"WHERE pc.patient_id = {schema}.audit_logs.patient_id "
        "AND pc.user_id::text = current_setting('app.current_user_id', true) "
        "AND (pc.expires_at IS NULL OR pc.expires_at > now())) "
        "AND EXISTS (SELECT 1 FROM platform.practices p "
        "WHERE p.owner_user_id::text = current_setting('app.current_user_id', true) "
        f"AND to_regclass(p.schema_name || '.audit_logs') = '{schema}.audit_logs'::regclass)"
    )


def _current_schema() -> str:
    return op.get_bind().execute(text("SELECT current_schema()")).scalar_one()


def upgrade() -> None:
    schema = _current_schema()
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {schema}.audit_logs")
    op.execute(
        f"CREATE POLICY {POLICY_NAME} ON {schema}.audit_logs "
        f"FOR SELECT USING ({owner_client_read_using(schema)})"
    )


def downgrade() -> None:
    schema = _current_schema()
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {schema}.audit_logs")
