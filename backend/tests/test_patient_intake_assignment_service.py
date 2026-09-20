# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The rules between an assignment and the answers saved against it.

The HTTP tests prove the door and the status codes. These prove the rules
underneath, where they can be stated once rather than through a request:
a form is only ever sent frozen, asking twice is asking once, a save is an
upsert, and completion is computed rather than remembered.

The two constants that describe the assignment lifecycle are pinned
against the migration's CHECK constraint and its partial index here,
because they are the sort of thing that drifts silently — a status added
to one and not the other fails at insert time in whatever runs next
rather than in the change that caused it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from app.intake.answers import AnswerError
from app.intake.items import ItemDraft
from app.repositories import (
    InMemoryIntakePacketRepository,
    InMemoryPatientIntakeAssignmentRepository,
)
from app.repositories.patient_intake_assignment import (
    ACTIVE_STATUSES,
    ASSIGNMENT_STATUSES,
    WRITABLE_STATUSES,
)
from app.services.intake_packet_service import IntakePacketService
from app.services.patient_intake_assignment_service import (
    AssignmentClosedError,
    IntakeAssignmentService,
    UnpublishedVersionError,
)

_PATIENT = "11111111-1111-4111-8111-111111111111"
_CLINICIAN = "clinician-1"
_PHQ9_COMPLETE = {str(i): 1 for i in range(1, 10)}

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "e4c92a1d70b6_patient_intake_assignments_responses.py"
)


