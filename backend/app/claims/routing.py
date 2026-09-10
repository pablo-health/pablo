# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which practice filed a claim, answered by lookup rather than by asking.

A clearinghouse webhook names a transaction and nothing else, so the receiver
has to work out which practice the claim belongs to. It used to do that by
asking every practice in turn — bounded, and therefore wrong past the bound:
the delivery answered "unmatched", which is also what a delivery for somebody
else's claim answers, so nothing alerted and the claim simply stopped moving.

Recording the answer when the claim is FILED turns that scan into a lookup.
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
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import create_standalone_session
from ..db.platform_models import ClaimRouteRow
from ..utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Collection

logger = logging.getLogger(__name__)


def record_claim_route(control_number: str, practice_id: str | None) -> None:
    """Remember that ``practice_id`` filed ``control_number``.

    Idempotent: a claim reconciled or refiled under the same control number
    writes the same row again rather than failing. A control number already
    recorded against a DIFFERENT practice is left alone and logged loudly —
    that is either a generator collision (PABLO-z7te) or a routing bug, and
    quietly overwriting it would send the next remittance to the wrong
    practice's ledger.
    """
    if not control_number or practice_id is None:
        return
    try:
        with create_standalone_session() as db:
            db.execute(
                pg_insert(ClaimRouteRow)
                .values(
                    control_number=control_number.upper(),
                    practice_id=practice_id,
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
        # Never fail a filing over the index; a missing row costs a scan.
        logger.exception("claim_route_write_failed control_number=%s", control_number)


def practice_for_control_numbers(control_numbers: Collection[str]) -> str | None:
    """The practice that filed any of ``control_numbers``, or ``None``.

    ``None`` means "not indexed", not "not ours" — the caller falls back to
    the scan, which is what a claim filed before this index existed needs.
    """
    wanted = [number.upper() for number in control_numbers if number]
    if not wanted:
        return None
    try:
        with create_standalone_session() as db:
            return (
                db.execute(
                    select(ClaimRouteRow.practice_id).where(
                        ClaimRouteRow.control_number.in_(wanted)
                    )
                )
                .scalars()
                .first()
            )
    except Exception:
        logger.exception("claim_route_lookup_failed")
        return None
