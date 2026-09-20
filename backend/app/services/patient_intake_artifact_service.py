# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake artifacts — the files a form asked for, and the plan on the card.

Two things a patient does on a form that the ordinary save route cannot do,
and both for the same reason: the answer is a reference to a row, so only
the thing that writes the row may write the reference.

**A file is attached, not typed.** The patient uploads through the document
surface they already have, and then tells the form which question the file
answers. This service is what checks the three things that matter before it
agrees — the question asks for a file, the file is one this patient sent,
and the side of the card is one the question asked for — and then writes
the item's answer FROM the rows rather than from anything the caller sent.
That inversion is the whole safety property: a document id in an answer got
there by being an artifact row first, and an artifact row got there by
being this patient's own upload.

**Insurance is typed once.** A card question may also ask for the plan
details written on the card. Those do not become the item's answer: they go
onto the client's coverage record, which is where the chart, a claim and an
eligibility check all already read them from. The alternative — a second
copy on the form — would be two records of one plan, disagreeing the first
time somebody corrects a digit.

**Nothing here blocks the patient on a payer.** When coverage lands and the
deployment can ask, an eligibility check is queued and the patient carries
on. The answer arrives on the coverage row minutes later, or never, and
either way the form was handed in.

**What was submitted stays submitted.** Every write here is refused once
the form is in. Removing a file before then is ordinary — somebody
photographs the back of a card badly and takes it again — and the document
goes with the artifact, because a file that was never sent is not a record
of anything.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from ..intake.answers import CARD_SIDES, UPLOAD_ITEM_TYPES
from ..intake.items import (
    DocumentRequestConfig,
    InsuranceCardConfig,
    ItemConfigError,
    stored_config,
    validate_item_config,
)
from ..models.patient_document import DocumentCategory
from ..repositories.patient_intake_assignment import WRITABLE_STATUSES
from ..utcnow import utc_now
from .coverage_intake import record_intake_coverage
from .patient_intake_assignment_service import AssignmentClosedError

if TYPE_CHECKING:
    from ..intake.items import ItemConfig
    from ..models.coverage import IntakeCoverage, PatientCoverage
    from ..repositories.coverage import PatientCoverageRepository, PayerRepository
    from ..repositories.intake_packet import IntakePacketRepository
    from ..repositories.patient_document import PatientDocumentRepository
    from ..repositories.patient_intake_artifact import PatientIntakeArtifactRepository
    from ..repositories.patient_intake_assignment import PatientIntakeAssignmentRepository

logger = logging.getLogger(__name__)


class NotAnUploadItemError(RuntimeError):
    """The question named is not one that asks for a file."""


class NotACardItemError(RuntimeError):
    """The question named is not one that asks for insurance details."""


class WrongSideError(RuntimeError):
    """A side of a card that this question did not ask for, or none at all."""


class DocumentNotUsableError(RuntimeError):
    """The document named is not one this patient uploaded for a form.

    Covers every way that can be true and deliberately does not say which:
    an id that never existed, another patient's, one still mid-upload, one
    filed in a different category. The route answers all of them the same.
    """


