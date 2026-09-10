# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A real claim timeline, read the way production reads one.

Every other test of the timeline mapping builds the vendor's objects by hand,
which can only ever confirm what the author believed the vendor sends. This
one starts from bytes the vendor actually sent — a claim filed against its
test payer, acknowledged and paid — and pushes them through the vendor's own
deserialiser before our mapping sees them.

That makes it a contract test rather than a fixture test: if the vendor
renames a field, changes an enum, or moves an amount, the SDK's deserialiser
notices here instead of a practice noticing it in its receivables.

The capture is synthetic throughout: the vendor's documented example person,
its dummy NPI, and its test payer, which acknowledges and pays every claim
and never forwards one to a real payer.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from app.claims.remittance import posting_for
from app.claims.sdk_timeline import _DISPOSITIONS, _OUTCOMES, timeline_from_sdk
from smithy_http import Field, Fields
from smithy_http.aio import HTTPResponse
from stedi.client import Stedi
from stedi.config import Config
from stedi.models import (
    ClaimAcknowledgmentStatus,
    ClaimPaymentInformationStatusCode,
    GetClaimTimelineInput,
)

if TYPE_CHECKING:
    from app.models.claims_timeline import ClaimTimeline

CAPTURE = Path(__file__).parent / "fixtures" / "clearinghouse" / "claim_timeline_paid_in_full.json"

#: What the captured claim was billed at, in cents. The capture is the
#: vendor's test payer paying a single 60-minute psychotherapy session in
#: full.
CHARGED_CENTS = 15_000


class _ReplayTransport:
    """Answers whatever the SDK asks with one recorded body.

    The SDK's transport seam is the last point before its own parsing, so
    swapping it here exercises every layer the vendor owns — protocol,
    deserialiser, enums, date parsing — against real bytes, with no network.
    """

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def send(self, request: Any, *, request_config: Any = None) -> HTTPResponse:
        return HTTPResponse(
            status=200,
            fields=Fields([Field(name="content-type", values=["application/json"])]),
            body=self._body,
        )


async def _read_timeline() -> ClaimTimeline:
    client = Stedi(
        config=Config(api_key="not-a-real-key", transport=_ReplayTransport(CAPTURE.read_bytes()))
    )
    output = await client.get_claim_timeline(GetClaimTimelineInput(id="clm_captured"))
    return timeline_from_sdk(output)


@pytest.fixture
def timeline() -> ClaimTimeline:
    return asyncio.run(_read_timeline())


class TestTheVendorStillSendsWhatWeRead:
    """The capture survives the vendor's deserialiser and reaches our shapes."""

    def test_every_entry_in_the_capture_is_read(self, timeline: ClaimTimeline) -> None:
        assert len(timeline.submissions) == 1
        assert len(timeline.acknowledgments) == 1
        assert len(timeline.payments) == 1

    def test_the_submission_carries_the_control_number_we_filed_under(
        self, timeline: ClaimTimeline
    ) -> None:
        submission = timeline.submissions[0]
        assert submission.patient_control_number
        assert submission.charged_cents == CHARGED_CENTS

    def test_amounts_arrive_as_cents_not_the_vendors_decimal_strings(
        self, timeline: ClaimTimeline
    ) -> None:
        payment = timeline.payments[0]
        assert payment.charged_cents == CHARGED_CENTS
        assert payment.paid_cents == CHARGED_CENTS
        assert payment.patient_responsibility_cents == 0

    def test_a_paid_claim_reads_as_money_that_moved(self, timeline: ClaimTimeline) -> None:
        payment = timeline.payments[0]
        assert payment.disposition == "paid"
        assert payment.is_money
        assert payment.trace_number, "a payment we can tie to funds must carry its trace"


class TestTheOrdinaryAcknowledgmentIsNotAMystery:
    """The first thing that happens to every healthy claim.

    The vendor's own name for it is ``RECEIVED`` — it has the claim and has
    not yet ruled on it. A mapping that did not know the word would read the
    ordinary case as ``unknown`` and leave every filed claim in a state the
    tracker cannot explain. This capture is where that was caught.
    """

    def test_it_reads_as_pending_rather_than_unknown(self, timeline: ClaimTimeline) -> None:
        assert timeline.acknowledgments[0].outcome == "pending"

    def test_it_names_who_said_so(self, timeline: ClaimTimeline) -> None:
        acknowledgment = timeline.acknowledgments[0]
        assert acknowledgment.reported_by == "CLEARINGHOUSE"
        assert acknowledgment.source_name


class TestWhatThePracticeWouldBePaid:
    """The whole point of reading a timeline: the posting it produces."""

    def test_a_claim_paid_in_full_posts_as_paid_in_full(self, timeline: ClaimTimeline) -> None:
        posting = posting_for(timeline, charged_cents=CHARGED_CENTS)

        assert posting is not None
        assert posting.event == "pay"
        assert posting.paid_cents == CHARGED_CENTS
        assert posting.patient_responsibility_cents == 0

    def test_the_posting_is_keyed_to_the_entry_it_came_from(self, timeline: ClaimTimeline) -> None:
        posting = posting_for(timeline, charged_cents=CHARGED_CENTS)

        assert posting is not None
        assert posting.source_id == timeline.payments[0].id

    def test_nothing_is_owed_by_the_client_on_a_claim_paid_in_full(
        self, timeline: ClaimTimeline
    ) -> None:
        posting = posting_for(timeline, charged_cents=CHARGED_CENTS)

        assert posting is not None
        assert posting.patient_responsibility_cents == 0


class TestWeHaveAnAnswerForEverythingTheVendorCanSay:
    """The vendor ships its own enums; our maps must cover them.

    Both maps fall back to a safe reading for a word they do not know, which
    is right at run time and useless as a check — it means a status the
    vendor adds is absorbed silently, and the first sign of it is a claim
    that never moves or money that never posts. Comparing against the SDK's
    enums turns that into a failing test on the next dependency bump, which
    is the only moment anyone is in a position to decide what the new word
    means.
    """

    def test_every_acknowledgment_status_the_vendor_defines_is_mapped(self) -> None:
        assert {status.value for status in ClaimAcknowledgmentStatus} == set(_OUTCOMES)

    def test_every_payment_status_the_vendor_defines_is_mapped(self) -> None:
        assert {status.value for status in ClaimPaymentInformationStatusCode} == set(_DISPOSITIONS)


class TestTheCaptureIsACapture:
    """Guards against somebody quietly replacing it with a hand-built one.

    Not a style rule: the file's value is entirely that a vendor produced it.
    A rewritten one would still pass every test above while proving nothing.
    """

    def test_it_carries_the_vendors_own_identifier_shapes(self) -> None:
        items = json.loads(CAPTURE.read_text())["items"]
        prefixes = {
            "professionalClaimSubmission": "sbm_",
            "claimAcknowledgment": "ack_",
            "claimPaymentInformation": "clp_",
        }
        seen = {key: entry[key]["id"] for entry in items for key in entry}
        assert set(seen) == set(prefixes)
        for key, identifier in seen.items():
            assert identifier.startswith(prefixes[key]), key
