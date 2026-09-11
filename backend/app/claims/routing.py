# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which practice filed a claim, answered by lookup rather than by asking.

A clearinghouse webhook names a transaction and nothing else, so the receiver
has to work out which practice the claim belongs to. It used to do that by
asking every practice in turn — bounded, and therefore wrong past the bound:
the delivery answered "unmatched", which is also what a delivery for somebody
else's claim answers, so nothing alerted and the claim simply stopped moving.

Recording the answer when the claim is FILED turns that scan into a lookup —
both of them, in fact: claims are row-policied, so a receiver holding only the
practice would still have to open a session per clinician to find whose claim
it is. The row names the clinician too, and the whole path becomes two indexed
reads.

The write goes alongside the outbox's pending marker, BEFORE the vendor call,
for the same reason the marker does: the crash that loses an answer is exactly
the crash after which an acknowledgement arrives for a claim we have no record
of routing. Recording a control number we then fail to file costs one unused
row; failing to record one we did file costs a claim that stops moving.

Failure here must never fail a filing. An index write that raises would turn a
submission into an error and, on the next pass, a second attempt at a claim
that may already be with the payer. So the writes swallow their exceptions and
log; the receiver reads a missing row as ``unmatched``, and the claim still
moves on the pipeline's next polling pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import create_standalone_session
from ..db.platform_models import ClaimRouteRow, PracticeRow
from ..utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Collection

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClaimRoute:
    """Where a claim lives: which practice's schema, and whose row policy."""

    practice_id: str
    user_id: str
    schema: str


def record_claim_route(control_number: str, practice_id: str | None, user_id: str) -> None:
    """Remember that ``user_id`` of ``practice_id`` filed ``control_number``.

    Idempotent: a claim reconciled or refiled under the same control number
    writes the same row again rather than failing. A control number already
    recorded against a DIFFERENT practice is left alone and logged loudly —
    that is either a generator collision (PABLO-z7te) or a routing bug, and
    quietly overwriting it would send the next remittance to the wrong
    practice's ledger.
    """
    if not control_number or practice_id is None or not user_id:
        return
    try:
        with create_standalone_session() as db:
            db.execute(
                pg_insert(ClaimRouteRow)
                .values(
                    control_number=control_number.upper(),
                    practice_id=practice_id,
                    user_id=user_id,
                    created_at=utc_now(),
                )
                .on_conflict_do_nothing(index_elements=["control_number"])
            )
            db.commit()
            existing = db.execute(
                select(ClaimRouteRow.practice_id).where(
                    ClaimRouteRow.control_number == control_number.upper()
                )
            ).scalar_one_or_none()
        if existing is not None and existing != practice_id:
            logger.error(
                "claim_route_conflict control_number=%s recorded_practice=%s filing_practice=%s",
                control_number,
                existing,
                practice_id,
            )
    except Exception:
        # Never fail a filing over the index; the claim still moves on the
        # pipeline's next polling pass.
        logger.exception("claim_route_write_failed control_number=%s", control_number)


def route_for_control_numbers(control_numbers: Collection[str]) -> ClaimRoute | None:
    """Where the claim named by any of ``control_numbers`` lives, or ``None``.

    One join of two platform tables, both by key: the index gives the practice
    and the clinician, ``practices`` gives the schema to open. ``None`` means
    "not indexed" or "practice no longer active", never "not ours" — the caller
    reports it unrouted and the claim waits for the polling pass.
    """
    wanted = [number.upper() for number in control_numbers if number]
    if not wanted:
        return None
    try:
        with create_standalone_session() as db:
            row = db.execute(
                select(
                    ClaimRouteRow.practice_id,
                    ClaimRouteRow.user_id,
                    PracticeRow.schema_name,
                )
                .join(PracticeRow, PracticeRow.id == ClaimRouteRow.practice_id)
                .where(
                    ClaimRouteRow.control_number.in_(wanted),
                    PracticeRow.is_active.is_(True),
                )
            ).first()
        if row is None:
            return None
        return ClaimRoute(practice_id=row[0], user_id=row[1], schema=row[2])
    except Exception:
        logger.exception("claim_route_lookup_failed")
        return None
