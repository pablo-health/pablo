# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A clearinghouse that answers the claims pipeline from the recorded fixtures.

The submission answer is the recorded test-payer accept (or an edit
rejection, or an exception) re-keyed to the claim being filed; the
transaction feed and the 277CA reports are the recorded documents re-keyed
to whatever control number a test filed. Every call is recorded so a test
can assert what left the practice — above all, that a claim left it once.

Shared by the unit suite and the Postgres integration suite.
"""

from __future__ import annotations

import copy
import json
import secrets
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from app.claims.clearinghouse import ClearinghouseNotFoundError
from app.claims.events import (
    ClaimEvent,
    clear_claim_event_listeners,
    compliance_reminder_listener,
    register_claim_event_listener,
)
from app.claims.receipts import ClaimPipeline
from app.claims.submit_worker import SubmissionAccount
from app.models.claims_transport import (
    ClaimSubmissionRequest,
    ClaimSubmissionResult,
    TransactionDocument,
    TransactionPage,
)
from app.models.coverage import Payer
from app.repositories.claim_receipts import InMemoryClaimReceiptRepository
from app.repositories.claims import InMemoryClaimRepository
from app.repositories.coverage import InMemoryPayerRepository

from tests.claims_fixtures import BUILT_AT, PAYER_ROW_ID, USER_ID, claim, line

if TYPE_CHECKING:
    from app.models.claims import Claim
    from sqlalchemy.orm import Session

FIXTURES = Path(__file__).parent / "fixtures" / "clearinghouse"

NOW = datetime(2026, 9, 6, 16, 0, tzinfo=UTC)

AcknowledgmentKind = Literal["clearinghouse_forwarded", "payer_accepted", "payer_rejected"]

ACCOUNT = SubmissionAccount(
    usage_indicator="T",
    tax_id="84-4459714",
    submitter_identification="0000001",
    receiver_name="Stedi",
)

TEST_PAYER = Payer(
    id=PAYER_ROW_ID,
    name="Stedi Test Payer",
    payer_id="STEDI",
    clearinghouse_payer_id=None,
    timely_filing_days=90,
    corrected_claim_days=60,
    appeal_days=180,
    created_at=BUILT_AT,
    updated_at=BUILT_AT,
)


#: Letters only, and none that a hex digit or a fixture value could spell:
#: the tests assert that reminders and events carry no identifier, and a
#: random control number must never be the thing that spells one.
_CONTROL_ALPHABET = "GHJKLMNPQRSTUVWXYZ"


def fresh_control_number() -> str:
    return "PCN" + "".join(secrets.choice(_CONTROL_ALPHABET) for _ in range(9))


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


def submission_success(control_number: str) -> dict[str, Any]:
    body = fixture("837p_submission_success_test_payer.json")
    body["claimReference"]["patientControlNumber"] = control_number
    body["claimReference"]["serviceLines"] = [{"lineItemControlNumber": control_number + "L1"}]
    return body


def submission_edit_rejected(control_number: str) -> dict[str, Any]:
    body = fixture("837p_submission_edit_rejected_dx_specificity.json")
    body["claimReference"]["patientControlNumber"] = control_number
    return body


def _stamp(processed_at: datetime) -> str:
    return processed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def outbound_837(
    control_number: str,
    *,
    transaction_id: str | None = None,
    processed_at: datetime = NOW,
    correlation_id: str = "01M1VPM7E0T38G9WBVG4HN5C5Q",
) -> dict[str, Any]:
    document = fixture("transaction_outbound_837.json")
    document["transactionId"] = transaction_id or str(uuid.uuid4())
    document["processedAt"] = _stamp(processed_at)
    for identifier in document["businessIdentifiers"]:
        if identifier["element"] == "CLM-01":
            identifier["value"] = control_number
        if identifier["element"] == "BHT-03":
            identifier["value"] = correlation_id
    return document


def inbound_277(
    control_number: str, *, transaction_id: str | None = None, processed_at: datetime = NOW
) -> dict[str, Any]:
    document = fixture("transaction_inbound_277.json")
    document["transactionId"] = transaction_id or str(uuid.uuid4())
    document["processedAt"] = _stamp(processed_at)
    trn = [i for i in document["businessIdentifiers"] if i["element"] == "TRN-02"]
    trn[-1]["value"] = control_number
    return document


def acknowledgment_report(
    kind: AcknowledgmentKind, control_number: str, *, transaction_id: str
) -> dict[str, Any]:
    report = fixture(f"277ca_report_{kind}.json")
    report["meta"]["transactionId"] = transaction_id
    status = report["transactions"][0]["payers"][0]["claimStatusTransactions"][0][
        "claimStatusDetails"
    ][0]["patientClaimStatusDetails"][0]["claims"][0]["claimStatus"]
    status["referencedTransactionTraceNumber"] = control_number
    status["patientAccountNumber"] = control_number
    return report


class FakeClearinghouse:
    """The pipeline's half of the clearinghouse protocol, answered from fixtures.

    ``answers`` is what the next submissions get, in order: a response body,
    or an exception to raise. Empty means the recorded accept.
    """

    def __init__(self) -> None:
        self.submissions: list[tuple[ClaimSubmissionRequest, str]] = []
        self.answers: deque[dict[str, Any] | Exception] = deque()
        self.feed: list[dict[str, Any]] = []
        self.reports: dict[str, dict[str, Any]] = {}
        self.feed_reads = 0
        self.report_reads = 0

    # -- shaping the vendor's side --------------------------------------------

    def filed(self, control_number: str, **kwargs: Any) -> str:
        """The vendor has an outbound 837 for this claim in its feed."""
        document = outbound_837(control_number, **kwargs)
        self.feed.append(document)
        return str(document["transactionId"])

    def acknowledge(
        self,
        kind: AcknowledgmentKind,
        control_number: str,
        *,
        transaction_id: str | None = None,
        processed_at: datetime = NOW,
    ) -> str:
        """An inbound 277CA of ``kind`` for this claim lands in the feed."""
        document = inbound_277(
            control_number, transaction_id=transaction_id, processed_at=processed_at
        )
        transaction = str(document["transactionId"])
        self.feed.append(document)
        self.reports[transaction] = acknowledgment_report(
            kind, control_number, transaction_id=transaction
        )
        return transaction

    # -- ClearinghouseClient ---------------------------------------------------

    def submit_claim(
        self, req: ClaimSubmissionRequest, *, idempotency_key: str
    ) -> ClaimSubmissionResult:
        self.submissions.append((req, idempotency_key))
        answer = (
            self.answers.popleft()
            if self.answers
            else submission_success(req.claimInformation.patientControlNumber)
        )
        if isinstance(answer, Exception):
            raise answer
        return ClaimSubmissionResult.model_validate(answer)

    def list_transactions(
        self, *, start: datetime | None = None, page_token: str | None = None
    ) -> TransactionPage:
        del page_token
        self.feed_reads += 1
        items = [
            TransactionDocument.model_validate(document)
            for document in self.feed
            if start is None
            or datetime.fromisoformat(document["processedAt"].replace("Z", "+00:00")) >= start
        ]
        return TransactionPage(items=items, nextPageToken=None)

    def get_transaction(self, transaction_id: str) -> TransactionDocument:
        for document in self.feed:
            if document["transactionId"] == transaction_id:
                return TransactionDocument.model_validate(document)
        raise ClearinghouseNotFoundError("Transaction not found")

    def get_claim_acknowledgment(self, transaction_id: str) -> dict[str, Any]:
        self.report_reads += 1
        return copy.deepcopy(self.reports[transaction_id])

    # -- the rest of the protocol is never reached by the pipeline -------------

    def search_payers(self, query: str) -> Any:
        raise NotImplementedError

    def check_eligibility(self, req: Any) -> Any:
        raise NotImplementedError

    def create_provider(self, provider: Any) -> Any:
        raise NotImplementedError

    def create_enrollment(self, enrollment: Any) -> Any:
        raise NotImplementedError

    def list_enrollments(self, filters: Any) -> Any:
        raise NotImplementedError


class RecordingListener:
    """A claim event listener that keeps what it was handed."""

    def __init__(self) -> None:
        self.events: list[ClaimEvent] = []

    def __call__(self, session: Session, event: ClaimEvent) -> None:
        del session
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [event.kind for event in self.events]


@dataclass
class PipelineHarness:
    """In-memory repositories, a fake clearinghouse, and a recording listener."""

    pipeline: ClaimPipeline
    client: FakeClearinghouse
    claims: InMemoryClaimRepository
    receipts: InMemoryClaimReceiptRepository
    payers: InMemoryPayerRepository
    listener: RecordingListener
    commits: list[str] = field(default_factory=list)
    account: SubmissionAccount = ACCOUNT

    def add(self, **overrides: Any) -> Claim:
        """A claim in the repository, unique by id and control number."""
        claim_id = overrides.pop("id", str(uuid.uuid4()))
        control = overrides.pop("control_number", fresh_control_number())
        lines = overrides.pop(
            "lines",
            [
                line(
                    id=str(uuid.uuid4()),
                    claim_id=claim_id,
                    line_control_number=f"{control}L1",
                )
            ],
        )
        return self.claims.create(
            claim(id=claim_id, control_number=control, lines=lines, **overrides)
        )

    def get(self, claim_id: str) -> Claim:
        found = self.claims.get(claim_id)
        assert found is not None
        return found

    def commit(self) -> None:
        self.commits.append("commit")

    def practice_users(self) -> list[str]:
        return [self.pipeline.principal_user_id]


def make_harness(*, now: datetime = NOW, principal: str = USER_ID) -> PipelineHarness:
    """Wire the pipeline onto fakes. Clears the event listeners; see ``restore_listeners``."""
    clear_claim_event_listeners()
    listener = RecordingListener()
    register_claim_event_listener(listener)
    claims = InMemoryClaimRepository()
    receipts = InMemoryClaimReceiptRepository()
    payers = InMemoryPayerRepository()
    payers.create(TEST_PAYER)
    pipeline = ClaimPipeline(
        claims=claims,
        receipts=receipts,
        session=cast("Session", object()),
        principal_user_id=principal,
        now=lambda: now,
    )
    return PipelineHarness(
        pipeline=pipeline,
        client=FakeClearinghouse(),
        claims=claims,
        receipts=receipts,
        payers=payers,
        listener=listener,
    )


def restore_listeners() -> None:
    clear_claim_event_listeners()
    register_claim_event_listener(compliance_reminder_listener)
