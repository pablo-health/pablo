# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""compliance_items.source_ref: key an enrollment reminder to its request

The claim arm of the reminder listener got a foreign key and a unique
constraint in ``f2a91c37b6d4``. The enrollment arm did not — a payer wanting
the practice to sign something carries no claim and no patient, so there was
nothing to point a foreign key at — and it kept the defect the claim arm was
moved off: the reminder was found by ``notes`` starting with
``Claim control number: <vendor request id>``, and the compliance update route
replaces ``notes`` wholesale. A clinician tidying her own note severed the
link, the next refresh found nothing, and filed another reminder.

``source_ref`` is that handle as a column. The route does not write it, so no
edit can remove it, and a partial unique index on
``(user_id, item_type, source_ref)`` makes the duplicate impossible rather
than merely unlikely.

Three things happen here, in order, because the index cannot be created while
the rows it forbids are still present:

1. Backfill ``source_ref`` from the marker, and strip the marker line from
   ``notes`` — it was only ever there to be looked up, and the reader has no
   use for a vendor request id above the payer's own words.
2. Collapse the duplicates the bug already filed. The earliest row per
   ``(user_id, item_type, source_ref)`` survives, since its dates describe
   when the event actually landed. Before the others go, a completion among
   them is carried onto the survivor — a reminder someone has already marked
   done must not come back to life as the price of deduping it. This drops
   rows, deliberately and only where they are duplicates of a row that stays.
3. Create the index.

A row whose marker had already been edited away cannot be matched to anything
— that is the bug — so it is left exactly where it is, with a NULL
``source_ref``, rather than guessed at. It stays on the dashboard as an
ordinary compliance item and the refresh files one fresh, properly keyed
reminder beside it, once.

Revision ID: a7d24e91f3c8
Revises: f2a91c37b6d4
Create Date: 2026-09-15
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a7d24e91f3c8"
down_revision: str | Sequence[str] | None = "f2a91c37b6d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: What ``app.claims.events`` wrote on the first line of ``notes``. Inlined
#: rather than imported, so a later edit to the application code cannot change
#: what this migration did — the same reason ``f2a91c37b6d4`` inlines it.
_MARKER = "Claim control number: "

_INDEX = "ux_compliance_items_source_ref"


def upgrade() -> None:
    op.add_column(
        "compliance_items",
        sa.Column("source_ref", sa.String(128), nullable=True),
    )

    # 1. Lift the marker out of the first line of notes and into the column,
    #    then drop that line. ``split_part(notes, E'\n', 1)`` is the marker
    #    line; everything after the first newline is what a person wrote.
    op.execute(
        sa.text(
            """
            UPDATE compliance_items
               SET source_ref = split_part(
                       split_part(notes, E'\\n', 1), :marker, 2
                   ),
                   notes = NULLIF(
                       substring(notes from position(E'\\n' in notes || E'\\n') + 1),
                       ''
                   )
             WHERE item_type LIKE 'claim\\_%'
               AND notes LIKE :marker_prefix
            """
        ).bindparams(
            sa.bindparam("marker", value=_MARKER),
            sa.bindparam("marker_prefix", value=f"{_MARKER}%"),
        )
    )

    # 2. Carry any completion onto the row that will survive, so deduping
    #    cannot reopen something already dealt with.
    op.execute(
        sa.text(
            """
            UPDATE compliance_items survivor
               SET completed_at = dup.completed_at,
                   updated_at = GREATEST(survivor.updated_at, dup.updated_at)
              FROM (
                    SELECT user_id, item_type, source_ref,
                           MIN(completed_at) AS completed_at,
                           MAX(updated_at) AS updated_at
                      FROM compliance_items
                     WHERE source_ref IS NOT NULL
                       AND completed_at IS NOT NULL
                     GROUP BY user_id, item_type, source_ref
                   ) dup
             WHERE survivor.source_ref IS NOT NULL
               AND survivor.completed_at IS NULL
               AND survivor.user_id = dup.user_id
               AND survivor.item_type = dup.item_type
               AND survivor.source_ref = dup.source_ref
            """
        )
    )

    # 3. Then the duplicates themselves: everything but the earliest row in
    #    each group.
    op.execute(
        sa.text(
            """
            DELETE FROM compliance_items
             WHERE id IN (
                   SELECT id
                     FROM (
                           SELECT id,
                                  ROW_NUMBER() OVER (
                                      PARTITION BY user_id, item_type, source_ref
                                      ORDER BY created_at ASC, id ASC
                                  ) AS row_num
                             FROM compliance_items
                            WHERE source_ref IS NOT NULL
                          ) ranked
                    WHERE ranked.row_num > 1
                   )
            """
        )
    )

    op.create_index(
        _INDEX,
        "compliance_items",
        ["user_id", "item_type", "source_ref"],
        unique=True,
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )


def downgrade() -> None:
    # Rebuild the marker the old code looked for, so a rolled-back deployment
    # dedupes the way it used to. The duplicates collapsed on the way up are
    # not recreated: they were the bug's output, and a downgrade that
    # faithfully restored them would be restoring the symptom.
    op.drop_index(_INDEX, table_name="compliance_items")
    op.execute(
        sa.text(
            """
            UPDATE compliance_items
               SET notes = :marker || source_ref || COALESCE(E'\\n' || notes, '')
             WHERE source_ref IS NOT NULL
            """
        ).bindparams(sa.bindparam("marker", value=_MARKER))
    )
    op.drop_column("compliance_items", "source_ref")
