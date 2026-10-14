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
  * the reader has access to that client (``has_patient_access``).

INSERT, UPDATE and DELETE are untouched: ``rls_audit_actor_access`` still
decides who may write a row in whose name, and the append-only trigger
still refuses every UPDATE and DELETE outside the retention purge.

**No name the policy reaches may depend on the caller's search_path.** It
is evaluated wherever an audit row is read, which includes the RETURNING
of an INSERT made from a session not scoped to this tenant. So this also
gives ``has_patient_access`` a SQL-standard ``RETURN`` body. Its old
``$$`` body found ``patient_clinicians`` through the caller's search_path;
the new one is bound to its own schema's table when it is created, and
stays bound across a schema rename. Every existing caller already runs
inside the tenant's search_path, where the function answers exactly as
before. It stays the one definition of "has access": the policy calls it
rather than repeating it.

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
        f"AND {schema}.has_patient_access("
        "patient_id, current_setting('app.current_user_id', true)) "
        "AND EXISTS (SELECT 1 FROM platform.practices p "
        "WHERE p.owner_user_id::text = current_setting('app.current_user_id', true) "
        f"AND to_regclass(p.schema_name || '.audit_logs') = '{schema}.audit_logs'::regclass)"
    )


def has_patient_access_ddl(schema: str) -> str:
    """``has_patient_access`` with a SQL-standard body bound to ``schema``.

    A ``RETURN`` body is parsed when the function is created and stored as
    references to the objects themselves, not as text. So it reads this
    schema's ``patient_clinicians`` whatever the caller's search_path, and
    it still does after the schema is renamed — which a qualified name in a
    ``$$`` body (or a pinned ``SET search_path``) would not: both are text,
    and would go on naming the old schema.
    """
    return (
        f"CREATE OR REPLACE FUNCTION {schema}.has_patient_access("  # noqa: S608
        "p_patient_id uuid, p_user_id character varying) "
        "RETURNS boolean LANGUAGE sql STABLE "
        f"RETURN EXISTS (SELECT 1 FROM {schema}.patient_clinicians pc "
        "WHERE pc.patient_id = p_patient_id "
        "AND pc.user_id::text = p_user_id "
        "AND (pc.expires_at IS NULL OR pc.expires_at > now()))"
    )


#: The definition this revision replaces, for the downgrade.
_SEARCH_PATH_HAS_PATIENT_ACCESS = """
    CREATE OR REPLACE FUNCTION {schema}.has_patient_access(
        p_patient_id uuid,
        p_user_id character varying
    ) RETURNS boolean
    LANGUAGE sql
    STABLE
    AS $$
        SELECT EXISTS (
            SELECT 1 FROM patient_clinicians
            WHERE patient_id = p_patient_id
              AND user_id::text = p_user_id
              AND (expires_at IS NULL OR expires_at > now())
        );
    $$
"""


def _current_schema() -> str:
    return op.get_bind().execute(text("SELECT current_schema()")).scalar_one()


def upgrade() -> None:
    schema = _current_schema()
    op.execute(has_patient_access_ddl(schema))
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {schema}.audit_logs")
    op.execute(
        f"CREATE POLICY {POLICY_NAME} ON {schema}.audit_logs "
        f"FOR SELECT USING ({owner_client_read_using(schema)})"
    )


def downgrade() -> None:
    schema = _current_schema()
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {schema}.audit_logs")
    op.execute(_SEARCH_PATH_HAS_PATIENT_ACCESS.format(schema=schema))
