# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""claim_reminders: link a claim's alert to the claim, instead of to a string

Claim alerts have been ``compliance_items`` rows, and the only thing tying one
to its claim was the first line of ``notes``. The compliance update route
replaces ``notes`` wholesale, so a clinician editing her own note severed the
link; the pipeline then found nothing on the next tick and filed a duplicate,
and kept filing one.

This gives them a table with a foreign key to the claim and a unique constraint
on ``(claim_id, kind)``, and moves the existing rows across.

The backfill matches on ``compliance_items.notes`` starting with the marker the
old code wrote, which is the only handle that exists. Rows whose marker was
already edited away cannot be matched — deliberately left where they are rather
than guessed at, so nothing is silently discarded. They stay visible on the
dashboard as ordinary compliance items; the pipeline simply files a fresh,
properly linked reminder the next time it sees that claim.

Revision ID: f2a91c37b6d4
Revises: c1b7e4a92d53
Create Date: 2026-09-15
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f2a91c37b6d4"
down_revision: str | Sequence[str] | None = "c1b7e4a92d53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The kinds that have a claim behind them.
#:
#: ``paid`` is absent because it needs nothing from anyone, and
#: ``enrollment_action_required`` because it is not about a claim — a payer
#: wanting the practice to sign something carries a synthesised claim id and a
#: vendor request id, so it stays a compliance item and the backfill below
#: leaves those rows exactly where they are.
_KINDS = (
    "rejected",
    "denied",
    "partial",
    "stalled",
    "deadline_approaching",
    "deadline_missed",
    "unmatched_remittance",
    "remittance_held",
)

#: What ``app.claims.events`` wrote on the first line of ``notes``. The backfill
#: has no other handle; it is inlined rather than imported so a later edit to
#: the application code cannot change what this migration did.
_MARKER = "Claim control number: "


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "claim_reminders",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "claim_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Carried beside claim_id so ``has_patient_access`` isolates the row
        # without the policy engine learning a join — the same arrangement
        # claim_lines and remittance_holds use.
        sa.Column(
            "patient_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"kind IN ({_in_list(_KINDS)})", name="ck_claim_reminders_kind"),
        # One line per claim per kind. Coarser than claim_events' ladder on
        # purpose: four deadline rungs are one thing to do.
        sa.UniqueConstraint("claim_id", "kind", name="ux_claim_reminders_claim_kind"),
    )
    op.create_index("ix_claim_reminders_patient_id", "claim_reminders", ["patient_id"])
    op.create_index("ix_claim_reminders_due_date", "claim_reminders", ["due_date"])

    # Move what can be moved. ``split_part`` takes the marker's line, and the
    # join to claims supplies the claim and patient the old row never named.
    #
    # DISTINCT ON keeps one row per (claim, kind) because the bug this fixes
    # means duplicates may already exist — the oldest is kept, since that is
    # the one whose dates and codes describe when the event actually landed.
    op.execute(
        sa.text(
            """
            INSERT INTO claim_reminders (
                id, claim_id, patient_id, kind, label,
                due_date, notes, completed_at, created_at, updated_at
            )
            SELECT DISTINCT ON (c.id, substring(ci.item_type from 7))
                gen_random_uuid(),
                c.id,
                c.patient_id,
                substring(ci.item_type from 7),
                ci.label,
                ci.due_date,
                ci.notes,
                ci.completed_at,
                ci.created_at,
                ci.updated_at
            FROM compliance_items ci
            JOIN claims c
              ON c.control_number = split_part(
                     split_part(ci.notes, E'\\n', 1), :marker, 2
                 )
            WHERE ci.item_type LIKE 'claim\\_%'
              AND ci.notes LIKE :marker_prefix
              AND substring(ci.item_type from 7) = ANY(:kinds)
            ORDER BY c.id, substring(ci.item_type from 7), ci.created_at ASC
            """
        ).bindparams(
            sa.bindparam("marker", value=_MARKER),
            sa.bindparam("marker_prefix", value=f"{_MARKER}%"),
            sa.bindparam("kinds", value=list(_KINDS), type_=sa.ARRAY(sa.Text)),
        )
    )

    # Remove only what was actually carried across, matched the same way. A row
    # whose marker had been edited away is left alone rather than deleted.
    op.execute(
        sa.text(
            """
            DELETE FROM compliance_items ci
            USING claims c
            WHERE ci.item_type LIKE 'claim\\_%'
              AND ci.notes LIKE :marker_prefix
              AND c.control_number = split_part(
                      split_part(ci.notes, E'\\n', 1), :marker, 2
                  )
              AND EXISTS (
                  SELECT 1 FROM claim_reminders r
                  WHERE r.claim_id = c.id
                    AND r.kind = substring(ci.item_type from 7)
              )
            """
        ).bindparams(
            sa.bindparam("marker", value=_MARKER),
            sa.bindparam("marker_prefix", value=f"{_MARKER}%"),
        )
    )


def downgrade() -> None:
    # Put them back as compliance items, rebuilding the marker the old code
    # depended on so a rolled-back deployment dedupes as it used to.
    op.execute(
        sa.text(
            """
            INSERT INTO compliance_items (
                id, user_id, item_type, label, due_date, notes,
                completed_at, created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                pc.user_id,
                'claim_' || r.kind,
                r.label,
                r.due_date,
                CASE
                    WHEN r.notes LIKE :marker_prefix THEN r.notes
                    ELSE :marker || c.control_number ||
                         COALESCE(E'\\n' || r.notes, '')
                END,
                r.completed_at,
                r.created_at,
                r.updated_at
            FROM claim_reminders r
            JOIN claims c ON c.id = r.claim_id
            -- The old row was addressed to one clinician; this picks the
            -- primary where the patient has one, and otherwise the earliest
            -- grant. A downgrade cannot recover who it was originally
            -- addressed to, because the new table does not record it — the
            -- claim's own access does.
            JOIN LATERAL (
                SELECT user_id FROM patient_clinicians
                WHERE patient_id = r.patient_id
                ORDER BY (role = 'primary') DESC, granted_at ASC
                LIMIT 1
            ) pc ON TRUE
            """
        ).bindparams(
            sa.bindparam("marker", value=_MARKER),
            sa.bindparam("marker_prefix", value=f"{_MARKER}%"),
        )
    )
    op.drop_index("ix_claim_reminders_due_date", table_name="claim_reminders")
    op.drop_index("ix_claim_reminders_patient_id", table_name="claim_reminders")
    op.drop_table("claim_reminders")
