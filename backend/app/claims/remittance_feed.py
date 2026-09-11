# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Finding the 835 that explains a claim, line by line.

The vendor's claim-lifecycle API answers "was this paid, and how much" per
claim, which is what the remit pass runs on. It does not answer "what
happened to each service", because its payment shape is one claim-level loop.
That detail exists only in the 835 document itself, which is reached through
the transaction feed.

So this is the one thing that still needs the feed, and it is worth saying why
plainly: it is not legacy code nobody got round to deleting. Until the vendor
reports service lines on the claim API, retiring the feed means giving up
knowing which session a payer denied.

The feed is read **once per pass**, not once per claim. A practice with forty
outstanding claims would otherwise scan the same pages forty times to answer
forty questions the first scan already had the answers to.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..utcnow import utc_now
from .clearinghouse import (
    ClearinghouseError,
    ClearinghouseNotFoundError,
    ClearinghouseReportUnreadableError,
)
from .responses import ParseError, parse_835

if TYPE_CHECKING:
    from ..models.claims_responses import Remittance, RemittanceClaim
    from .clearinghouse import ClearinghouseClient

logger = logging.getLogger(__name__)

#: The feed's transaction-set code for a remittance.
REMITTANCE_TRANSACTION_SET = "835"


def _processed_at(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class FetchedRemittance:
    """One inbound 835, parsed: which transaction it was and what it says."""

    transaction_id: str
    processed_at: datetime | None
    remittances: list[Remittance]


def fetch_remittance(client: ClearinghouseClient, transaction_id: str) -> FetchedRemittance | None:
    """The parsed 835 behind ``transaction_id``, or ``None`` if it is not one.

    The webhook's read of a single remittance the moment it arrives —
    :class:`FeedRemittanceDetails` above is the periodic pass's bulk read of
    a whole lookback window at once. Raises the adapter's typed errors the
    same way; a transaction another account owns is
    :class:`~app.claims.clearinghouse.ClearinghouseNotFoundError`, while a
    transaction that IS ours whose 835 cannot be read is
    :class:`~app.claims.clearinghouse.ClearinghouseReportUnreadableError`.
    """
    document = client.get_transaction(transaction_id)
    if document.direction != "INBOUND" or document.transaction_set != REMITTANCE_TRANSACTION_SET:
        return None
    try:
        report = client.get_remittance_report(transaction_id)
    except ClearinghouseNotFoundError as exc:
        raise ClearinghouseReportUnreadableError(
            f"no 835 report for transaction {transaction_id}"
        ) from exc
    return FetchedRemittance(
        transaction_id=transaction_id,
        processed_at=_processed_at(document.processedAt),
        remittances=parse_835(report),
    )


#: How far back a pass looks for remittances it has not read yet.
#:
#: Deliberately not "as far as possible". The feed pages oldest-first, so a
#: window wider than ``MAX_PAGES`` can hold does not degrade to "some of the
#: remittances" — it degrades to *the oldest* ones, which are the least
#: likely to be the ones a pass is asking about. Keeping the window narrow
#: enough to fit is what keeps the newest remittances reachable.
#:
#: Two weeks is comfortably wider than the gap between a payer paying and a
#: pass noticing, and reading one twice costs a request and changes nothing:
#: the posting itself is idempotent.
DEFAULT_LOOKBACK = timedelta(days=14)

#: A stop so a misconfigured account cannot walk the whole history. At the
#: feed's hundred-per-page this is two thousand transactions inside the
#: window — far more than a practice generates, and reached only when the
#: window is too wide rather than when the practice is too busy.
MAX_PAGES = 20


class FeedRemittanceDetails:
    """Service-line detail for the claims adjudicated in one pass.

    Scans the transaction feed lazily — the first question triggers the scan,
    and a pass where nothing was adjudicated asks nothing at all.
    """

    def __init__(
        self,
        client: ClearinghouseClient,
        *,
        lookback: timedelta = DEFAULT_LOOKBACK,
        max_pages: int = MAX_PAGES,
    ) -> None:
        self._client = client
        self._lookback = lookback
        self._max_pages = max_pages
        self._by_control_number: dict[str, RemittanceClaim] | None = None

    def detail_for(self, control_number: str) -> RemittanceClaim | None:
        if self._by_control_number is None:
            self._by_control_number = self._scan()
        return self._by_control_number.get(control_number)

    def _scan(self) -> dict[str, RemittanceClaim]:
        found: dict[str, RemittanceClaim] = {}
        for transaction_id in self._remittance_transaction_ids():
            try:
                remittances = parse_835(self._client.get_remittance_report(transaction_id))
            except (ClearinghouseError, ParseError):
                # One unreadable remittance is not the pass failing, and the
                # blast radius is the reason this is caught per transaction
                # rather than around the loop: a single malformed document
                # anywhere in the lookback window would otherwise blank the
                # service-line detail for every claim in it. The others still
                # explain their own claims, and this one is read again next
                # time.
                logger.warning("remittance_report_unreadable transaction_id=%s", transaction_id)
                continue
            for remittance in remittances:
                for claim in remittance.claims:
                    if claim.patient_control_number:
                        found[claim.patient_control_number] = claim
        logger.info("remittance_detail_scanned claims=%d", len(found))
        return found

    def _remittance_transaction_ids(self) -> list[str]:
        start = utc_now() - self._lookback
        ids: list[str] = []
        page_token: str | None = None
        for _ in range(self._max_pages):
            page = self._client.list_transactions(start=start, page_token=page_token)
            ids.extend(
                item.transactionId
                for item in page.items
                if item.direction == "INBOUND"
                and item.transaction_set == REMITTANCE_TRANSACTION_SET
            )
            page_token = page.nextPageToken
            if not page_token:
                return ids
        # Worth being loud about: the feed pages oldest-first, so stopping
        # here means the NEWEST remittances in the window were not read, and
        # those are the ones a pass is most likely to be asking about. The
        # fix is a narrower lookback, not a higher page cap.
        logger.warning(
            "remittance_feed_pages_exhausted pages=%d lookback_days=%d newest_unread=true",
            self._max_pages,
            self._lookback.days,
        )
        return ids
