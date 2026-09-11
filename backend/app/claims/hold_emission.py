# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Saying, on every scheduled run, which client bills are still held.

A hold is raised once, so a dropped event is gone — unlike a crash report,
which recurs. This re-emits every open hold on every tick, and says "nothing
is open" out loud when nothing is.

The heartbeat is not redundancy. Its ABSENCE is the signal, and it is the
only thing that catches this mechanism rotting during the months before a
real payer sends an inconsistent remittance.

INFO, never ERROR: a hold is an expected business event, and raising it to
ERROR teaches whoever reads error rates that "incident" sometimes means "a
customer did an expected thing".

No PHI — no client identifier, name or date of service. A claim has exactly
one client, so anything pinning the claim to a person puts a patient in a
log line.

Emits; does not route. Where these go is per-deployment configuration.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from ..models.claims_holds import RemittanceHold
    from ..repositories.remittance_hold import RemittanceHoldRepository

logger = logging.getLogger(__name__)

#: One per open hold, every tick.
HOLD_EVENT = "remittance_hold"

#: Exactly one per run, zero holds included. Its absence is the alert.
HEARTBEAT_EVENT = "remittance_hold_heartbeat"

_SECONDS_PER_HOUR = 3600


def _age_hours(hold: RemittanceHold, *, now: datetime) -> int:
    return max(0, int((now - hold.detected_at).total_seconds() // _SECONDS_PER_HOUR))


def fields_for(hold: RemittanceHold, *, now: datetime) -> dict[str, object]:
    """The structured fields one hold's event carries.

    Separate from emitting so the PHI rule is checkable as data rather than
    as a regex over rendered text.
    """
    return {
        "event_type": HOLD_EVENT,
        "hold_id": hold.id,
        "claim_id": hold.claim_id,
        "control_number": hold.control_number,
        "state": hold.state,
        "reason": hold.reason,
        "age_hours": _age_hours(hold, now=now),
        "stated_cents": hold.stated_cents,
        "computed_cents": hold.computed_cents,
        "delta_cents": hold.delta_cents,
        "line_count": hold.line_count,
        "payer_name": hold.payer_name,
        "codes": [f"{c['group_code']}-{c['reason_code']}" for c in hold.codes],
    }


def emit_open(holds: RemittanceHoldRepository | None, *, now: datetime) -> int:
    """Emit one event per open hold. Returns how many there were.

    ``None`` (no hold repository) emits nothing and reports zero — it has no
    holds because it cannot have any.
    """
    if holds is None:
        return 0
    open_holds = holds.list_open()
    for hold in open_holds:
        logger.info(
            "remittance_hold_open hold_id=%s state=%s age_hours=%d",
            hold.id,
            hold.state,
            _age_hours(hold, now=now),
            extra=fields_for(hold, now=now),
        )
    return len(open_holds)


def emit_heartbeat(open_count: int) -> None:
    """Say how many client bills are held, including when the answer is none.

    Exactly once per run. Twice teaches a reader to tolerate duplicates;
    zero on a quiet run is indistinguishable from the tick being dead.
    """
    logger.info(
        "remittance_hold_heartbeat open=%d",
        open_count,
        extra={"event_type": HEARTBEAT_EVENT, "open": open_count},
    )
