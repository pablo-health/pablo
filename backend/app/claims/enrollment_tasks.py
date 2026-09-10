# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Answering what a payer asks for before it will accept a practice's claims.

An enrollment stops and waits whenever the payer wants something from the
practice — a signed agreement, a Medicaid id, a voided cheque. The
clearinghouse carries that as a *task* on the enrollment, and until somebody
answers it nothing moves. This is how a therapist answers one without leaving
Pablo and without a clearinghouse login.

A task is a small form. ``fields`` says what it wants:

* **nothing at all** — the whole task is its instructions, done somewhere
  else: a payer's portal, a phone call. Completing it asserts the practice
  did that, and the vendor is told nothing more.
* **text** — values the practice types.
* **a document** — a PDF, uploaded.
* **both**, in one task, answered together.

The order matters and is not ours to choose
-------------------------------------------

A PDF becomes usable in three steps, and the last two cannot be swapped: ask
the clearinghouse where to put it, write the bytes there, and only then —
once it says the document is ``UPLOADED`` rather than ``PENDING`` — complete
the task quoting it. Completing early points the payer at a document that is
not there yet, and the enrollment fails on the payer's schedule rather than
on ours, days later, for a reason nobody can see from here.

So :func:`answer_task` refuses to complete until every document it uploaded
has settled, and raises rather than completing if one comes back ``FAILED``.

What is in these documents
--------------------------

Practice paperwork — a W-9, a signed agreement, a voided cheque. Not a
patient's anything. They do carry the practice's tax id and somebody's
signature, so the bytes are never logged, never put in an error message, and
never kept here after the upload: the clearinghouse stores them and hands
back a short-lived URL when they are wanted again.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..models.claims_transport import (
    ManualTaskResponse,
    TaskCompletion,
    TaskDocumentRef,
    TaskFieldAnswer,
    TaskFieldValue,
    TaskResponseData,
)
from .clearinghouse import ClearinghouseError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ..models.claims_transport import Enrollment, EnrollmentTask
    from .clearinghouse import ClearinghouseClient

logger = logging.getLogger(__name__)

#: How long to wait for the clearinghouse to accept a PDF it just took. Its
#: own guidance is "typically less than 10 seconds"; this is generous enough
#: to cover a slow one and short enough that a therapist is not left
#: watching a spinner over a request.
SETTLE_TIMEOUT_SECONDS = 30.0
SETTLE_INTERVAL_SECONDS = 1.0

_UPLOADED = "UPLOADED"
_FAILED = "FAILED"


class TaskAnswerError(Exception):
    """The task could not be answered, and nothing was completed."""


class MissingAnswerError(TaskAnswerError):
    """A field the task asks for was not answered.

    Every field is required — the vendor will not accept a partial answer —
    so this is refused here rather than sent and rejected.
    """

    def __init__(self, keys: list[str]) -> None:
        super().__init__("no answer for: " + ", ".join(sorted(keys)))
        self.keys = sorted(keys)


class DocumentRejectedError(TaskAnswerError):
    """The clearinghouse could not take a PDF, so the task stays open."""


@dataclass(frozen=True, slots=True)
class Upload:
    """A PDF the practice is answering a document field with."""

    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class Answer:
    """What the practice is saying back, keyed by the task's own field keys.

    Anything the task did not ask for is ignored; anything it asked for and
    is missing here is refused before a byte is sent.
    """

    text: Mapping[str, str] = field(default_factory=dict)
    documents: Mapping[str, Upload] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Poll:
    """How long to wait for the clearinghouse to accept a PDF.

    ``sleep`` is injected so a test can prove the waiting without doing any.
    """

    timeout: float = SETTLE_TIMEOUT_SECONDS
    interval: float = SETTLE_INTERVAL_SECONDS
    sleep: Callable[[float], None] = time.sleep


def _settle(
    client: ClearinghouseClient,
    enrollment_id: str,
    document_ids: set[str],
    poll: Poll,
) -> None:
    """Wait until every uploaded document is accepted, or say why not.

    Polls the enrollment rather than the document: the clearinghouse reports
    a document's status as part of the enrollment it belongs to, so one read
    settles all of them however many were uploaded.
    """
    deadline = time.monotonic() + poll.timeout
    pending = set(document_ids)
    while pending:
        enrollment = client.get_enrollment(enrollment_id)
        for document_id in list(pending):
            document = enrollment.document(document_id)
            if document is None:
                continue
            if document.status == _UPLOADED:
                pending.discard(document_id)
            elif document.status == _FAILED:
                msg = "the clearinghouse could not read the document"
                raise DocumentRejectedError(msg)
        if not pending:
            return
        if time.monotonic() >= deadline:
            msg = "the clearinghouse has not accepted the document yet"
            raise DocumentRejectedError(msg)
        poll.sleep(poll.interval)


def answer_task(
    client: ClearinghouseClient,
    enrollment: Enrollment,
    task: EnrollmentTask,
    answer: Answer,
    *,
    poll: Poll | None = None,
) -> None:
    """Answer one task and mark it done. Raises rather than half-completing."""
    # Trimmed on the way in: a field holding spaces is not an answer, and
    # sending one gets it back from the payer weeks later as a rejection.
    text = {key: value.strip() for key, value in answer.text.items()}
    documents = dict(answer.documents)

    missing = [
        wanted.key
        for wanted in task.fields
        if (wanted.fieldType == "TEXT" and not text.get(wanted.key))
        or (wanted.fieldType == "DOCUMENT" and wanted.key not in documents)
    ]
    if missing:
        raise MissingAnswerError(missing)

    if not task.fields:
        # Nothing to say. The completion itself is the answer.
        client.complete_enrollment_task(task.id, TaskCompletion())
        logger.info("enrollment_task_completed task_id=%s fields=0", task.id)
        return

    uploaded: dict[str, str] = {}
    for wanted in task.fields:
        if wanted.fieldType != "DOCUMENT":
            continue
        upload = documents[wanted.key]
        try:
            slot = client.upload_enrollment_document(
                enrollment.id, name=upload.filename, task_id=task.id
            )
            client.put_document(slot.uploadUrl, upload.content)
        except ClearinghouseError as exc:
            logger.warning(
                "enrollment_document_upload_failed task_id=%s field=%s error=%s",
                task.id,
                wanted.key,
                type(exc).__name__,
            )
            msg = "the document could not be sent to the clearinghouse"
            raise DocumentRejectedError(msg) from exc
        uploaded[wanted.key] = slot.documentId

    if uploaded:
        _settle(client, enrollment.id, set(uploaded.values()), poll or Poll())

    answers = [
        TaskFieldAnswer(
            key=wanted.key,
            value=(
                TaskFieldValue(document=TaskDocumentRef(documentId=uploaded[wanted.key]))
                if wanted.fieldType == "DOCUMENT"
                else TaskFieldValue(text=text[wanted.key])
            ),
        )
        for wanted in task.fields
    ]
    client.complete_enrollment_task(
        task.id,
        TaskCompletion(
            responseData=TaskResponseData(manualTask=ManualTaskResponse(values=answers))
        ),
    )
    logger.info(
        "enrollment_task_completed task_id=%s fields=%d documents=%d",
        task.id,
        len(task.fields),
        len(uploaded),
    )
