# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The review cycle at the service layer, and the vocabulary it shares.

Two things are proved here that the HTTP tests beside them cannot.

**The rules, without a front door.** What a correction does to a form, what
a clinician entry writes beside what was already there, and which order the
review log ends up in — read off the rows rather than off a response body.

**The constants and the DDL agree.** Four literals say what a review event
may be: the CHECK constraint in the revision, ``REVIEW_EVENT_KINDS``, the
one kind a patient's row policy admits, and the policy predicate that
admits it. A kind added to one and not the others surfaces as a CHECK
violation or a silent refusal in whatever writes next, which is a long way
from the change that caused it — so they are compared directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.db import PATIENT_WRITE_NARROWING
from app.intake.items import ItemDraft
from app.repositories import (
    InMemoryIntakePacketRepository,
    InMemoryPatientIntakeAssignmentRepository,
)
from app.repositories.patient_intake_assignment import (
    ACTIVE_STATUSES,
    PATIENT_REVIEW_EVENT_KIND,
    RESPONSE_PROVENANCE,
    REVIEW_EVENT_KINDS,
)
from app.services.intake_packet_service import IntakePacketService
from app.services.patient_intake_assignment_service import IntakeAssignmentService
from app.services.patient_intake_review_service import (
    AlreadyAcceptedError,
    IntakeReviewService,
    ReviewStateError,
    UnknownItemError,
)

_PATIENT = "11111111-1111-4111-8111-111111111111"
_CLINICIAN = "clinician-1"

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "c5e1a9f0d248_patient_intake_review_events.py"
)


@pytest.fixture(scope="module")
def migration_sql() -> str:
    """The revision that ships this table, read as text.

    Compared against rather than parsed: what is being pinned is that two
    literals say the same thing, and a SQL parser would not make that
    clearer.
    """
    return _MIGRATION.read_text()


@pytest.fixture
def packets() -> InMemoryIntakePacketRepository:
    return InMemoryIntakePacketRepository()


@pytest.fixture
def assignments() -> InMemoryPatientIntakeAssignmentRepository:
    repo = InMemoryPatientIntakeAssignmentRepository()
    repo.grant_all_access()
    return repo


@pytest.fixture
def service(
    assignments: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
) -> IntakeAssignmentService:
    return IntakeAssignmentService(assignments, packets)


@pytest.fixture
def reviews(
    assignments: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
) -> IntakeReviewService:
    return IntakeReviewService(assignments, packets)


@pytest.fixture
def version_id(packets: InMemoryIntakePacketRepository) -> str:
    packet_service = IntakePacketService(packets)
    template = packet_service.create_template("Intake", _CLINICIAN)
    version = str(packet_service.list_versions(str(template["id"]))[0]["id"])
    packet_service.replace_items(
        version,
        [
            ItemDraft(key="reason", item_type="reason", config={}),
            ItemDraft(
                key="goals",
                item_type="free_text",
                config={"max_len": 500},
                label="What would you like to be different?",
            ),
        ],
    )
    packet_service.publish(version, _CLINICIAN)
    return version


def _item(service: IntakeAssignmentService, version: str, key: str) -> str:
    return next(str(row["id"]) for row in service.items(version) if row["key"] == key)


def _handed_in(
    service: IntakeAssignmentService,
    assignments: InMemoryPatientIntakeAssignmentRepository,
    version: str,
) -> dict[str, object]:
    """An assignment with both questions answered and frozen."""
    assignment, _ = service.assign(_PATIENT, version, _CLINICIAN)
    for key, text in (("reason", "Panic before every shift."), ("goals", "Sleep.")):
        service.save_answer(
            dict(assignments.assignments[str(assignment["id"])]),
            _PATIENT,
            _item(service, version, key),
            {"text": text},
        )
    row = assignments.assignments[str(assignment["id"])]
    service._repo.freeze_draft_responses(str(row["id"]), _PATIENT, row["updated_at"])  # type: ignore[arg-type]
    row["status"] = "submitted"
    return dict(row)


class TestRequestingCorrections:
    def test_it_reopens_the_form_and_logs_what_was_asked(
        self,
        service: IntakeAssignmentService,
        reviews: IntakeReviewService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        version_id: str,
    ) -> None:
        assignment = _handed_in(service, assignments, version_id)
        reason = _item(service, version_id, "reason")

        moved, event = reviews.request_correction(
            assignment, _CLINICIAN, item_ids=[reason], note="Have another look?"
        )
        assert moved["status"] == "needs_correction"
        assert event["kind"] == "correction_requested"
        assert event["item_ids"] == [reason]
        assert event["note_to_patient"] == "Have another look?"

    def test_the_named_questions_come_back_in_form_order(
        self,
        service: IntakeAssignmentService,
        reviews: IntakeReviewService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        version_id: str,
    ) -> None:
        """Whatever order the screen collected them in, first means first."""
        assignment = _handed_in(service, assignments, version_id)
        reason = _item(service, version_id, "reason")
        goals = _item(service, version_id, "goals")

        _moved, event = reviews.request_correction(
            assignment, _CLINICIAN, item_ids=[goals, reason], note="Both, please."
        )
        assert event["item_ids"] == [reason, goals]

    def test_a_question_that_is_not_on_the_form_is_refused(
        self,
        service: IntakeAssignmentService,
        reviews: IntakeReviewService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        version_id: str,
    ) -> None:
        assignment = _handed_in(service, assignments, version_id)
        with pytest.raises(UnknownItemError):
            reviews.request_correction(
                assignment, _CLINICIAN, item_ids=["not-an-item"], note="Nowhere to look."
            )

    def test_a_form_nobody_handed_in_is_refused(
        self, service: IntakeAssignmentService, reviews: IntakeReviewService, version_id: str
    ) -> None:
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        with pytest.raises(ReviewStateError):
            reviews.request_correction(
                dict(assignment),
                _CLINICIAN,
                item_ids=[_item(service, version_id, "reason")],
                note="Too early.",
            )


