# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The list of claims waiting to be read, answered by lookup rather than search.

A held claim lives in its practice's schema and is row-policied to the clinician
who owns it, so "everything waiting on a reviewer" is the same question
:mod:`app.claims.routing` answers for inbound webhooks: reachable only by
opening every practice in turn, which has to be bounded, which makes it wrong
past the bound. A reviewer's list that stops at the fiftieth practice omits
exactly the claims nobody knows to release.

Recording the claim when it is HELD turns that search into a read, and deleting
the row when it is released keeps the list equal to the set.

**An index, never the authority.** The claim's ``in_review`` state is the truth.
Every write here swallows its own failure and logs, the same as
``record_claim_route`` and for the same reason: bookkeeping must not fail a
claim. Losing a row costs a claim that is held but missing from the queue —
still on the filing clock, still escalated by the watchdog — so a person still
hears about it. A write that could fail the hold would cost the hold.

What it carries is bounded by who reads it: the claim, whose it is, the payer's
NAME, why, and when. A payer is an insurance company. Nothing here names a
client, a diagnosis, a service or an amount; a reviewer who needs those opens
the claim in its own tenant session, where the row policy still applies.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import create_standalone_session
from ..db.platform_models import ClaimReviewRow, PracticeRow
from ..utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def record_held(  # noqa: PLR0913 — one row's columns, keyword-only past the key
    claim_id: str,
    control_number: str,
    *,
    practice_id: str | None,
    user_id: str,
    payer_name: str,
    reasons: Sequence[str],
) -> None:
    """Put a held claim on the reviewable list. Never raises.

    Idempotent on the claim id: a claim held, released and held again writes the
    same row rather than a second one, and a re-run of the same pass is a no-op.

    ``practice_id`` is ``None`` on a deployment with no practice registry, which
    is not an error — there is nothing to index across, and the deployment can
    list its own held claims from its one schema.
    """
    if practice_id is None or not claim_id:
        return
    try:
        with create_standalone_session() as db:
            db.execute(
                pg_insert(ClaimReviewRow)
                .values(
                    claim_id=claim_id,
                    practice_id=practice_id,
                    user_id=user_id,
                    control_number=control_number,
                    payer_name=payer_name,
                    reasons=",".join(reasons),
                    held_at=utc_now(),
                )
                .on_conflict_do_nothing(index_elements=["claim_id"])
            )
            db.commit()
    except Exception:
        # The claim is held regardless; the queue is a convenience over it.
        logger.exception("claim_review_record_failed claim_id=%s", claim_id)


def record_released(claim_id: str) -> None:
    """Take a claim off the list once it is no longer waiting. Never raises.

    Called on approve and on refuse alike: both mean nobody is waiting on it
    any more, and a queue that only ever grew would stop being a queue.
    """
    if not claim_id:
        return
    try:
        with create_standalone_session() as db:
            db.execute(delete(ClaimReviewRow).where(ClaimReviewRow.claim_id == claim_id))
            db.commit()
    except Exception:
        # A row left behind shows a claim as waiting when it is not. Annoying,
        # and self-correcting the next time anybody opens it; a raise here would
        # instead fail the release of a claim somebody just approved.
        logger.exception("claim_review_release_failed claim_id=%s", claim_id)


def list_awaiting_review(*, limit: int = 200) -> list[dict[str, object]]:
    """Every claim waiting to be read, newest first, with its practice's schema.

    One join of two platform tables, both by key — the same two-indexed-reads
    shape ``route_for_control_numbers`` uses. The schema comes back so a caller
    can open the claim in its own tenant session; this function deliberately
    does not do that itself, because reading the claim is a PHI access with an
    audit obligation and this is a list.

    Practices no longer active are dropped: their claims are not fileable and a
    reviewer cannot act on them.
    """
    with create_standalone_session() as db:
        rows = db.execute(
            select(
                ClaimReviewRow.claim_id,
                ClaimReviewRow.practice_id,
                ClaimReviewRow.user_id,
                ClaimReviewRow.control_number,
                ClaimReviewRow.payer_name,
                ClaimReviewRow.reasons,
                ClaimReviewRow.held_at,
                PracticeRow.schema_name,
                PracticeRow.name,
            )
            .join(PracticeRow, PracticeRow.id == ClaimReviewRow.practice_id)
            .where(PracticeRow.is_active.is_(True))
            .order_by(ClaimReviewRow.held_at.desc())
            .limit(limit)
        ).all()
    return [
        {
            "claim_id": row[0],
            "practice_id": row[1],
            "user_id": row[2],
            "control_number": row[3],
            "payer_name": row[4],
            "reasons": row[5].split(",") if row[5] else [],
            "held_at": row[6],
            "schema": row[7],
            "practice_name": row[8],
        }
        for row in rows
    ]
