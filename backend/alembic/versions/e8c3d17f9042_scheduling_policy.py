# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""practice-level scheduling policy

A practice had nowhere to say how late a patient may cancel, how far ahead
anything may be booked, how a new enquiry starts, or whether patients may book
at all. Appointment types answer what an appointment IS; nothing answered what
the practice will allow to happen to its calendar.

``scheduling_policy`` is that: one row per practice, pinned by
``CHECK (id = 1)`` so a save upserts in place.

Every gate ships off or strict. ``self_book_existing`` and ``self_book_new``
are both false and ``self_book_mode`` is ``request``, so a practice upgrading
into this code cannot discover that patients have started booking it. No row is
created here either — an unconfigured practice reads the defaults, and reading
never writes.

Two switches govern self-booking on purpose. This table says the practice
allows it at all; ``appointment_types.self_bookable`` says whether a particular
appointment is one of the bookable ones. Both must be true. That is why there
is no allow-list of type names here: the per-type flag already answers it, and
answers it by reference rather than by name, so renaming a type cannot silently
change what patients may book.

RLS: the isolation boundary for this table is the tenant schema, not a per-row
predicate — it is practice config with no ``user_id`` or ``patient_id``. It is
registered in ``not_row_scoped`` so ``enable_rls_on_schema`` leaves RLS off
rather than force-enabling it with no policy, which would be a silent deny-all.

Revision ID: e8c3d17f9042
Revises: d4b1e8c25a76
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e8c3d17f9042"
down_revision: str | Sequence[str] | None = "d4b1e8c25a76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduling_policy",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("min_notice_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("max_horizon_days", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("cancel_cutoff_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("reschedule_cutoff_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("pending_hold_hours", sa.Integer(), nullable=False, server_default="72"),
        sa.Column("self_book_existing", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("self_book_new", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("self_book_mode", sa.String(length=10), nullable=False, server_default="request"),
        sa.Column(
            "new_patient_flow", sa.String(length=10), nullable=False, server_default="consult"
        ),
        sa.Column("intake_forms_due_hours", sa.Integer(), nullable=False, server_default="48"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_scheduling_policy_singleton"),
        sa.CheckConstraint(
            "self_book_mode IN ('request', 'auto')", name="ck_scheduling_policy_self_book_mode"
        ),
        sa.CheckConstraint(
            "new_patient_flow IN ('consult', 'intake')",
            name="ck_scheduling_policy_new_patient_flow",
        ),
        sa.CheckConstraint("min_notice_hours >= 0", name="ck_scheduling_policy_min_notice"),
        sa.CheckConstraint("max_horizon_days > 0", name="ck_scheduling_policy_max_horizon"),
        sa.CheckConstraint("cancel_cutoff_hours >= 0", name="ck_scheduling_policy_cancel_cutoff"),
        sa.CheckConstraint(
            "reschedule_cutoff_hours >= 0", name="ck_scheduling_policy_reschedule_cutoff"
        ),
        sa.CheckConstraint("pending_hold_hours > 0", name="ck_scheduling_policy_pending_hold"),
        sa.CheckConstraint(
            "intake_forms_due_hours >= 0", name="ck_scheduling_policy_intake_forms_due"
        ),
    )


def downgrade() -> None:
    op.drop_table("scheduling_policy")