class TestEnteringForThePatient:
    def test_it_supersedes_what_was_there_without_editing_it(
        self,
        service: IntakeAssignmentService,
        reviews: IntakeReviewService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        version_id: str,
    ) -> None:
        assignment = _handed_in(service, assignments, version_id)
        goals = _item(service, version_id, "goals")
        original = next(
            dict(row) for row in assignments.responses.values() if str(row["item_id"]) == goals
        )

        stored, event = reviews.enter_for_patient(
            assignment, _CLINICIAN, item_id=goals, value={"text": "Sleeping through."}
        )
        assert stored["provenance"] == "clinician"
        assert stored["draft"] is False
        assert event["kind"] == "clinician_entered"

        frozen = assignments.responses[str(original["id"])]
        assert frozen["value"] == original["value"]
        assert frozen["superseded_by"] == stored["id"]

    def test_an_accepted_form_is_refused(
        self,
        service: IntakeAssignmentService,
        reviews: IntakeReviewService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        version_id: str,
    ) -> None:
        assignment = _handed_in(service, assignments, version_id)
        reviews.accept(assignment, _CLINICIAN)
        with pytest.raises(AlreadyAcceptedError):
            reviews.enter_for_patient(
                dict(assignments.assignments[str(assignment["id"])]),
                _CLINICIAN,
                item_id=_item(service, version_id, "goals"),
                value={"text": "Too late."},
            )

    def test_a_withdrawn_form_is_refused(
        self,
        service: IntakeAssignmentService,
        reviews: IntakeReviewService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        version_id: str,
    ) -> None:
        """The practice stopped asking, so there is no request to answer."""
        assignment = _handed_in(service, assignments, version_id)
        service.withdraw(str(assignment["id"]), _CLINICIAN)
        with pytest.raises(AlreadyAcceptedError):
            reviews.enter_for_patient(
                dict(assignments.assignments[str(assignment["id"])]),
                _CLINICIAN,
                item_id=_item(service, version_id, "goals"),
                value={"text": "Nobody is asking."},
            )


class TestTheReviewLog:
    def test_it_reads_in_the_order_things_happened(
        self,
        service: IntakeAssignmentService,
        reviews: IntakeReviewService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        version_id: str,
    ) -> None:
        assignment = _handed_in(service, assignments, version_id)
        reviews.enter_for_patient(
            assignment,
            _CLINICIAN,
            item_id=_item(service, version_id, "goals"),
            value={"text": "Sleeping through."},
        )
        reviews.accept(dict(assignments.assignments[str(assignment["id"])]), _CLINICIAN)

        kinds = [row["kind"] for row in reviews.events(str(assignment["id"]), _CLINICIAN)]
        assert kinds == ["clinician_entered", "accepted"]

    def test_replaced_answers_are_counted_per_question(
        self,
        service: IntakeAssignmentService,
        reviews: IntakeReviewService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        version_id: str,
    ) -> None:
        assignment = _handed_in(service, assignments, version_id)
        goals = _item(service, version_id, "goals")
        reviews.enter_for_patient(
            assignment, _CLINICIAN, item_id=goals, value={"text": "Sleeping through."}
        )
        counts = reviews.superseded_counts(str(assignment["id"]), _CLINICIAN)
        assert counts == {goals: 1}

    def test_a_clinician_with_no_grant_reads_nothing(
        self,
        service: IntakeAssignmentService,
        packets: InMemoryIntakePacketRepository,
        version_id: str,
    ) -> None:
        """An empty log and a log nobody may read look the same on purpose."""
        ungranted = InMemoryPatientIntakeAssignmentRepository()
        ungranted.grant_access(_PATIENT, _CLINICIAN)
        owner_service = IntakeAssignmentService(ungranted, packets)
        owner_reviews = IntakeReviewService(ungranted, packets)
        assignment = _handed_in(owner_service, ungranted, version_id)
        owner_reviews.accept(assignment, _CLINICIAN)

        stranger = IntakeReviewService(ungranted, packets)
        assert stranger.events(str(assignment["id"]), "somebody-else") == []
        assert stranger.superseded_counts(str(assignment["id"]), "somebody-else") == {}


class TestTheVocabularyMatchesTheMigration:
    """The Python constants and the DDL have to agree, so they are compared."""

    def test_every_kind_is_admitted_by_the_check_constraint(self, migration_sql: str) -> None:
        for kind in REVIEW_EVENT_KINDS:
            assert f"'{kind}'" in migration_sql, kind

    def test_every_provenance_is_admitted_by_the_check_constraint(self, migration_sql: str) -> None:
        for provenance in RESPONSE_PROVENANCE:
            assert f"'{provenance}'" in migration_sql, provenance

    def test_the_patients_one_kind_is_the_one_their_policy_admits(self) -> None:
        """``PATIENT_WRITE_NARROWING`` is what stops a patient minting an acceptance."""
        narrowing = PATIENT_WRITE_NARROWING["patient_intake_review_events"]
        assert narrowing == f"kind = '{PATIENT_REVIEW_EVENT_KIND}'"
        assert PATIENT_REVIEW_EVENT_KIND in REVIEW_EVENT_KINDS

    def test_entering_a_value_is_allowed_exactly_while_the_form_is_live(self) -> None:
        assert set(ACTIVE_STATUSES) == {
            "assigned",
            "in_progress",
            "submitted",
            "needs_correction",
        }
