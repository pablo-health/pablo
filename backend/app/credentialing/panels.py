# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where each panel application stands, read for the clinician it belongs to.

Reads only. Pablo runs the applications, so every write here comes from the
operator surface rather than from her — a clinician who could set her own
application to ``effective`` would be recording a fact she is not the source
of.

The table is PLATFORM-scoped, because the surface that reads it most is an
operator working across every practice at once. Her own view is not weakened by
that: ``platform.panel_applications`` carries row-level security on
``app.current_user_id``, so the ``user_id`` filter below is the same answer
stated twice rather than the only thing standing between her and a colleague's
applications.

The payer's name is joined rather than stored, so an insurer renamed in
Settings is renamed everywhere it appears at once. ``payers`` is per-tenant, so
that join resolves through the request's ``search_path`` — which is set to her
practice schema, and is why this read needs no practice filter of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db.models import PayerRow
from ..db.platform_models import PlatformPanelApplicationRow

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

    row: PlatformPanelApplicationRow
    payer_name: str

    @property
    def mine_to_act_on(self) -> bool:
        """Whether this one is waiting on HER rather than on us or the payer."""
        return self.row.action_owner == "therapist"

    @property
    def settled(self) -> bool:
        return self.row.status in SETTLED_STATUSES

    def days_since_submitted(self, now: datetime) -> int | None:
        """How long it has been sitting with the payer, or ``None`` if unfiled."""
        submitted = self.row.submitted_at
        if submitted is None:
            return None
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
        select(PlatformPanelApplicationRow, PayerRow.name)
        .join(PayerRow, PayerRow.id == PlatformPanelApplicationRow.payer_id)
        .where(PlatformPanelApplicationRow.user_id == user_id)
        .order_by(
            PlatformPanelApplicationRow.status.in_(SETTLED_STATUSES),
            (PlatformPanelApplicationRow.action_owner != "therapist"),
            PlatformPanelApplicationRow.due_at.is_(None),
            PlatformPanelApplicationRow.due_at,
            PlatformPanelApplicationRow.created_at,
        )
    ).all()
    return [PanelApplication(row=row, payer_name=payer_name) for row, payer_name in rows]
