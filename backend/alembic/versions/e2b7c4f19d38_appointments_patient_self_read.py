# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Give existing tenants the patient read arm on ``appointments``.

``appointments`` is now registered in ``PATIENT_READABLE_TABLES``, so a
patient principal may select their own rows. Registration alone only
reaches tenants provisioned *after* it: ``enable_rls_on_schema`` runs at
provisioning time, so a schema's policies reflect what the code said when
that schema was created. Every tenant that already exists would keep the
clinician-only shape.

The failure that causes is quiet rather than loud. ``appointments`` is
clinician-scoped on ``user_id``; a patient request never arms
``app.current_user_id``, and the ``pablo`` role is NOBYPASSRLS. So a
patient asking for their own appointments on an unhealed tenant gets zero
rows back — an empty calendar, not an error — while the same request on a
tenant provisioned yesterday works fine. That is the worst shape of bug to
find from the outside, because nothing anywhere reports a failure.

``enable_rls_on_schema`` is idempotent and self-healing by construction
(DROP POLICY IF EXISTS before each CREATE; not-row-scoped tables get RLS
disabled each run), so this migration just invokes it and lets it true the
schema up to the shape provisioning would build today. Same approach, and
the same reasoning, as ``b6e1d8c4a7f2_heal_tenant_rls_policies``: the
semantics wanted here are "match what provisioning builds now", not a
frozen snapshot of one CREATE POLICY statement — and re-running
provisioning's own function is the only version of that which cannot drift
away from it.

The per-tenant fan-out applies this to every practice schema. The
deploy-time default path is a no-op: ``enable_rls_on_schema`` skips the
``practice`` template schema by design, and provisioning re-applies RLS
when a tenant is cloned from it. ``tenant_template.sql`` is therefore
unchanged by this revision, which is expected rather than an omission.

Revision ID: e2b7c4f19d38
Revises: a1f6c30b9d47
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "e2b7c4f19d38"
down_revision: str | Sequence[str] | None = "a1f6c30b9d47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Imported here, not at module level: env.py puts backend/ on sys.path
    # for migration runs, but revision *walkers* (provisioning's
    # stamp-at-head) import every migration module without that setup —
    # a module-level `from app...` import breaks tenant provisioning.
    from app.db import enable_rls_on_schema  # noqa: PLC0415

    bind = op.get_bind()
    schema = bind.execute(text("SELECT current_schema()")).scalar()
    if not schema:
        return
    session = Session(bind=bind)
    enable_rls_on_schema(session, schema)
    # The session wraps alembic's connection/transaction — flush, don't
    # commit/close (alembic owns the transaction; closing would return
    # the connection mid-migration).
    session.flush()


def downgrade() -> None:
    # Re-running the heal would restore the arm this revision added, since
    # it reads the live registry rather than a snapshot. Dropping the
    # policy by hand instead would leave the schema disagreeing with the
    # code that provisions new ones, which is the drift this revision
    # exists to remove. Reverting the registration is the real inverse,
    # and that is a code change, not a DDL one.
    pass
