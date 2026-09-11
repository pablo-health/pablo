# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Answering a payer's enrollment task without leaving Pablo.

The failure that matters here is not an exception — it is a task marked
complete against a document the clearinghouse has not accepted, which the
payer discovers days later and answers by rejecting the enrollment. So most
of what is asserted below is *ordering*: what was sent, in what sequence, and
what was never sent at all when something went wrong.

The vendor refuses its enrollment API to test keys, so these run against a
fake built to the documented shapes rather than a recording. That is stated
plainly because it is the weakness of this file: it proves we obey the
protocol we read, not that we read it right. The first real payer task is
what confirms the second.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.claims.clearinghouse import ClearinghouseUnavailableError
from app.claims.enrollment_tasks import (
    Answer,
    DocumentRejectedError,
    MissingAnswerError,
    Poll,
    Upload,
    answer_task,
)
from app.models.claims_transport import DocumentUpload, Enrollment, EnrollmentTask

if TYPE_CHECKING:
    from app.models.claims_transport import TaskCompletion

PDF = b"%PDF-1.4 not really a pdf"


def _task(*fields: dict[str, str], task_id: str = "task-1") -> EnrollmentTask:
    return EnrollmentTask.model_validate(
        {
            "id": task_id,
            "responsibleParty": "PROVIDER",
            "isComplete": False,
            "rank": 0,
            "definition": {
                "manualTask": {
                    "instructions": "Do the thing",
                    "fields": list(fields),
                }
            },
        }
    )


def _enrollment(documents: list[dict[str, Any]] | None = None) -> Enrollment:
    return Enrollment.model_validate(
        {
            "id": "enr-1",
            "status": "PROVIDER_ACTION_REQUIRED",
            "payer": {
                "name": "Test Payer",
                "stediPayerId": "P1",
                "submittedPayerIdOrAlias": "P1",
            },
            "provider": {
                "id": "prov-1",
                "name": "Pablo Test Practice",
                "npi": "1999999984",
                "taxId": "123456789",
                "taxIdType": "EIN",
            },
            "statusLastUpdatedAt": "2026-09-10T00:00:00Z",
            "documents": documents or [],
        }
    )


class _Clearinghouse:
    """A stand-in built to the vendor's documented enrollment shapes.

    Records everything in one ordered log, because the order is the thing
    under test.
    """

    def __init__(self, *, settles_after: int = 1, fails: bool = False) -> None:
        self.log: list[tuple[str, Any]] = []
        self.completions: list[TaskCompletion] = []
        self._settles_after = settles_after
        self._fails = fails
        self._reads = 0
        self._documents: list[dict[str, Any]] = []

    def upload_enrollment_document(
        self, enrollment_id: str, *, name: str, task_id: str
    ) -> DocumentUpload:
        document_id = f"doc-{len(self._documents) + 1}"
        self.log.append(("ask", (enrollment_id, name, task_id, document_id)))
        self._documents.append({"id": document_id, "name": name, "status": "PENDING"})
        return DocumentUpload(
            enrollmentId=enrollment_id,
            uploadUrl=f"https://s3.example.test/{document_id}?signed",
            documentId=document_id,
        )

    def put_document(self, upload_url: str, content: bytes) -> None:
        self.log.append(("put", (upload_url, len(content))))

    def get_enrollment(self, enrollment_id: str) -> Enrollment:
        self._reads += 1
        self.log.append(("read", enrollment_id))
        status = (
            "FAILED"
            if self._fails
            else ("UPLOADED" if self._reads >= self._settles_after else "PENDING")
        )
        return _enrollment([{**doc, "status": status} for doc in self._documents])

    def complete_enrollment_task(self, task_id: str, completion: TaskCompletion) -> None:
        self.log.append(("complete", task_id))
        self.completions.append(completion)

    @property
    def steps(self) -> list[str]:
        return [step for step, _ in self.log]


def _answered(client: _Clearinghouse) -> list[dict[str, Any]]:
    """The values on the one completion that was sent."""
    assert len(client.completions) == 1
    data = client.completions[0].responseData
    assert data is not None
    return [value.model_dump(exclude_none=True) for value in data.manualTask.values]


