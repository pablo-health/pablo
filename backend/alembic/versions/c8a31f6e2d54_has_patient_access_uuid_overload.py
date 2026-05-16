"""has_patient_access switched to UUID patient_id

Revision ID: c8a31f6e2d54
Revises: b7e25c1d8a4f
Create Date: 2026-05-16

The b7e25c1d8a4f migration converted ``patient_clinicians.patient_id``
from ``VARCHAR(128)`` to native ``UUID``. The pre-existing
``has_patient_access(VARCHAR, VARCHAR)`` function defined in
777b846ab944 was not updated — its body's
``WHERE patient_id = p_patient_id`` then tries ``uuid = varchar`` at
runtime and Postgres raises::

    operator does not exist: uuid = character varying

This breaks every read that goes through the access predicate, including
``GET /api/patients/{id}/notes`` and any RLS-gated query whose policy
inlines the function (chat_conversations, sessions, appointments, etc.).

Fix: drop the legacy ``(VARCHAR, VARCHAR)`` overload and create a single
``(UUID, VARCHAR)`` overload that matches the column types in
``patient_clinicians`` after b7e25c1d8a4f. RLS USING-clauses installed
by ``app.db.enable_rls_on_schema`` pass the ``patient_id`` UUID column
directly, so they resolve cleanly. Python repo callers — five files
under ``backend/app/repositories/postgres/`` — were updated in the same
commit to pass ``:pid::uuid`` so psycopg2's text-typed bind parameter
gets cast at the call site (Postgres has no implicit text→uuid cast).

Idempotent under per-tenant fan-out: ``DROP FUNCTION IF EXISTS`` and
``CREATE OR REPLACE`` are both safe to replay.
"""

from collections.abc import Sequence

from alembic import op

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "c8a31f6e2d54"
down_revision: str | Sequence[str] | None = "b7e25c1d8a4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS has_patient_access(VARCHAR, VARCHAR)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION has_patient_access(
            p_patient_id UUID,
            p_user_id    VARCHAR
        ) RETURNS BOOLEAN
        LANGUAGE sql
        STABLE
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM patient_clinicians
                WHERE patient_id = p_patient_id
                  AND user_id    = p_user_id
                  AND (expires_at IS NULL OR expires_at > now())
            );
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS has_patient_access(UUID, VARCHAR)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION has_patient_access(
            p_patient_id VARCHAR,
            p_user_id    VARCHAR
        ) RETURNS BOOLEAN
        LANGUAGE sql
        STABLE
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM patient_clinicians
                WHERE patient_id = p_patient_id
                  AND user_id    = p_user_id
                  AND (expires_at IS NULL OR expires_at > now())
            );
        $$
        """
    )