@pytest.fixture(scope="module")
def migration_sql() -> str:
    """The revision that ships these tables, read as text.

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
def packet_service(packets: InMemoryIntakePacketRepository) -> IntakePacketService:
    return IntakePacketService(packets)


@pytest.fixture
def service(
    assignments: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
) -> IntakeAssignmentService:
    return IntakeAssignmentService(assignments, packets)


def _version(service: IntakePacketService, *, publish: bool = True) -> str:
    template = service.create_template("Intake", _CLINICIAN)
    version_id = str(service.list_versions(str(template["id"]))[0]["id"])
    service.replace_items(
        version_id,
        [
            ItemDraft(key="reason", item_type="reason"),
            ItemDraft(key="phq9", item_type="instrument", config={"code": "phq9"}),
            ItemDraft(key="note", item_type="free_text", required=False, config={"max_len": 50}),
        ],
    )
    if publish:
        service.publish(version_id, _CLINICIAN)
    return version_id


def _item(service: IntakeAssignmentService, version_id: str, key: str) -> str:
    return next(str(row["id"]) for row in service.items(version_id) if row["key"] == key)


class TestSendingAForm:
    def test_a_published_version_can_be_sent(
        self, service: IntakeAssignmentService, packet_service: IntakePacketService
    ) -> None:
        assignment, created = service.assign(_PATIENT, _version(packet_service), _CLINICIAN)
        assert created is True
        assert assignment["status"] == "assigned"
        assert assignment["assigned_by"] == _CLINICIAN

    def test_a_draft_version_cannot(
        self, service: IntakeAssignmentService, packet_service: IntakePacketService
    ) -> None:
        version_id = _version(packet_service, publish=False)
        with pytest.raises(UnpublishedVersionError):
            service.assign(_PATIENT, version_id, _CLINICIAN)

    def test_an_unknown_version_raises(self, service: IntakeAssignmentService) -> None:
        with pytest.raises(LookupError):
            service.assign(_PATIENT, "no-such-version", _CLINICIAN)

    def test_asking_twice_returns_the_one_already_live(
        self, service: IntakeAssignmentService, packet_service: IntakePacketService
    ) -> None:
        version_id = _version(packet_service)
        first, created_first = service.assign(_PATIENT, version_id, _CLINICIAN)
        second, created_second = service.assign(_PATIENT, version_id, _CLINICIAN)
        assert created_first is True
        assert created_second is False
        assert second["id"] == first["id"]

    def test_a_different_version_is_a_different_ask(
        self, service: IntakeAssignmentService, packet_service: IntakePacketService
    ) -> None:
        """The rule is one live assignment per VERSION, not per patient."""
        first, _ = service.assign(_PATIENT, _version(packet_service), _CLINICIAN)
        second, created = service.assign(_PATIENT, _version(packet_service), _CLINICIAN)
        assert created is True
        assert second["id"] != first["id"]

    def test_a_clinician_with_no_grant_cannot_send(
        self, packets: InMemoryIntakePacketRepository, packet_service: IntakePacketService
    ) -> None:
        repo = InMemoryPatientIntakeAssignmentRepository()
        repo.grant_access(_PATIENT, "the-treating-clinician")
        service = IntakeAssignmentService(repo, packets)
        with pytest.raises(LookupError):
            service.assign(_PATIENT, _version(packet_service), "a-stranger")


class TestWithdrawing:
    def test_it_flips_the_status_and_stamps_the_column(
        self, service: IntakeAssignmentService, packet_service: IntakePacketService
    ) -> None:
        assignment, _ = service.assign(_PATIENT, _version(packet_service), _CLINICIAN)
        withdrawn = service.withdraw(str(assignment["id"]), _CLINICIAN)
        assert withdrawn is not None
        assert withdrawn["status"] == "withdrawn"
        assert withdrawn["withdrawn_at"] is not None

    def test_an_unknown_assignment_is_none(self, service: IntakeAssignmentService) -> None:
        assert service.withdraw("no-such-assignment", _CLINICIAN) is None

    def test_a_withdrawn_form_refuses_a_later_save(
        self, service: IntakeAssignmentService, packet_service: IntakePacketService
    ) -> None:
        version_id = _version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        service.withdraw(str(assignment["id"]), _CLINICIAN)
        current = service.get_for_patient(str(assignment["id"]), _PATIENT)
        assert current is not None
        with pytest.raises(AssignmentClosedError):
            service.save_answer(current, _PATIENT, _item(service, version_id, "reason"), {})


class TestSavingAnswers:
    def test_the_first_save_moves_the_form_to_in_progress(
        self, service: IntakeAssignmentService, packet_service: IntakePacketService
    ) -> None:
        version_id = _version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        service.save_answer(
            assignment, _PATIENT, _item(service, version_id, "reason"), {"text": "Panic."}
        )
        current = service.get_for_patient(str(assignment["id"]), _PATIENT)
        assert current is not None
        assert current["status"] == "in_progress"

    def test_a_second_save_of_the_same_question_updates_one_row(
        self,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        packet_service: IntakePacketService,
    ) -> None:
        version_id = _version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        item_id = _item(service, version_id, "reason")
        service.save_answer(assignment, _PATIENT, item_id, {"text": "First."})
        service.save_answer(assignment, _PATIENT, item_id, {"text": "Second."})

        live = assignments.list_draft_responses(str(assignment["id"]), _PATIENT)
        assert len(live) == 1
        assert live[0]["value"] == {"text": "Second."}

    def test_a_value_the_question_refuses_is_not_stored(
        self,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        packet_service: IntakePacketService,
    ) -> None:
        version_id = _version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        with pytest.raises(AnswerError):
            service.save_answer(
                assignment, _PATIENT, _item(service, version_id, "phq9"), {"item_scores": {"1": 1}}
            )
        assert assignments.list_draft_responses(str(assignment["id"]), _PATIENT) == []

    def test_an_item_from_another_version_is_not_on_this_form(
        self, service: IntakeAssignmentService, packet_service: IntakePacketService
    ) -> None:
        assignment, _ = service.assign(_PATIENT, _version(packet_service), _CLINICIAN)
        elsewhere = _item(service, _version(packet_service), "reason")
        with pytest.raises(LookupError):
            service.save_answer(assignment, _PATIENT, elsewhere, {"text": "wrong form"})


class TestProgress:
    def test_an_untouched_form_is_missing_its_required_questions(
        self, service: IntakeAssignmentService, packet_service: IntakePacketService
    ) -> None:
        """The optional question is absent from ``missing``, as it should be."""
        version_id = _version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        result = service.progress(assignment, _PATIENT)
        assert result.complete is False
        assert result.missing == [
            _item(service, version_id, "reason"),
            _item(service, version_id, "phq9"),
        ]

    def test_answering_the_required_questions_completes_it(
        self, service: IntakeAssignmentService, packet_service: IntakePacketService
    ) -> None:
        version_id = _version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        service.save_answer(
            assignment, _PATIENT, _item(service, version_id, "reason"), {"text": "Panic."}
        )
        service.save_answer(
            assignment,
            _PATIENT,
            _item(service, version_id, "phq9"),
            {"item_scores": dict(_PHQ9_COMPLETE)},
        )
        result = service.progress(assignment, _PATIENT)
        assert result.complete is True
        assert result.missing == []

    def test_it_is_computed_rather_than_remembered(
        self,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        packet_service: IntakePacketService,
    ) -> None:
        """Take the answers away and the same assignment reports incomplete."""
        version_id = _version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        for key, value in (
            ("reason", {"text": "Panic."}),
            ("phq9", {"item_scores": dict(_PHQ9_COMPLETE)}),
        ):
            service.save_answer(assignment, _PATIENT, _item(service, version_id, key), value)
        assert service.progress(assignment, _PATIENT).complete is True

        assignments.responses.clear()
        assert service.progress(assignment, _PATIENT).complete is False


class TestTheStatusVocabularyMatchesTheMigration:
    """The Python constants and the DDL have to agree, so they are compared.

    A status added to one and not the other surfaces as a CHECK violation
    in whatever inserts next, which is a long way from the change that
    caused it.
    """

    def test_every_status_is_admitted_by_the_check_constraint(self, migration_sql: str) -> None:
        for status in ASSIGNMENT_STATUSES:
            assert f"'{status}'" in migration_sql, status

    def test_the_active_set_is_the_complement_of_the_partial_index(self) -> None:
        """One live form per patient per version is what ``ACTIVE_STATUSES`` means."""
        assert set(ASSIGNMENT_STATUSES) - set(ACTIVE_STATUSES) == {"accepted", "withdrawn"}

    def test_the_partial_index_excludes_exactly_those_two(self, migration_sql: str) -> None:
        predicate = "WHERE status NOT IN ('accepted','withdrawn')"
        assert predicate in migration_sql

    def test_a_patient_may_only_write_a_form_that_is_still_theirs(self) -> None:
        assert set(ACTIVE_STATUSES) >= WRITABLE_STATUSES
        assert "submitted" not in WRITABLE_STATUSES


class TestTheSeamCompletionUses:
    """``progress`` goes through the visibility seam, stub and all."""

    def test_every_question_is_asked_today(
        self, service: IntakeAssignmentService, packet_service: IntakePacketService
    ) -> None:
        """Rules are stored and validated; none is evaluated yet.

        The form built here carries no rule, so this is a statement about
        the default rather than about evaluation — which is exactly what
        v1 promises. ``test_intake_completion.py`` pins the stub itself.
        """
        version_id = _version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        required: list[Any] = [row for row in service.items(version_id) if row["required"]]
        assert len(service.progress(assignment, _PATIENT).missing) == len(required)