class IntakeArtifactService:
    """Files attached to a form, and the coverage a card question collects."""

    def __init__(
        self,
        artifacts: PatientIntakeArtifactRepository,
        assignments: PatientIntakeAssignmentRepository,
        packets: IntakePacketRepository,
        documents: PatientDocumentRepository,
        payers: PayerRepository,
        coverage: PatientCoverageRepository,
    ) -> None:
        self._artifacts = artifacts
        self._assignments = assignments
        self._packets = packets
        self._documents = documents
        self._payers = payers
        self._coverage = coverage

    # --- reads ---

    def list_for_patient(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        return self._artifacts.list_for_assignment(assignment_id, patient_id)

    def list_for_clinician(self, assignment_id: str, user_id: str) -> list[dict[str, object]]:
        return self._artifacts.list_for_clinician(assignment_id, user_id)

    # --- writes ---

    def attach(
        self,
        assignment: dict[str, object],
        patient_id: str,
        item_id: str,
        document_id: str,
        side: str | None,
    ) -> dict[str, object]:
        """Record that *document_id* answers *item_id* on this form.

        Raises :class:`AssignmentClosedError` when the form is no longer the
        patient's to fill in, ``LookupError`` when the item is not on its
        version, :class:`NotAnUploadItemError` when the question does not
        ask for a file, :class:`WrongSideError` when the side is not one
        this question asked for, and :class:`DocumentNotUsableError` when
        the document is not this patient's own intake upload.

        The item's answer is rewritten from the artifact rows afterwards, so
        what completion reads is always what is attached.
        """
        self._require_open(assignment)
        config = self._upload_item(assignment, item_id)
        self._check_side(config, side)
        self._require_own_artifact_document(document_id, patient_id)

        row = self._artifacts.add(
            {
                "id": str(uuid.uuid4()),
                "assignment_id": str(assignment["id"]),
                "patient_id": patient_id,
                "item_id": item_id,
                "document_id": document_id,
                "side": side,
                "created_at": utc_now(),
            }
        )
        self._rewrite_answer(assignment, patient_id, item_id)
        return row

    def remove(
        self, assignment: dict[str, object], patient_id: str, artifact_id: str
    ) -> dict[str, object]:
        """Take a file back off a form that has not been handed in.

        The artifact row goes and the document it pointed at is tombstoned
        in the same request: the file was attached to a form still being
        filled in, so it was never sent to the practice and there is nothing
        for the practice's record of what arrived to keep. That is the one
        delete on the patient's whole document surface, and this is the only
        thing that reaches it.

        Raises :class:`AssignmentClosedError` once the form is in, and
        ``LookupError`` when there is no such artifact of this patient's.
        """
        self._require_open(assignment)
        row = self._artifacts.get(artifact_id, patient_id)
        if row is None or str(row["assignment_id"]) != str(assignment["id"]):
            raise LookupError(artifact_id)

        if not self._artifacts.delete(artifact_id, patient_id):  # pragma: no cover — just read it
            raise LookupError(artifact_id)
        self._documents.soft_delete_for_patient_principal(
            str(row["document_id"]), patient_id, utc_now()
        )
        self._rewrite_answer(assignment, patient_id, str(row["item_id"]))
        return row

    def save_coverage(
        self,
        assignment: dict[str, object],
        patient_id: str,
        item_id: str,
        typed: IntakeCoverage,
    ) -> PatientCoverage:
        """Put the plan written on the card onto this client's record.

        The same two rows the booking form writes when somebody types their
        insurance before any chart exists, through the same service — so a
        plan typed at intake and a plan typed at booking are one shape, and
        the chart reads both from one place.

        Raises :class:`AssignmentClosedError` when the form is closed,
        ``LookupError`` when the item is not on its version, and
        :class:`NotACardItemError` when the question does not ask for these
        details.
        """
        self._require_open(assignment)
        config = self._upload_item(assignment, item_id)
        if not isinstance(config, InsuranceCardConfig) or not config.collect_fields:
            raise NotACardItemError(item_id)
        return record_intake_coverage(patient_id, typed, self._payers, self._coverage)

    # --- checks ---

    def _require_open(self, assignment: dict[str, object]) -> None:
        if str(assignment["status"]) not in WRITABLE_STATUSES:
            raise AssignmentClosedError(str(assignment["id"]))

    def _upload_item(self, assignment: dict[str, object], item_id: str) -> ItemConfig:
        """The parsed config of an item on this form that asks for a file."""
        row = next(
            (
                item
                for item in self._packets.list_items(str(assignment["version_id"]))
                if str(item["id"]) == item_id
            ),
            None,
        )
        if row is None:
            raise LookupError(item_id)
        if str(row["item_type"]) not in UPLOAD_ITEM_TYPES:
            raise NotAnUploadItemError(item_id)
        config = _parse(row)
        if config is None:
            # Stored settings that no longer parse. The question cannot be
            # answered as it stands, which is what completion already says
            # about it, so attaching to it would be answering something
            # nobody could have been asked.
            raise NotAnUploadItemError(item_id)
        return config

    def _check_side(self, config: ItemConfig, side: str | None) -> None:
        """A card wants a named side; anything else wants none.

        Both directions are refused rather than coerced. A side on a
        document request would be meaningless, and a card photograph with no
        side is one the form cannot tell from the other one.
        """
        if isinstance(config, InsuranceCardConfig):
            wanted = CARD_SIDES if config.sides == "both" else CARD_SIDES[:1]
            if side not in wanted:
                raise WrongSideError(str(side))
            return
        if side is not None:
            raise WrongSideError(side)

    def _require_own_artifact_document(self, document_id: str, patient_id: str) -> None:
        """The document has to be this patient's own, finished, intake upload.

        Four conditions and one answer, because a caller learning WHICH of
        them failed would learn something about a document that is not
        theirs. ``get_for_patient_principal`` already refuses another
        patient's row and any category this surface does not carry; what is
        added here is that the upload finished and that it was filed as an
        intake artifact rather than as a message attachment.
        """
        document = self._documents.get_for_patient_principal(document_id, patient_id)
        if (
            document is None
            or document.finalized_at is None
            or document.category is not DocumentCategory.INTAKE_ARTIFACT
            or document.uploaded_by_patient_id != patient_id
        ):
            raise DocumentNotUsableError(document_id)

    # --- the answer the rows add up to ---

    def _rewrite_answer(self, assignment: dict[str, object], patient_id: str, item_id: str) -> None:
        """Write the item's answer from its artifact rows.

        Computed rather than accumulated, so attaching and removing both end
        in a value that says exactly what is attached now. An item with
        nothing left on it keeps its row and holds an empty list, which
        every validator reads as unanswered — the truthful answer, and the
        one completion then reports as outstanding.

        Not written when the question's existing answer is already frozen:
        submitting is what freezes it, and this path is refused before then.
        """
        attached = [
            row
            for row in self._artifacts.list_for_assignment(str(assignment["id"]), patient_id)
            if str(row["item_id"]) == item_id
        ]
        value: dict[str, object] = {
            "documents": [
                (
                    {"document_id": str(row["document_id"]), "side": row["side"]}
                    if row["side"] is not None
                    else {"document_id": str(row["document_id"])}
                )
                for row in attached
            ]
        }
        now = utc_now()
        self._assignments.save_draft_response(
            {
                "id": str(uuid.uuid4()),
                "assignment_id": str(assignment["id"]),
                "patient_id": patient_id,
                "item_id": item_id,
                "value": value,
                "draft": True,
                "superseded_by": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        self._assignments.record_save(str(assignment["id"]), patient_id, now)


def _parse(row: dict[str, object]) -> ItemConfig | None:
    """One stored item's configuration, or ``None`` if it no longer parses."""
    try:
        return validate_item_config(str(row["item_type"]), stored_config(row["config"]))
    except ItemConfigError:
        return None


def blank_form_id_of(config: ItemConfig) -> str | None:
    """The practice's own form a document request offers, when it names one."""
    if isinstance(config, DocumentRequestConfig):
        return config.blank_form_id
    return None


__all__ = [
    "DocumentNotUsableError",
    "IntakeArtifactService",
    "NotACardItemError",
    "NotAnUploadItemError",
    "WrongSideError",
    "blank_form_id_of",
]
