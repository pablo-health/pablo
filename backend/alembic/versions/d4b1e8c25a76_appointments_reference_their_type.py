# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointments reference the type they are an instance of

``appointments`` recorded its type as a free-form string. That was survivable
while a type was just a name and a fee, but a type now carries its length, its
fee and its booking window, and the settings UI lets a clinician rename one.
The moment they do, every appointment booked under the old name is orphaned:
nothing matches, and fee resolution by name silently stops finding anything.

So ``appointment_type_id`` joins the two properly.

Nullable, with ``ON DELETE SET NULL``: an appointment can outlive its type.
Deleting a type should tidy the type away, not refuse because someone was seen
under it in March, and not cascade the appointment out of the record.

``session_type`` stays, and is not redundant. The id says which type this is
now; the string says what it was called when it was booked. After a rename,
history should still read as what it was.

Backfill matches on name within the same user. Anything that does not match a
type stays NULL — a null here means "we could not tell", which is honest, and
is exactly what the settings UI needs to surface rather than a wrong guess.

Not converted, deliberately: ``booking_links.session_type``. That table is
platform-scoped so a public slug resolves before a tenant schema is selected,
and a platform table cannot hold a foreign key into one of N tenant schemas.

Revision ID: d4b1e8c25a76
Revises: c7e2a9f14b83
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d4b1e8c25a76"
down_revision: str | Sequence[str] | None = "c7e2a9f14b83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("appointment_type_id", sa.Uuid(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_appointments_appointment_type",
        "appointments",
        "appointment_types",
        ["appointment_type_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_appointments_appointment_type_id", "appointments", ["appointment_type_id"]
    )

    # Match on name within the same clinician, which is what the unique
    # constraint on appointment_types already guarantees is unambiguous.
    # A row whose session_type names no existing type keeps a NULL link.
    op.execute(
        """
        UPDATE appointments a
           SET appointment_type_id = t.id
          FROM appointment_types t
         WHERE t.user_id = a.user_id
           AND t.name = a.session_type
        """
    )


def downgrade() -> None:
    op.drop_index("ix_appointments_appointment_type_id", table_name="appointments")
    op.drop_constraint("fk_appointments_appointment_type", "appointments", type_="foreignkey")
    op.drop_column("appointments", "appointment_type_id")
