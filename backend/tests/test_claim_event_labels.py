# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The reminder label for a stalled claim says whether it ever left.

A claim parked by the watchdog was filed and is waiting on an answer; one
parked by the outbox was never filed at all. Both are ``stalled``, and the
label is where a person reading the reminder learns which.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.claims.events import ClaimEvent, ClaimEventDetail, CodeRef, _label


def _stalled(*, filing_refused: bool) -> ClaimEvent:
    return ClaimEvent(
        kind="stalled",
        control_number="1NC1ATY9D4VH5",
        claim_id="claim-1",
        user_id="user-1",
        payer_id="STEDI",
        payer_name="Test Payer",
        state="stalled",
        occurred_at=datetime(2026, 10, 3, tzinfo=UTC),
        detail=ClaimEventDetail(
            codes=(CodeRef(system="status", code="access_denied"),),
            filing_refused=filing_refused,
        ),
    )


def test_a_claim_the_clearinghouse_refused_reads_as_not_filed() -> None:
    assert _label(_stalled(filing_refused=True)) == (
        "Claim 1NC1ATY9 could not be filed with Test Payer"
    )


def test_a_claim_that_timed_out_still_reads_as_stalled_at_the_payer() -> None:
    assert _label(_stalled(filing_refused=False)) == "Claim 1NC1ATY9 stalled at Test Payer"
