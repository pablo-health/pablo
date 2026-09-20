# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading one form out of the rows, ready to be printed.

:mod:`app.intake.export` decides what a document looks like and knows
nothing about storage. This is the half that knows where everything lives:
the questions on the version, every answer ever written against them, the
signatures, and the log of what was asked for and done.

Two things are worth stating, because both are decisions rather than
mechanics.

**The whole history, not a count.** The review screen is told how many
answers each question replaced and never the answers themselves, because a
screen offering to show them would be a second disclosure surface. A
document that leaves the product is the opposite case: it is filed,
attached to a referral, or handed over under a release, and "this answer was
corrected afterwards" is exactly what the reader of a chart copy needs. So
this reads them, and the document prints them under the answer that
replaced them.

**A question nobody was shown is not a question nobody answered.** Which
questions this patient was shown is computed by the same completion walk the
portal and the submit path use — never decided here, and never inferred from
an answer being absent.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..intake.consent_statement import consent_statement
from ..intake.documents import render_html
from ..intake.export import (
    ExportAnswer,
    ExportEvent,
    ExportItem,
    ExportSignature,
    IntakeExport,
    answer_lines,
    item_heading,
)
from ..intake.items import InstructionsConfig

if TYPE_CHECKING:
    from ..intake.export import AnswerState
    from ..intake.items import ItemConfig
    from ..repositories.intake_document import IntakeDocumentRepository
    from ..repositories.patient_intake_signature import PatientIntakeSignatureRepository
    from .patient_intake_assignment_service import IntakeAssignmentService
    from .patient_intake_review_service import IntakeReviewService


class IntakeExportService:
    """One assignment, read out of every table that holds part of it."""

    def __init__(
        self,
        assignments: IntakeAssignmentService,
        reviews: IntakeReviewService,
        signatures: PatientIntakeSignatureRepository,
        documents: IntakeDocumentRepository,
    ) -> None:
        self._assignments = assignments
        self._reviews = reviews
        self._signatures = signatures
        self._documents = documents

    def build(
        self,
        assignment: dict[str, object],
        user_id: str,
        *,
        patient_name: str,
        patient_date_of_birth: str | None,
        packet_name: str,
        version: int,
        practice_name: str | None,
    ) -> IntakeExport:
        """Everything the document prints, for one form on one chart.

        The caller has already checked that this assignment is on this
        patient's chart and that the clinician holds a grant on it; every
        read below carries the grant as well, so a caller that had not
        would get an empty document rather than somebody else's.
        """
        assignment_id = str(assignment["id"])
        patient_id = str(assignment["patient_id"])
        hidden = set(self._assignments.progress(assignment, patient_id).hidden)
        history = self._responses_by_item(assignment_id, user_id)

        return IntakeExport(
            practice_name=practice_name,
            patient_name=patient_name,
            patient_date_of_birth=patient_date_of_birth,
            packet_name=packet_name,
            version=version,
            status=str(assignment["status"]),
            receipt_code=_optional_str(assignment.get("receipt_code")),
            assigned_at=_moment(assignment.get("assigned_at")),
            submitted_at=_moment(assignment.get("submitted_at")),
            accepted_at=_moment(assignment.get("accepted_at")),
            withdrawn_at=_moment(assignment.get("withdrawn_at")),
            items=[
                self._item(row, history.get(str(row["id"]), []), shown=str(row["id"]) not in hidden)
                for row in self._assignments.items(str(assignment["version_id"]))
            ],
            signatures=[
                self._signature(row)
                for row in self._signatures.list_live_for_assignment(assignment_id, patient_id)
            ],
            events=[
                ExportEvent(
                    kind=str(row["kind"]),
                    created_at=row["created_at"],  # type: ignore[arg-type]
                    note_to_patient=_optional_str(row.get("note_to_patient")),
                )
                for row in self._reviews.events(assignment_id, user_id)
            ],
        )

    # --- one question ---

    def _item(
        self, row: dict[str, object], answers: list[dict[str, object]], *, shown: bool
    ) -> ExportItem:
        """One question with its current answer and everything it replaced."""
        config = self._assignments.config_for(row)
        item_type = str(row["item_type"])
        key = str(row["key"])
        current_row = _current(answers)
        return ExportItem(
            key=key,
            item_type=item_type,
            heading=item_heading(config, _optional_str(row.get("label")), key, item_type),
            help_text=_optional_str(row.get("help_text")),
            body_html=_body_html(config),
            shown=shown,
            required=bool(row["required"]),
            current=None if current_row is None else _answer(current_row, config),
            history=[_answer(earlier, config) for earlier in answers if earlier is not current_row],
        )

    def _signature(self, row: dict[str, object]) -> ExportSignature:
        """One signature, with the document it names resolved to a title.

        The digest on the row is what names the words that were read, and
        it is printed whether or not the version still resolves: a document
        can be archived, and a signature still says what it was taken
        against.
        """
        version_id = str(row["document_version_id"])
        document = self._documents.get(version_id)
        statement_version = str(row["consent_statement_version"])
        return ExportSignature(
            document_title=None if document is None else str(document["title"]),
            document_version=None if document is None else int(document["version"]),  # type: ignore[call-overload]
            document_version_id=version_id,
            document_digest=str(row["document_digest"]),
            signer_role=str(row["signer_role"]),
            signer_typed_name=str(row["signer_typed_name"]),
            consent_statement=consent_statement(statement_version),
            consent_statement_version=statement_version,
            signed_at=row["signed_at"],  # type: ignore[arg-type]
            auth_strength=str(row["auth_strength"]),
            session_id=_optional_str(row.get("session_id")),
            ip=_optional_str(row.get("ip")),
            user_agent=_optional_str(row.get("user_agent")),
            evidence_digest=str(row["evidence_digest"]),
        )

    def _responses_by_item(
        self, assignment_id: str, user_id: str
    ) -> dict[str, list[dict[str, object]]]:
        """Every answer on the form, grouped by question, oldest first."""
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in self._assignments.all_responses_for_clinician(assignment_id, user_id):
            grouped.setdefault(str(row["item_id"]), []).append(row)
        return grouped