class TestATaskThatOnlyWantsAnAction:
    """No fields: the whole task is its instructions."""

    def test_it_is_completed_with_nothing_attached(self) -> None:
        client = _Clearinghouse()

        answer_task(client, _enrollment(), _task(), Answer())  # type: ignore[arg-type] — a fake

        assert client.steps == ["complete"]
        assert client.completions[0].completed is True
        assert client.completions[0].responseData is None, (
            "an empty values list would claim to have answered nothing, rather "
            "than that there was nothing to answer"
        )


class TestATaskThatWantsText:
    def test_the_value_is_filed_under_the_vendors_own_key(self) -> None:
        client = _Clearinghouse()
        task = _task({"key": "MEDICAID_ID", "label": "Medicaid ID", "fieldType": "TEXT"})

        answer_task(client, _enrollment(), task, Answer(text={"MEDICAID_ID": "ABCD12"}))  # type: ignore[arg-type]

        assert client.steps == ["complete"], "no document, so nothing to upload or wait for"
        assert _answered(client) == [{"key": "MEDICAID_ID", "value": {"text": "ABCD12"}}]

    def test_an_unanswered_field_is_refused_before_anything_is_sent(self) -> None:
        client = _Clearinghouse()
        task = _task({"key": "MEDICAID_ID", "label": "Medicaid ID", "fieldType": "TEXT"})

        with pytest.raises(MissingAnswerError) as raised:
            answer_task(client, _enrollment(), task, Answer())  # type: ignore[arg-type]

        assert raised.value.keys == ["MEDICAID_ID"]
        assert client.log == [], "the task must not be touched when the answer is short"

    def test_a_blank_string_is_not_an_answer(self) -> None:
        client = _Clearinghouse()
        task = _task({"key": "MEDICAID_ID", "label": "Medicaid ID", "fieldType": "TEXT"})

        with pytest.raises(MissingAnswerError):
            answer_task(client, _enrollment(), task, Answer(text={"MEDICAID_ID": "   "}))  # type: ignore[arg-type]


class TestATaskThatWantsAPdf:
    """The ordering rule, which is the reason this module exists."""

    def test_the_document_is_uploaded_and_settled_before_the_task_is_completed(self) -> None:
        client = _Clearinghouse(settles_after=1)
        task = _task({"key": "ENROLLMENT_FORM", "label": "Signed PDF", "fieldType": "DOCUMENT"})

        answer_task(
            client,  # type: ignore[arg-type]
            _enrollment(),
            task,
            Answer(documents={"ENROLLMENT_FORM": Upload("signed.pdf", PDF)}),
            poll=Poll(sleep=lambda _: None),
        )

        assert client.steps == ["ask", "put", "read", "complete"], (
            "completing before the clearinghouse has the bytes points the payer "
            "at a document that is not there"
        )
        assert _answered(client) == [
            {"key": "ENROLLMENT_FORM", "value": {"document": {"documentId": "doc-1"}}}
        ]

    def test_it_waits_while_the_document_is_still_pending(self) -> None:
        client = _Clearinghouse(settles_after=3)
        task = _task({"key": "ENROLLMENT_FORM", "label": "Signed PDF", "fieldType": "DOCUMENT"})
        slept: list[float] = []

        answer_task(
            client,  # type: ignore[arg-type]
            _enrollment(),
            task,
            Answer(documents={"ENROLLMENT_FORM": Upload("signed.pdf", PDF)}),
            poll=Poll(sleep=slept.append),
        )

        assert client.steps.count("read") == 3
        assert slept, "a pending document must be waited on, not spun on"
        assert client.steps[-1] == "complete"

    def test_a_document_the_clearinghouse_rejects_leaves_the_task_open(self) -> None:
        client = _Clearinghouse(fails=True)
        task = _task({"key": "ENROLLMENT_FORM", "label": "Signed PDF", "fieldType": "DOCUMENT"})

        with pytest.raises(DocumentRejectedError):
            answer_task(
                client,  # type: ignore[arg-type]
                _enrollment(),
                task,
                Answer(documents={"ENROLLMENT_FORM": Upload("signed.pdf", PDF)}),
                poll=Poll(sleep=lambda _: None),
            )

        assert "complete" not in client.steps, (
            "a task completed against a failed document is an enrollment that "
            "dies at the payer, days later, for a reason nobody can see here"
        )

    def test_a_document_that_never_settles_times_out_rather_than_completing(self) -> None:
        client = _Clearinghouse(settles_after=10_000)
        task = _task({"key": "ENROLLMENT_FORM", "label": "Signed PDF", "fieldType": "DOCUMENT"})

        with pytest.raises(DocumentRejectedError):
            answer_task(
                client,  # type: ignore[arg-type]
                _enrollment(),
                task,
                Answer(documents={"ENROLLMENT_FORM": Upload("signed.pdf", PDF)}),
                poll=Poll(timeout=0.0, sleep=lambda _: None),
            )

        assert "complete" not in client.steps

    def test_an_upload_the_store_refuses_never_completes_the_task(self) -> None:
        class _Refuses(_Clearinghouse):
            def put_document(self, upload_url: str, content: bytes) -> None:
                self.log.append(("put", "refused"))
                raise ClearinghouseUnavailableError("the store said no")

        client = _Refuses()
        task = _task({"key": "ENROLLMENT_FORM", "label": "Signed PDF", "fieldType": "DOCUMENT"})

        with pytest.raises(DocumentRejectedError):
            answer_task(
                client,  # type: ignore[arg-type]
                _enrollment(),
                task,
                Answer(documents={"ENROLLMENT_FORM": Upload("signed.pdf", PDF)}),
                poll=Poll(sleep=lambda _: None),
            )

        assert client.steps == ["ask", "put"]

    def test_a_missing_pdf_is_refused_before_a_slot_is_asked_for(self) -> None:
        client = _Clearinghouse()
        task = _task({"key": "ENROLLMENT_FORM", "label": "Signed PDF", "fieldType": "DOCUMENT"})

        with pytest.raises(MissingAnswerError):
            answer_task(client, _enrollment(), task, Answer())  # type: ignore[arg-type]

        assert client.log == []


