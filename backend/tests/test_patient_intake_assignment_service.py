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
from app.intake.receipts import RECEIPT_ALPHABET, RECEIPT_LENGTH
from app.outcome_measures.service import OutcomeMeasureService
from app.repositories import (
    InMemoryIntakePacketRepository,
    InMemoryPatientIntakeAssignmentRepository,
)
from app.repositories.outcome_measure import InMemoryOutcomeMeasureRepository
from app.repositories.patient_intake_assignment import (
    ACTIVE_STATUSES,
    ASSIGNMENT_STATUSES,
    WRITABLE_STATUSES,
)
from app.services.intake_packet_service import IntakePacketService
from app.services.patient_intake_assignment_service import (
    AssignmentClosedError,
    FrozenResponseError,
    IncompleteFormError,
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


@pytest.fixture
def measure_repo() -> InMemoryOutcomeMeasureRepository:
    repo = InMemoryOutcomeMeasureRepository()
    repo.grant_all_access()
    return repo


@pytest.fixture
def measures(measure_repo: InMemoryOutcomeMeasureRepository) -> OutcomeMeasureService:
    """The real scoring service, so the totals are the ones the chart shows."""
    return OutcomeMeasureService(measure_repo)


def _version(service: IntakePacketService, *, publish: bool = True) -> str:
    template = service.create_template("Intake", _CLINICIAN)
    version_id = str(service.list_versions(str(template["id"]))[0]["id"])
    service.replace_items(
        version_id,
        [
            ItemDraft(key="reason", item_type="reason"),
            ItemDraft(key="phq9", item_type="instrument", config={"code": "phq9"}),
            ItemDraft(
                key="note",
                item_type="free_text",
                required=False,
                label="Anything else you want us to know?",
                config={"max_len": 50},
            ),
        ],
    )
    if publish:
        service.publish(version_id, _CLINICIAN)
    return version_id


def _item(service: IntakeAssignmentService, version_id: str, key: str) -> str:
    return next(str(row["id"]) for row in service.items(version_id) if row["key"] == key)


def _answered(service: IntakeAssignmentService, version_id: str) -> dict[str, Any]:
    """An assignment with every required question answered, ready to hand in."""
    assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
    for key, value in (
        ("reason", {"text": "Panic before every shift."}),
        ("phq9", {"item_scores": dict(_PHQ9_COMPLETE)}),
    ):
        service.save_answer(assignment, _PATIENT, _item(service, version_id, key), value)
    current = service.get_for_patient(str(assignment["id"]), _PATIENT)
    assert current is not None
    return dict(current)


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


class TestHandingItIn:
    def test_an_unfinished_form_is_refused_and_nothing_changes(
        self,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        measures: OutcomeMeasureService,
        packet_service: IntakePacketService,
    ) -> None:
        version_id = _version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        service.save_answer(
            assignment, _PATIENT, _item(service, version_id, "reason"), {"text": "Panic."}
        )

        with pytest.raises(IncompleteFormError) as exc:
            service.submit(assignment, _PATIENT, measures)

        assert exc.value.missing == [_item(service, version_id, "phq9")]
        current = service.get_for_patient(str(assignment["id"]), _PATIENT)
        assert current is not None
        assert current["status"] == "in_progress"
        assert current["receipt_code"] is None
        assert len(assignments.list_draft_responses(str(assignment["id"]), _PATIENT)) == 1

    def test_a_finished_form_freezes_scores_and_gets_a_receipt(
        self,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        measures: OutcomeMeasureService,
        measure_repo: InMemoryOutcomeMeasureRepository,
        packet_service: IntakePacketService,
    ) -> None:
        version_id = _version(packet_service)
        assignment = _answered(service, version_id)

        submission = service.submit(assignment, _PATIENT, measures)
        submitted, recorded = submission.assignment, submission.measures

        assert submitted["status"] == "submitted"
        assert submission.notes == []
        assert submitted["submitted_at"] is not None
        assert set(str(submitted["receipt_code"])) <= set(RECEIPT_ALPHABET)
        assert len(str(submitted["receipt_code"])) == RECEIPT_LENGTH

        # Every draft is now an answer, so the narrower read is empty and
        # the wider one still has both.
        assert assignments.list_draft_responses(str(assignment["id"]), _PATIENT) == []
        assert len(assignments.list_live_responses(str(assignment["id"]), _PATIENT)) == 2

        assert [m.instrument for m in recorded] == ["phq9"]
        assert recorded[0].total_score == sum(_PHQ9_COMPLETE.values())
        assert recorded[0].source == "patient_self_report"
        assert recorded[0].created_by == _PATIENT
        assert len(measure_repo.list_by_patient(_PATIENT, _CLINICIAN)) == 1

    def test_a_submitted_form_still_reads_as_complete(
        self,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        packet_service: IntakePacketService,
    ) -> None:
        """Freezing an answer must not make it stop counting as one."""
        version_id = _version(packet_service)
        assignment = _answered(service, version_id)
        submitted = service.submit(assignment, _PATIENT, measures).assignment
        assert service.progress(submitted, _PATIENT).complete is True

    def test_handing_it_in_twice_is_refused(
        self,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        measure_repo: InMemoryOutcomeMeasureRepository,
        packet_service: IntakePacketService,
    ) -> None:
        """The second call must not mint a second receipt or a second score."""
        version_id = _version(packet_service)
        assignment = _answered(service, version_id)
        submitted = service.submit(assignment, _PATIENT, measures).assignment

        with pytest.raises(AssignmentClosedError):
            service.submit(submitted, _PATIENT, measures)

        current = service.get_for_patient(str(assignment["id"]), _PATIENT)
        assert current is not None
        assert current["receipt_code"] == submitted["receipt_code"]
        assert len(measure_repo.list_by_patient(_PATIENT, _CLINICIAN)) == 1

    def test_two_submissions_do_not_share_a_receipt(
        self,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        packet_service: IntakePacketService,
    ) -> None:
        receipts = set()
        for _ in range(2):
            version_id = _version(packet_service)
            assignment = _answered(service, version_id)
            submitted = service.submit(assignment, _PATIENT, measures).assignment
            receipts.add(str(submitted["receipt_code"]))
        assert len(receipts) == 2

    def test_a_taken_receipt_is_tried_again_rather_than_raised(
        self,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        packet_service: IntakePacketService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only the unique index can arbitrate, so the service retries.

        The first draw is forced to collide; the second is a real one. A
        submit that gave up on the first refusal would leave a patient
        unable to hand a form in for a reason that is nobody's fault.
        """
        import app.services.patient_intake_assignment_service as module  # noqa: PLC0415

        drawn = iter(["TAKEN234", "TAKEN234", "FREE2345"])
        monkeypatch.setattr(module, "new_receipt_code", lambda: next(drawn))

        first = _answered(service, _version(packet_service))
        service.submit(first, _PATIENT, measures)

        second = _answered(service, _version(packet_service))
        submitted = service.submit(second, _PATIENT, measures).assignment
        assert submitted["receipt_code"] == "FREE2345"


def _branching_version(service: IntakePacketService) -> str:
    """A form whose second question is asked only of somebody who said yes."""
    template = service.create_template("Substance use", _CLINICIAN)
    version_id = str(service.list_versions(str(template["id"]))[0]["id"])
    service.replace_items(
        version_id,
        [
            ItemDraft(
                key="substances",
                item_type="yes_no",
                label="Do you drink alcohol or use any other substances?",
            ),
            ItemDraft(
                key="which",
                item_type="free_text",
                label="What, and roughly how often?",
                config={
                    "max_len": 500,
                    "visible_when": {"item_key": "substances", "op": "eq", "value": True},
                },
            ),
        ],
    )
    service.publish(version_id, _CLINICIAN)
    return version_id


class TestAnAnswerToAQuestionThatStoppedApplying:
    """Said yes, answered what opened, went back and said no.

    The follow-up is not asked any more, so what was typed into it is not
    part of what the practice receives — and the patient is told on the way
    out rather than finding it missing from the chart later.
    """

    def test_it_is_not_handed_in_and_the_receipt_says_so(
        self,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        measures: OutcomeMeasureService,
        packet_service: IntakePacketService,
    ) -> None:
        version_id = _branching_version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        yes_no = _item(service, version_id, "substances")
        follow_up = _item(service, version_id, "which")

        service.save_answer(assignment, _PATIENT, yes_no, {"yes": True})
        service.save_answer(assignment, _PATIENT, follow_up, {"text": "Wine, most nights."})
        service.save_answer(assignment, _PATIENT, yes_no, {"yes": False})

        submission = service.submit(assignment, _PATIENT, measures)

        assert submission.assignment["status"] == "submitted"
        assert submission.notes == [
            "One question stopped applying as you answered, so your answer to it wasn't sent."
        ]
        handed_in = assignments.list_live_responses(str(assignment["id"]), _PATIENT)
        assert [str(row["item_id"]) for row in handed_in] == [yes_no]

    def test_the_row_is_retired_rather_than_deleted(
        self,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        measures: OutcomeMeasureService,
        packet_service: IntakePacketService,
    ) -> None:
        """An answer somebody gave is never destroyed; it stops counting."""
        version_id = _branching_version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        service.save_answer(
            assignment, _PATIENT, _item(service, version_id, "substances"), {"yes": True}
        )
        service.save_answer(
            assignment,
            _PATIENT,
            _item(service, version_id, "which"),
            {"text": "Wine, most nights."},
        )
        service.save_answer(
            assignment, _PATIENT, _item(service, version_id, "substances"), {"yes": False}
        )
        service.submit(assignment, _PATIENT, measures)

        retired = [
            row for row in assignments.responses.values() if row["superseded_by"] is not None
        ]
        assert len(retired) == 1
        assert retired[0]["value"] == {"text": "Wine, most nights."}
        assert retired[0]["draft"] is True

    def test_an_answer_the_form_still_asks_for_is_handed_in_as_usual(
        self,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        measures: OutcomeMeasureService,
        packet_service: IntakePacketService,
    ) -> None:
        """The control. Without it the retirement above could be blanket."""
        version_id = _branching_version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        service.save_answer(
            assignment, _PATIENT, _item(service, version_id, "substances"), {"yes": True}
        )
        service.save_answer(
            assignment,
            _PATIENT,
            _item(service, version_id, "which"),
            {"text": "Wine, most nights."},
        )

        submission = service.submit(assignment, _PATIENT, measures)

        assert submission.notes == []
        handed_in = assignments.list_live_responses(str(assignment["id"]), _PATIENT)
        assert len(handed_in) == 2
        assert all(row["draft"] is False for row in handed_in)

    def test_a_hidden_question_does_not_hold_the_form_up(
        self,
        service: IntakeAssignmentService,
        measures: OutcomeMeasureService,
        packet_service: IntakePacketService,
    ) -> None:
        """Required, and never asked, so submitting does not need it."""
        version_id = _branching_version(packet_service)
        assignment, _ = service.assign(_PATIENT, version_id, _CLINICIAN)
        service.save_answer(
            assignment, _PATIENT, _item(service, version_id, "substances"), {"yes": False}
        )

        submission = service.submit(assignment, _PATIENT, measures)
        assert submission.assignment["status"] == "submitted"
        assert submission.notes == []


class TestWhatWasHandedInIsNotEdited:
    """The immutability invariant, stated where no route can skip it."""

    def test_the_service_refuses_to_change_a_frozen_answer(
        self,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        measures: OutcomeMeasureService,
        packet_service: IntakePacketService,
    ) -> None:
        """Even on a form that is open again, a frozen row is not edited.

        The assignment is put back into a writable state deliberately, so
        what this proves is about the ROW rather than about the form's
        status — which is the invariant a correction has to honour when it
        lands: a successor row, never an edit in place.
        """
        version_id = _version(packet_service)
        assignment = _answered(service, version_id)
        service.submit(assignment, _PATIENT, measures)

        reopened = assignments.assignments[str(assignment["id"])]
        reopened["status"] = "needs_correction"

        before = assignments.list_live_responses(str(assignment["id"]), _PATIENT)
        with pytest.raises(FrozenResponseError):
            service.save_answer(
                dict(reopened),
                _PATIENT,
                _item(service, version_id, "reason"),
                {"text": "Rewriting what I handed in."},
            )
        assert assignments.list_live_responses(str(assignment["id"]), _PATIENT) == before

    def test_an_unanswered_question_on_a_reopened_form_can_still_be_answered(
        self,
        service: IntakeAssignmentService,
        assignments: InMemoryPatientIntakeAssignmentRepository,
        measures: OutcomeMeasureService,
        packet_service: IntakePacketService,
    ) -> None:
        """The control. Without it the refusal above could be blanket."""
        version_id = _version(packet_service)
        assignment = _answered(service, version_id)
        service.submit(assignment, _PATIENT, measures)

        reopened = assignments.assignments[str(assignment["id"])]
        reopened["status"] = "needs_correction"

        # ``note`` is optional and was never answered, so it has no frozen row.
        stored, _ = service.save_answer(
            dict(reopened), _PATIENT, _item(service, version_id, "note"), {"text": "One more."}
        )
        assert stored["draft"] is True


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
