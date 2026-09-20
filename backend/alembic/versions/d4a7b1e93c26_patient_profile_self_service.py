# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Let a patient keep their own contact details and preferred name.

Two changes to ``patients``, both needed by the portal's profile screen and
neither meaningful without the other.

**A ``preferred_name`` column.** The chart holds a legal first and last
name, which is what a claim and a release of information need and is not
always what the person is called. Identity fields stay the clinician's to
change — a patient who says their date of birth is wrong is starting a
conversation, not editing a field — so the profile screen needs one name
field the patient owns outright, and this is it. Nullable, because a chart
that has never been asked has no answer, and blank means "use the first
name" rather than "no name".

**A patient WRITE arm on ``patients``.** Until now the table was registered
patient-READABLE only, deliberately: nothing in the engine let a patient
write their own record. The profile screen does, for contact fields, so the
registration widens — and registration alone only ever reaches schemas
provisioned AFTER it, because ``enable_rls_on_schema`` runs at provisioning
time. On an unhealed tenant the failure is loud rather than quiet (the
``pablo`` role is NOBYPASSRLS and an UPDATE with no matching policy simply
affects no rows, so the patient is told their change saved and it did not),
which is worse than an error. So this re-runs ``enable_rls_on_schema``, the
same idempotent self-healing call ``e2b7c4f19d38`` and
``b6e1d8c4a7f2_heal_tenant_rls_policies`` make, and for the same reason.

The write arm is bounded by the row, not the column — row-level security has
no column granularity — so which columns a patient may actually change is the
route's decision and is made there: ``app.routes.patient_profile`` takes an
allow-listed request model and writes nothing outside it. The policy stops A
from writing B's row; the route stops anyone from writing a name or a date of
birth.

``tenant_template.sql`` DOES change here, for the column but not for the
policy: ``enable_rls_on_schema`` skips the ``practice`` template schema by
design and provisioning re-applies RLS when a tenant is cloned from it.

Revision ID: d4a7b1e93c26
Revises: b7e3f0c48d15
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "d4a7b1e93c26"
down_revision: str | Sequence[str] | None = "b7e3f0c48d15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``IF NOT EXISTS`` because this revision runs over two different
    # starting points. A tenant that already existed gets the column added
    # here; a tenant provisioned AFTER this lands is built from
    # ``tenant_template.sql``, which is captured at chain head and therefore
    # already has it — and is then stamped and migrated, so the chain walks
    # over a table that is already right. A bare ``ADD COLUMN`` fails that
    # second case with ``DuplicateColumn`` and takes provisioning down with
    # it. Same idiom as ``e5b91c34a72f``.
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS preferred_name VARCHAR(255) NULL")

    # Imported here, not at module level: env.py puts backend/ on sys.path
    # for migration runs, but revision *walkers* (provisioning's
    # stamp-at-head) import every migration module without that setup — a
    # module-level ``from app...`` import breaks tenant provisioning.
    from app.db import enable_rls_on_schema  # noqa: PLC0415

    bind = op.get_bind()
    schema = bind.execute(text("SELECT current_schema()")).scalar()
    if not schema:
        return
    session = Session(bind=bind)
    enable_rls_on_schema(session, schema)
    # The session wraps alembic's connection/transaction — flush, don't
    # commit/close (alembic owns the transaction; closing would return the
    # connection mid-migration).
    session.flush()


def downgrade() -> None:
    # The column comes back out. The policy does not: its real inverse is
    # unregistering the table, which is a code change, and dropping the arm
    # here would leave the schema disagreeing with what provisioning builds.
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS preferred_name")