class TestATaskThatWantsBoth:
    """The vendor's mixed example: a Medicaid id and a signed agreement."""

    def test_every_value_is_sent_together_after_the_pdf_settles(self) -> None:
        client = _Clearinghouse()
        task = _task(
            {"key": "MEDICAID_ID", "label": "Medicaid ID", "fieldType": "TEXT"},
            {"key": "AGREEMENT_PDF", "label": "Enrollment PDF", "fieldType": "DOCUMENT"},
        )

        answer_task(
            client,  # type: ignore[arg-type]
            _enrollment(),
            task,
            Answer(
                text={"MEDICAID_ID": "ABCD12"},
                documents={"AGREEMENT_PDF": Upload("agreement.pdf", PDF)},
            ),
            poll=Poll(sleep=lambda _: None),
        )

        assert client.steps == ["ask", "put", "read", "complete"]
        assert _answered(client) == [
            {"key": "MEDICAID_ID", "value": {"text": "ABCD12"}},
            {"key": "AGREEMENT_PDF", "value": {"document": {"documentId": "doc-1"}}},
        ]

    def test_a_missing_half_refuses_the_whole_thing(self) -> None:
        client = _Clearinghouse()
        task = _task(
            {"key": "MEDICAID_ID", "label": "Medicaid ID", "fieldType": "TEXT"},
            {"key": "AGREEMENT_PDF", "label": "Enrollment PDF", "fieldType": "DOCUMENT"},
        )

        with pytest.raises(MissingAnswerError) as raised:
            answer_task(
                client,  # type: ignore[arg-type]
                _enrollment(),
                task,
                Answer(text={"MEDICAID_ID": "ABCD12"}),
            )

        assert raised.value.keys == ["AGREEMENT_PDF"]
        assert client.log == [], "the text half must not be sent on its own"


class TestWhichTasksAreOurs:
    def test_only_open_provider_tasks_are_shown_and_in_the_vendors_order(self) -> None:
        enrollment = Enrollment.model_validate(
            {
                **_enrollment().model_dump(),
                "tasks": [
                    {"id": "b", "responsibleParty": "PROVIDER", "rank": 2},
                    {"id": "a", "responsibleParty": "PROVIDER", "rank": 1},
                    {"id": "done", "responsibleParty": "PROVIDER", "rank": 0, "isComplete": True},
                    {"id": "theirs", "responsibleParty": "STEDI", "rank": 0},
                ],
            }
        )

        assert [task.id for task in enrollment.open_tasks()] == ["a", "b"]
