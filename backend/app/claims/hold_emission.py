# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Saying, on every scheduled run, which client bills are still held.

A hold is raised once. A one-shot event that is dropped is gone, and
whatever was meant to chase it never learns it existed — which is the
difference between a hold and a crash report: crashes are reliable
precisely because they recur, so losing one log line costs you nothing.

So this re-emits every open hold on every tick, and says "nothing is open"
out loud when nothing is. The heartbeat is not redundancy. A reader
notices a missing line far sooner than a missing alert, and its absence is
the only thing that catches this whole mechanism rotting during the months
between a deployment going live and the first payer that sends an
inconsistent remittance.

**Severity is INFO, never ERROR.** A hold is an expected business event —
somebody has to decide something, and nothing is broken. Raising it to
ERROR moves error-rate SLOs and teaches whoever reads them that "incident"
sometimes means "a customer did an expected thing", which is how a real
incident gets skimmed past.

**No PHI.** The events carry an opaque hold id, a state, an age, the
payer's name, the adjustment codes and the practice's own control number
for the claim. No client identifier, no client name, no date of service —
a claim has exactly one client, so anything that pins the claim to a person
would put a patient in a log line. A test asserts this against a fully
populated hold rather than an empty one.

**This engine emits; it does not route.** Where the events go is
per-deployment configuration, and nothing here names a recipient.
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

#: Exactly one per run, including when the count is zero. Its absence is
#: the alert: no line for a couple of intervals means the tick, the log
#: pipeline, or the environment is dead.
HEARTBEAT_EVENT = "remittance_hold_heartbeat"

_SECONDS_PER_HOUR = 3600


def _age_hours(hold: RemittanceHold, *, now: datetime) -> int:
    return max(0, int((now - hold.detected_at).total_seconds() // _SECONDS_PER_HOUR))


def fields_for(hold: RemittanceHold, *, now: datetime) -> dict[str, object]:
    """The structured fields one hold's event carries.

    Separate from the emitting so a test can assert what crosses without
    parsing a log line — and so the PHI rule above is checkable as data
    rather than as a regex over rendered text.
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

    ``None`` — a deployment with no hold repository — emits nothing and
    reports zero. That is honest rather than convenient: it has no holds
    because it cannot have any, and the heartbeat's job is to say what the
    tick found.

    A hold that has been resolved is not open, so it stops being emitted on
    the very next tick without anything here having to know it happened.
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

    Called exactly once per run. Emitting it twice would make a reader
    watching for its absence learn to tolerate duplicates, and emitting it
    zero times on a quiet run is indistinguishable from the tick being
    dead — which is the one thing this line exists to tell them apart.
    """
    logger.info(
        "remittance_hold_heartbeat open=%d",
        open_count,
        extra={"event_type": HEARTBEAT_EVENT, "open": open_count},
    )
