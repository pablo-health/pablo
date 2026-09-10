# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A clearinghouse that answers enrollment calls from the recorded fixtures.

The vendor's enrollment API refuses test-mode keys, so the only way the
enrollment lifecycle runs outside production is against what was recorded
through production credentials once (``tests/fixtures/clearinghouse``). This
fake plays those back: the payer directory's answer for the test payer, the
provider record, and one enrollment per request with a distinct vendor id
so several requests can sit side by side. ``listing`` is whatever the test
wants the next status poll to say.

Answering a task is the one part that has to behave rather than replay: the
fake keeps each enrollment it handed out, takes documents through the
vendor's two steps, and refuses — as the vendor does — to complete a task
against a document whose bytes never arrived.

Shared by the unit suite and the Postgres integration suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.claims.clearinghouse import ClearinghouseError
from app.models.claims_transport import (
    DocumentDownload,
    DocumentUpload,
    Enrollment,
    EnrollmentFilters,
    EnrollmentPage,
    EnrollmentRequest,
    Payer,
    ProviderRecord,
    ProviderRegistration,
    TaskCompletion,
)

FIXTURES = Path(__file__).parent / "fixtures" / "clearinghouse"

PROVIDER_ID = "01a0746f-25d4-78a0-bb43-0f95acd218c9"
TEST_PAYER_ID = "STEDI"
TEST_PAYER_STEDI_ID = "FRCPB"
INSTRUCTIONS = "Sign the EFT authorization form and upload the signed copy."
#: The base the fake claims to serve the enrollment API from. Task links
#: under it are the clearinghouse's own and need the key; anything else is
#: an ordinary web link.
ENROLLMENTS_BASE = "https://enrollments.example.test/2024-09-01"


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
    ``page_size`` splits ``listing`` into pages the way the vendor does,
    with the page token an offset into it; ``None`` answers in one page.
    """

    def __init__(self, *, transaction_support: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.listing: list[dict[str, Any]] = []
        self.page_size: int | None = None
        self._support = transaction_support
        self._next_vendor_id = 0
        #: Every enrollment this fake has handed out, by vendor id, as the
        #: mutable dict a task answer changes. ``get_enrollment`` reads it,
        #: so a completed task is visible on the next poll the way it is at
        #: the vendor.
        self.enrollments: dict[str, dict[str, Any]] = {}
        self._next_document_id = 0

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
        vendor_id = f"enr-{self._next_vendor_id:04d}"
        record = enrollment_fixture(vendor_id=vendor_id, transaction=transaction)
        self.enrollments[vendor_id] = record
        return Enrollment.model_validate(record)

    def list_enrollments(self, filters: EnrollmentFilters) -> EnrollmentPage:
        self.calls.append(("list_enrollments", filters))
        items = [Enrollment.model_validate(item) for item in self.listing]
        if self.page_size is None:
            return EnrollmentPage(items=items)
        start = int(filters.pageToken or 0)
        end = start + self.page_size
        return EnrollmentPage(
            items=items[start:end], nextPageToken=str(end) if end < len(items) else None
        )

    # -- what the payer is waiting for -----------------------------------------

    def wants_a_signed_form(self, vendor_id: str) -> dict[str, Any]:
        """Put this request into "the payer needs something from you"."""
        record = enrollment_fixture(vendor_id=vendor_id, status="PROVIDER_ACTION_REQUIRED")
        self.enrollments[vendor_id] = record
        return record

    def _require_enrollment(self, enrollment_id: str) -> dict[str, Any]:
        record = self.enrollments.get(enrollment_id)
        if record is None:
            msg = f"no such enrollment: {enrollment_id}"
            raise ClearinghouseError(msg)
        return record

    def get_enrollment(self, enrollment_id: str) -> Enrollment:
        self.calls.append(("get_enrollment", enrollment_id))
        return Enrollment.model_validate(self._require_enrollment(enrollment_id))

    def upload_enrollment_document(
        self, enrollment_id: str, *, name: str, task_id: str
    ) -> DocumentUpload:
        self.calls.append(("upload_enrollment_document", (enrollment_id, name, task_id)))
        record = self._require_enrollment(enrollment_id)
        self._next_document_id += 1
        document_id = f"doc-{self._next_document_id:04d}"
        record.setdefault("documents", []).append(
            {"id": document_id, "name": name, "status": "PENDING"}
        )
        return DocumentUpload(
            enrollmentId=enrollment_id,
            uploadUrl=f"https://uploads.test/{document_id}",
            documentId=document_id,
        )

    def put_document(self, upload_url: str, content: bytes) -> None:
        self.calls.append(("put_document", (upload_url, len(content))))
        document_id = upload_url.rsplit("/", 1)[-1]
        for record in self.enrollments.values():
            for document in record.get("documents", []):
                if document["id"] == document_id:
                    document["status"] = "UPLOADED"
                    return
        msg = f"nothing is expecting {document_id}"
        raise ClearinghouseError(msg)

    def download_enrollment_document(self, document_id: str) -> DocumentDownload:
        self.calls.append(("download_enrollment_document", document_id))
        return DocumentDownload(downloadUrl=f"https://downloads.test/{document_id}?sig=abc")

    def hosts_enrollment_documents(self, url: str) -> bool:
        return url.startswith(f"{ENROLLMENTS_BASE}/")

    def resolve_enrollment_link(self, url: str) -> DocumentDownload:
        self.calls.append(("resolve_enrollment_link", url))
        if not self.hosts_enrollment_documents(url):
            msg = "not ours to fetch"
            raise ClearinghouseError(msg)
        return DocumentDownload(downloadUrl=f"https://downloads.test/{url.rsplit('/', 1)[-1]}")

    def complete_enrollment_task(self, task_id: str, completion: TaskCompletion) -> None:
        """Mark it done — refusing, as the vendor does, a document not yet taken."""
        self.calls.append(("complete_enrollment_task", (task_id, completion)))
        manual = completion.responseData.manualTask if completion.responseData else None
        named = {
            value.value.document.documentId
            for value in (manual.values if manual else [])
            if value.value.document is not None
        }
        for record in self.enrollments.values():
            for task in record.get("tasks", []):
                if task["id"] != task_id:
                    continue
                taken = {
                    document["id"]
                    for document in record.get("documents", [])
                    if document["status"] == "UPLOADED"
                }
                if named - taken:
                    msg = "document is not uploaded"
                    raise ClearinghouseError(msg)
                task["isComplete"] = True
                if all(
                    other["isComplete"]
                    for other in record["tasks"]
                    if other["responsibleParty"] == "PROVIDER"
                ):
                    record["status"] = "PROVISIONING"
                return
        msg = f"no such task: {task_id}"
        raise ClearinghouseError(msg)

    # -- the rest of the protocol is never reached by enrollment ---------------

    def check_eligibility(self, req: Any) -> Any:
        raise NotImplementedError

    def submit_claim(self, req: Any, *, idempotency_key: str) -> Any:
        raise NotImplementedError

    def get_transaction(self, transaction_id: str) -> Any:
        raise NotImplementedError
