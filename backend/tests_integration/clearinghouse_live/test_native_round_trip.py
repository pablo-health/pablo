# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A claim's whole life, through the code that lives it.

Everything else in this repository tests one link: the request we build, the
answer we read, the posting we derive. This runs the chain — file a claim
through the adapter, wait for the vendor's test payer to acknowledge and pay
it, read the claim's own timeline back, and check the money that falls out.

It is the only test that can catch the failure that matters most here: a
claim that files perfectly and can never be found again. That is not
hypothetical. Claims filed through the older endpoint do not appear on the
claim-lifecycle API at all, so every part of this could pass in isolation
while a practice's claims went out and nothing ever came back.

Slow by nature — the payer answers in tens of seconds — and skipped entirely
without a test key, like the rest of this lane.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.claims.remittance import posting_for
from app.claims.sdk_timeline import SdkClaimTimelines

from .conftest import fresh_control_number, fresh_idempotency_key, submission_body

if TYPE_CHECKING:
    from app.models.claims_timeline import ClaimTimeline

    from .conftest import LiveClient

#: The recorded test-payer claim bills a single 60-minute session.
_CHARGED_CENTS = 15_000

_ADJUDICATION_TIMEOUT_SECONDS = 240
_POLL_INTERVAL_SECONDS = 10


def _wait_for_adjudication(timelines: SdkClaimTimelines, claim_id: str) -> ClaimTimeline:
    deadline = time.monotonic() + _ADJUDICATION_TIMEOUT_SECONDS
    while True:
        timeline = timelines.timeline_for(claim_id)
        if timeline.payments:
            return timeline
        assert time.monotonic() < deadline, (
            f"the test payer did not adjudicate within "
            f"{_ADJUDICATION_TIMEOUT_SECONDS}s; saw "
            f"{len(timeline.submissions)} submission(s) and "
            f"{len(timeline.acknowledgments)} acknowledgement(s)"
        )
        time.sleep(_POLL_INTERVAL_SECONDS)


def test_a_filed_claim_can_be_found_again_and_pays(live: LiveClient) -> None:
    """File a claim, then read its money back off its own timeline.

    The assertion that carries the weight is the quiet one: the id the
    submission returned is an id the timeline API answers to. Everything
    downstream — acknowledgements, payments, the client's balance — hangs off
    that being true.
    """
    from app.models.claims_transport import ClaimSubmissionRequest  # noqa: PLC0415

    control_number = fresh_control_number()
    request = ClaimSubmissionRequest.model_validate(submission_body(control_number))

    result = live.adapter.submit_claim(request, idempotency_key=fresh_idempotency_key())

    assert result.status == "SUCCESS", [error.description for error in result.errors]
    assert result.claimReference is not None
    claim_id = result.claimReference.correlationId
    assert claim_id, "a filed claim must come back with the id its timeline is keyed on"

    timelines = SdkClaimTimelines(live.credentials)
    timeline = _wait_for_adjudication(timelines, claim_id)

    # The claim knows it was us who filed it.
    assert [s.patient_control_number for s in timeline.submissions] == [control_number]
    assert timeline.submissions[0].charged_cents == _CHARGED_CENTS

    # Somebody acknowledged it, and we have a word for whatever they said.
    assert timeline.acknowledgments
    assert all(ack.outcome != "unknown" for ack in timeline.acknowledgments), (
        "an acknowledgement status we have no word for leaves the claim in a "
        "state the tracker cannot explain"
    )

    posting = posting_for(timeline, charged_cents=_CHARGED_CENTS)

    assert posting is not None, "a paid claim must produce something to post"
    assert posting.event == "pay"
    assert posting.paid_cents == _CHARGED_CENTS
    assert posting.patient_responsibility_cents == 0
    assert posting.trace_number, "money that moved must carry the trace that proves it"


def test_the_payer_says_what_it_did_with_each_service(live: LiveClient) -> None:
    """The half the claim API cannot answer.

    A claim total says $180 of $300 was paid. Only the service lines say
    whether that was two sessions with a deductible applied or one paid and
    one denied — a conversation with the client versus a conversation with
    the payer. This proves the 835 is reachable for a claim we filed and
    lands on the line we billed.

    The test payer pays in full and adjusts nothing, so what this can prove
    is that the detail arrives and matches up. The contractual-versus-client
    split is exercised in the unit suite against constructed remittances and
    waits on a real payer.
    """
    from app.claims.remittance_feed import FeedRemittanceDetails  # noqa: PLC0415
    from app.claims.remittance_lines import postings_for  # noqa: PLC0415
    from app.models.claims_transport import ClaimSubmissionRequest  # noqa: PLC0415

    control_number = fresh_control_number()
    request = ClaimSubmissionRequest.model_validate(submission_body(control_number))
    result = live.adapter.submit_claim(request, idempotency_key=fresh_idempotency_key())
    assert result.status == "SUCCESS", [error.description for error in result.errors]

    deadline = time.monotonic() + _ADJUDICATION_TIMEOUT_SECONDS
    while True:
        # A fresh source each time: the scan is cached for a pass on purpose,
        # so re-asking the same one would answer from the first empty scan.
        detail = FeedRemittanceDetails(live.adapter).detail_for(control_number)
        if detail is not None:
            break
        assert time.monotonic() < deadline, (
            f"no 835 for the claim within {_ADJUDICATION_TIMEOUT_SECONDS}s"
        )
        time.sleep(_POLL_INTERVAL_SECONDS)

    postings = postings_for(detail)

    assert list(postings) == [control_number + "L1"], (
        "the payer's service line must carry back the line control number we billed under"
    )
    [posting] = postings.values()
    assert posting.paid_cents == _CHARGED_CENTS
    # This payer reports no allowed amount, so we report none either. The
    # temptation is to infer it from the charge and the adjustments; that
    # inference is wrong out of network, under Medicare sequestration and on
    # secondary claims, so an unreported allowance stays unreported.
    assert posting.allowed_cents is None
    assert posting.patient_responsibility_cents == 0


def test_the_same_key_files_one_claim_not_two(live: LiveClient) -> None:
    """The property the submission worker's retries depend on.

    A submission that times out is retried with the same key. If the vendor
    treated the retry as a new claim, the payer would receive the session
    twice and the practice would be paid twice for work done once — the kind
    of error that is found by an audit, not by a test.
    """
    from app.models.claims_transport import ClaimSubmissionRequest  # noqa: PLC0415

    request = ClaimSubmissionRequest.model_validate(submission_body(fresh_control_number()))
    key = fresh_idempotency_key()

    first = live.adapter.submit_claim(request, idempotency_key=key)
    again = live.adapter.submit_claim(request, idempotency_key=key)

    assert first.claimReference is not None
    assert again.claimReference is not None
    assert again.claimReference.correlationId == first.claimReference.correlationId
