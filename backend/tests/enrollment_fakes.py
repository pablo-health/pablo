# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A clearinghouse that answers enrollment calls from the recorded fixtures.

The vendor's enrollment API refuses test-mode keys, so the only way the
enrollment lifecycle runs outside production is against what was recorded
through production credentials once (``tests/fixtures/clearinghouse``). This
fake plays those back: the payer directory's answer for the test payer, the
provider record, and one enrollment per request with a distinct vendor id
so several requests can sit side by side. ``listing`` is whatever the test
wants the next status poll to say.

Shared by the unit suite and the Postgres integration suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.claims_transport import (
    Enrollment,
    EnrollmentFilters,
    EnrollmentRequest,
    Payer,
    ProviderRecord,
    ProviderRegistration,
)

FIXTURES = Path(__file__).parent / "fixtures" / "clearinghouse"

PROVIDER_ID = "01a0746f-25d4-78a0-bb43-0f95acd218c9"
TEST_PAYER_ID = "STEDI"
TEST_PAYER_STEDI_ID = "FRCPB"
INSTRUCTIONS = "Sign the EFT authorization form and upload the signed copy."


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


def enrollment_fixture(
    *,
    vendor_id: str,
    status: str = "STEDI_ACTION_REQUIRED",
    transaction: str = "claimPayment",
) -> dict[str, Any]:
    """The recorded 835 enrollment, re-keyed to another request or status.

    ``PROVIDER_ACTION_REQUIRED`` comes from the constructed fixture that
    carries the vendor's tasks and reason; every other status is the
    recorded answer with ``status`` swapped, which is all the vendor changes
    between polls for those.
    """
    if status == "PROVIDER_ACTION_REQUIRED":
        data = fixture("enrollment_provider_action_required.json")
    else:
        data = fixture("enrollment_create_enrollment_835.json")
        data["status"] = status
    data["id"] = vendor_id
    data["transactions"] = {transaction: {"enroll": True}}
    return data


class FakeClearinghouse:
    """Enrollment calls answered from fixtures; every call recorded.

    ``transaction_support`` overrides the directory's answer for the test
    payer so a test can make claims or eligibility need an enrollment too.
    """

    def __init__(self, *, transaction_support: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.listing: list[dict[str, Any]] = []
        self._support = transaction_support
        self._next_vendor_id = 0

    # -- what a test reads back ------------------------------------------------

    def calls_named(self, name: str) -> list[Any]:
        return [payload for called, payload in self.calls if called == name]

    # -- ClearinghouseClient, the enrollment half ------------------------------

    def search_payers(self, query: str) -> list[Payer]:
        self.calls.append(("search_payers", query))
        hits = [
            Payer.model_validate(item["payer"])
            for item in fixture("payer_search_test_payer.json")["items"]
        ]
        if self._support is not None:
            hits = [
                h.model_copy(update={"transactionSupport": self._support})
                if h.primaryPayerId == TEST_PAYER_ID
                else h
                for h in hits
            ]
        return hits

    def create_provider(self, provider: ProviderRegistration) -> ProviderRecord:
        self.calls.append(("create_provider", provider))
        return ProviderRecord.model_validate(fixture("enrollment_create_provider.json"))

    def create_enrollment(self, enrollment: EnrollmentRequest) -> Enrollment:
        self.calls.append(("create_enrollment", enrollment))
        self._next_vendor_id += 1
        [transaction] = [
            name for name, flag in enrollment.transactions.model_dump().items() if flag
        ]
        return Enrollment.model_validate(
            enrollment_fixture(vendor_id=f"enr-{self._next_vendor_id:04d}", transaction=transaction)
        )

    def list_enrollments(self, filters: EnrollmentFilters) -> list[Enrollment]:
        self.calls.append(("list_enrollments", filters))
        return [Enrollment.model_validate(item) for item in self.listing]

    # -- the rest of the protocol is never reached by enrollment ---------------

    def check_eligibility(self, req: Any) -> Any:
        raise NotImplementedError

    def submit_claim(self, req: Any, *, idempotency_key: str) -> Any:
        raise NotImplementedError

    def get_transaction(self, transaction_id: str) -> Any:
        raise NotImplementedError
