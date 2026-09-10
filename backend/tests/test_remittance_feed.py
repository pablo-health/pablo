# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Finding the 835 that explains a claim's service lines.

The feed is the only place per-line adjudication lives, and reading it is the
expensive part of a remit pass — so the behaviour worth pinning down is not
just "does it find the claim" but "how often does it ask".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.claims.clearinghouse import ClearinghouseUnavailableError
from app.claims.remittance_feed import FeedRemittanceDetails
from app.models.claims_transport import TransactionPage

if TYPE_CHECKING:
    from datetime import datetime

    import pytest

_FIXTURES = Path(__file__).parent / "fixtures" / "clearinghouse"


def _fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((_FIXTURES / name).read_text())
    return body


def _feed_page() -> dict[str, Any]:
    """The recorded feed listing an outbound 837, an inbound 277 and an 835."""
    return _fixture("polling_transactions_277_and_835.json")


class _FakeClearinghouse:
    """Answers the two calls the detail source makes, and counts them."""

    def __init__(
        self,
        pages: list[dict[str, Any]] | None = None,
        *,
        report: dict[str, Any] | None = None,
        report_raises: Exception | None = None,
    ) -> None:
        self._pages = pages if pages is not None else [_feed_page()]
        self._report = report if report is not None else _fixture("835_report_paid_in_full.json")
        self._report_raises = report_raises
        self.list_calls = 0
        self.report_calls: list[str] = []

    def list_transactions(
        self, *, start: datetime | None = None, page_token: str | None = None
    ) -> TransactionPage:
        index = 0 if page_token is None else int(page_token)
        self.list_calls += 1
        body = dict(self._pages[index])
        body["nextPageToken"] = str(index + 1) if index + 1 < len(self._pages) else None
        return TransactionPage.model_validate(body)

    def get_remittance_report(self, transaction_id: str) -> dict[str, Any]:
        self.report_calls.append(transaction_id)
        if self._report_raises is not None:
            raise self._report_raises
        return self._report


def _control_number() -> str:
    """The control number the recorded 835 pays."""
    body = _fixture("835_report_paid_in_full.json")
    claims = body["transactions"][0]["detailInfo"][0]["paymentInfo"]
    number = claims[0]["claimPaymentInfo"]["patientControlNumber"]
    assert isinstance(number, str)
    return number


class TestFindingAClaimsDetail:
    def test_the_recorded_remittance_is_found_by_control_number(self) -> None:
        client = _FakeClearinghouse()
        details = FeedRemittanceDetails(client)  # type: ignore[arg-type] — a two-method fake

        found = details.detail_for(_control_number())

        assert found is not None
        assert found.patient_control_number == _control_number()
        assert found.lines

    def test_a_claim_the_feed_has_no_remittance_for_reads_as_nothing(self) -> None:
        client = _FakeClearinghouse()
        details = FeedRemittanceDetails(client)  # type: ignore[arg-type] — a two-method fake

        assert details.detail_for("NOSUCHCLAIM") is None

    def test_only_inbound_remittances_are_fetched(self) -> None:
        """The same feed carries the 837 we sent and the 277 that acknowledged
        it. Fetching those as remittances would be a wasted request each."""
        client = _FakeClearinghouse()
        details = FeedRemittanceDetails(client)  # type: ignore[arg-type] — a two-method fake

        details.detail_for(_control_number())

        assert len(client.report_calls) == 1


class TestHowOftenItAsks:
    """A pass answers one question per adjudicated claim off one scan."""

    def test_the_feed_is_read_once_however_many_claims_ask(self) -> None:
        client = _FakeClearinghouse()
        details = FeedRemittanceDetails(client)  # type: ignore[arg-type] — a two-method fake

        for _ in range(5):
            details.detail_for(_control_number())
            details.detail_for("NOSUCHCLAIM")

        assert client.list_calls == 1
        assert len(client.report_calls) == 1

    def test_a_pass_that_adjudicates_nothing_reads_nothing(self) -> None:
        """The source is built for every practice with a clearinghouse, and
        most passes have no newly-paid claim in them."""
        client = _FakeClearinghouse()

        FeedRemittanceDetails(client)  # type: ignore[arg-type] — a two-method fake

        assert client.list_calls == 0

    def test_it_follows_the_feed_to_the_end(self) -> None:
        client = _FakeClearinghouse([_feed_page(), _feed_page()])
        details = FeedRemittanceDetails(client)  # type: ignore[arg-type] — a two-method fake

        details.detail_for(_control_number())

        assert client.list_calls == 2

    def test_it_stops_rather_than_walking_a_whole_history(self) -> None:
        client = _FakeClearinghouse([_feed_page()] * 10)
        details = FeedRemittanceDetails(client, max_pages=3)  # type: ignore[arg-type]

        details.detail_for(_control_number())

        assert client.list_calls == 3


class TestOneBadRemittanceIsNotThePassFailing:
    def test_an_unreadable_report_leaves_the_claim_without_detail(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The claim-level posting still happens; only the breakdown is
        missing, and the next pass reads it again."""
        client = _FakeClearinghouse(report_raises=ClearinghouseUnavailableError("down"))
        details = FeedRemittanceDetails(client)  # type: ignore[arg-type] — a two-method fake

        with caplog.at_level("WARNING"):
            found = details.detail_for(_control_number())

        assert found is None
        assert "remittance_report_unreadable" in caplog.text
