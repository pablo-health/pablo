# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where each panel application stands, read for the clinician it belongs to.

Reads only. Pablo runs the applications, so every write here comes from the
operator surface rather than from her — a clinician who could set her own
application to ``effective`` would be recording a fact she is not the source
of.

The payer's name is joined rather than stored, so an insurer renamed in
Settings is renamed everywhere it appears at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db.models import PanelApplicationRow, PayerRow

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

#: The statuses that mean the application is over, one way or another. Kept
#: apart from the live ones because the screen leads with what is still moving
#: and a finished application is history, not work.
SETTLED_STATUSES: frozenset[str] = frozenset({"effective", "denied"})


@dataclass(frozen=True)
class PanelApplication:
    """One application, with the payer's name resolved."""

    row: PanelApplicationRow
    payer_name: str

    @property
    def mine_to_act_on(self) -> bool:
        """Whether this one is waiting on HER rather than on us or the payer."""
        return self.row.action_owner == "therapist"

    @property
    def settled(self) -> bool:
        return self.row.status in SETTLED_STATUSES

    def days_since_submitted(self, now: datetime) -> int | None:
        """How long it has been sitting with the payer, or ``None`` if unfiled.

        The column is ``TIMESTAMP WITH TIME ZONE`` and Postgres hands back an
        aware value, but SQLite — which the route tests run on — drops the
        offset and returns a naive one. Subtracting the two raises, so the
        stored value is read as UTC when it arrives without an offset. That is
        what it always was; only the carrier forgot.
        """
        submitted = self.row.submitted_at
        if submitted is None:
            return None
        if submitted.tzinfo is None:
            submitted = submitted.replace(tzinfo=UTC)
        return (now - submitted).days


def list_for(session: Session, user_id: str) -> list[PanelApplication]:
    """Every application this clinician has, hers to act on first.

    The ordering is the screen's argument rather than a detail. What she owes
    comes first, soonest deadline at the top, because a board that sorts by
    payer name makes her read all of it to find the one thing she has to do.
    Everything Pablo is carrying follows, and applications that have finished
    — contracted or refused — sink to the bottom.

    ``due_at`` is NULL for most rows, and NULLs sort last within their group:
    an application waiting on the payer's own clock has no deadline we set,
    and putting those above a dated one would bury the dated one.
    """
    rows = session.execute(
        select(PanelApplicationRow, PayerRow.name)
        .join(PayerRow, PayerRow.id == PanelApplicationRow.payer_id)
        .where(PanelApplicationRow.user_id == user_id)
        .order_by(
            PanelApplicationRow.status.in_(SETTLED_STATUSES),
            (PanelApplicationRow.action_owner != "therapist"),
            PanelApplicationRow.due_at.is_(None),
            PanelApplicationRow.due_at,
            PanelApplicationRow.created_at,
        )
    ).all()
    return [PanelApplication(row=row, payer_name=payer_name) for row, payer_name in rows]