def _current(answers: list[dict[str, object]]) -> dict[str, object] | None:
    """The answer that stands, chosen the way a read of one question is.

    A draft first when there is one, then the most recently written — the
    same order :meth:`get_live_response` uses, so the document and the
    screen never disagree about which answer is the current one.
    """
    live = [row for row in answers if row["superseded_by"] is None]
    if not live:
        return None
    return max(live, key=lambda row: (bool(row["draft"]), row["updated_at"]))  # type: ignore[arg-type,return-value]


def _answer(row: dict[str, object], config: ItemConfig | None) -> ExportAnswer:
    return ExportAnswer(
        lines=answer_lines(config, _mapping(row.get("value"))),
        state=_state(row),
        provenance=_optional_str(row.get("provenance")),
        written_at=_moment(row.get("updated_at")),
    )


def _state(row: dict[str, object]) -> AnswerState:
    """What became of one answer.

    A row pointing at itself is the retirement the submit path writes for a
    question the form stopped asking — the answer was never part of what
    was handed in, which is a different fact from having been replaced.
    """
    superseded_by = row["superseded_by"]
    if superseded_by is None:
        return "current"
    return "withheld" if str(superseded_by) == str(row["id"]) else "replaced"


def _body_html(config: ItemConfig | None) -> str | None:
    """Prose the practice wrote, as safe HTML, for the items that are prose.

    The engine's own markdown reader, which escapes before it marks up, so
    the only tags in the result are ones it wrote.
    """
    return render_html(config.body_markdown) if isinstance(config, InstructionsConfig) else None


def _mapping(value: object) -> dict[str, object] | None:
    return dict(value) if isinstance(value, dict) else None


def _moment(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


__all__ = ["IntakeExportService"]
