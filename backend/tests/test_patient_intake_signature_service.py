# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The rules between a form, a consent document and a signature.

The HTTP tests prove the door and the status codes. These prove the rules
underneath, where each can be stated once rather than through a request:
what gets signed is the version the form pinned, a re-signature rule
refuses a stale one, one role signs once, and signing is what answers the
question so the form can be handed in.

The document and packet services here are the real ones, so a consent item
is pinned by the code that pins it in production rather than by a fixture
that agrees with this file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.intake.consent_statement import CURRENT_CONSENT_STATEMENT_VERSION
from app.intake.items import ItemDraft
from app.intake.signatures import evidence_digest
from app.repositories import (
    InMemoryIntakeDocumentRepository,
    InMemoryIntakePacketRepository,
    InMemoryPatientIntakeAssignmentRepository,
    InMemoryPatientIntakeSignatureRepository,
)
from app.services.intake_document_service import IntakeDocumentService
from app.services.intake_packet_service import IntakePacketService
from app.services.patient_intake_assignment_service import IntakeAssignmentService
from app.services.patient_intake_signature_service import (
    AlreadySignedError,
    AssignmentClosedError,
    IntakeSignatureService,
    NotAffirmedError,
    NotASignableItemError,
    SignerRoleNotAskedError,
    SigningRequest,
    StaleDocumentVersionError,
    TypedNameError,
    UnsignableDocumentError,
)

if TYPE_CHECKING:
    from app.repositories.intake_document import IntakeDocumentRepository

_PATIENT = "11111111-1111-4111-8111-111111111111"
_CLINICIAN = "clinician-1"
_CONSENT_BODY = "# Consent to treatment\n\nYou are agreeing to be treated here."


# ---------------------------------------------------------------------------
# Fixtures: the real services, wired the way the routes wire them
# ---------------------------------------------------------------------------


@pytest.fixture
def documents() -> InMemoryIntakeDocumentRepository:
    return InMemoryIntakeDocumentRepository()


@pytest.fixture
def document_service(documents: InMemoryIntakeDocumentRepository) -> IntakeDocumentService:
    return IntakeDocumentService(documents)


@pytest.fixture
def packets() -> InMemoryIntakePacketRepository:
    return InMemoryIntakePacketRepository()


@pytest.fixture
def packet_service(
    packets: InMemoryIntakePacketRepository, documents: IntakeDocumentRepository
) -> IntakePacketService:
    """The publisher, with the document lookup that pins a version."""

    def published(document_key: str) -> str | None:
        row = documents.published_for_key(document_key)
        return str(row["id"]) if row else None

    return IntakePacketService(packets, published_document=published)


@pytest.fixture
def assignments() -> InMemoryPatientIntakeAssignmentRepository:
    repo = InMemoryPatientIntakeAssignmentRepository()
    repo.grant_all_access()
    return repo


@pytest.fixture
def signature_repo() -> InMemoryPatientIntakeSignatureRepository:
    return InMemoryPatientIntakeSignatureRepository()


@pytest.fixture
def assignment_service(
    assignments: InMemoryPatientIntakeAssignmentRepository,
    packets: InMemoryIntakePacketRepository,
) -> IntakeAssignmentService:
    return IntakeAssignmentService(assignments, packets)


@pytest.fixture
def service(
    signature_repo: InMemoryPatientIntakeSignatureRepository,
    packets: InMemoryIntakePacketRepository,
    documents: InMemoryIntakeDocumentRepository,
    assignments: InMemoryPatientIntakeAssignmentRepository,
) -> IntakeSignatureService:
    return IntakeSignatureService(signature_repo, packets, documents, assignments)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _published_document(
    service: IntakeDocumentService, *, signer_roles: list[str] | None = None
) -> dict[str, object]:
    draft = service.create(
        title="Consent to treatment",
        body_markdown=_CONSENT_BODY,
        signer_roles=signer_roles,
    )
    return service.publish(str(draft["id"]), _CLINICIAN)


def _new_version(service: IntakeDocumentService, document: dict[str, object]) -> dict[str, object]:
    """A second published version of the same document."""
    draft = service.new_version(str(document["id"]))
    service.update_draft(str(draft["id"]), body_markdown=f"{_CONSENT_BODY}\n\nAnd this too.")
    return service.publish(str(draft["id"]), _CLINICIAN)


def _form_with_consent(
    packet_service: IntakePacketService,
    document: dict[str, object],
    *,
    resign: bool = False,
    publish: bool = True,
) -> str:
    template = packet_service.create_template("Intake", _CLINICIAN)
    version_id = str(packet_service.list_versions(str(template["id"]))[0]["id"])
    packet_service.replace_items(
        version_id,
        [
            ItemDraft(
                key="consent",
                item_type="consent_document",
                resign_on_new_version=resign,
                config={"document_key": str(document["document_key"])},
            ),
        ],
    )
    if publish:
        packet_service.publish(version_id, _CLINICIAN)
    return version_id


def _assigned(assignment_service: IntakeAssignmentService, version_id: str) -> dict[str, object]:
    assignment, _ = assignment_service.assign(_PATIENT, version_id, _CLINICIAN)
    return assignment


def _consent_item(assignment_service: IntakeAssignmentService, version_id: str) -> str:
    return next(
        str(row["id"])
        for row in assignment_service.items(version_id)
        if row["item_type"] == "consent_document"
    )


def _request(
    assignment: dict[str, object],
    item_id: str,
    *,
    signer_role: str = "patient",
    typed_name: str = "Ada Lovelace",
    affirmed: bool = True,
    auth_strength: str = "stepped_up",
) -> SigningRequest:
    return SigningRequest(
        assignment=assignment,
        patient_id=_PATIENT,
        item_id=item_id,
        signer_role=signer_role,
        typed_name=typed_name,
        affirmed=affirmed,
        auth_strength=auth_strength,
        session_id="session-handle",
        ip="203.0.113.7",
        user_agent="Mozilla/5.0",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWhatGetsRecorded:
    def test_a_signature_names_the_version_the_form_pinned(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        document = _published_document(document_service)
        version_id = _form_with_consent(packet_service, document)
        assignment = _assigned(assignment_service, version_id)
        item_id = _consent_item(assignment_service, version_id)

        signature = service.sign(_request(assignment, item_id))

        assert signature["document_version_id"] == document["id"]
        assert signature["document_digest"] == document["digest"]
        assert signature["signer_typed_name"] == "Ada Lovelace"
        assert signature["signer_role"] == "patient"
        assert signature["auth_strength"] == "stepped_up"
        assert signature["session_id"] == "session-handle"
        assert signature["consent_statement_version"] == CURRENT_CONSENT_STATEMENT_VERSION
        assert signature["superseded_at"] is None

    def test_the_stored_evidence_digest_recomputes_from_the_row(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        """The whole point of storing it: the row can be checked against itself."""
        document = _published_document(document_service)
        version_id = _form_with_consent(packet_service, document)
        assignment = _assigned(assignment_service, version_id)
        item_id = _consent_item(assignment_service, version_id)

        signature = service.sign(_request(assignment, item_id))
        assert signature["evidence_digest"] == evidence_digest(signature)

    def test_an_edited_column_no_longer_recomputes(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        """What the digest is for, stated as the failure it is meant to catch."""
        document = _published_document(document_service)
        version_id = _form_with_consent(packet_service, document)
        assignment = _assigned(assignment_service, version_id)
        item_id = _consent_item(assignment_service, version_id)

        signature = dict(service.sign(_request(assignment, item_id)))
        signature["signer_typed_name"] = "Somebody Else"
        assert signature["evidence_digest"] != evidence_digest(signature)

    def test_the_typed_name_is_trimmed(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        document = _published_document(document_service)
        version_id = _form_with_consent(packet_service, document)
        assignment = _assigned(assignment_service, version_id)
        item_id = _consent_item(assignment_service, version_id)

        signature = service.sign(_request(assignment, item_id, typed_name="  Ada Lovelace  "))
        assert signature["signer_typed_name"] == "Ada Lovelace"


class TestWhatIsRefused:
    @pytest.fixture
    def ready(
        self,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> tuple[dict[str, object], str]:
        document = _published_document(document_service)
        version_id = _form_with_consent(packet_service, document)
        assignment = _assigned(assignment_service, version_id)
        return assignment, _consent_item(assignment_service, version_id)

    def test_an_unticked_affirmation(
        self, service: IntakeSignatureService, ready: tuple[dict[str, object], str]
    ) -> None:
        assignment, item_id = ready
        with pytest.raises(NotAffirmedError):
            service.sign(_request(assignment, item_id, affirmed=False))

    @pytest.mark.parametrize("typed_name", ["", "   ", "\t\n"])
    def test_a_blank_name(
        self,
        service: IntakeSignatureService,
        ready: tuple[dict[str, object], str],
        typed_name: str,
    ) -> None:
        assignment, item_id = ready
        with pytest.raises(TypedNameError):
            service.sign(_request(assignment, item_id, typed_name=typed_name))

    def test_a_name_longer_than_the_column(
        self, service: IntakeSignatureService, ready: tuple[dict[str, object], str]
    ) -> None:
        assignment, item_id = ready
        with pytest.raises(TypedNameError):
            service.sign(_request(assignment, item_id, typed_name="A" * 161))

    def test_a_role_this_document_does_not_ask_for(
        self, service: IntakeSignatureService, ready: tuple[dict[str, object], str]
    ) -> None:
        """The document defaults to the patient alone, so guardian is refused."""
        assignment, item_id = ready
        with pytest.raises(SignerRoleNotAskedError):
            service.sign(_request(assignment, item_id, signer_role="guardian"))

    def test_a_role_that_is_not_a_role_at_all(
        self, service: IntakeSignatureService, ready: tuple[dict[str, object], str]
    ) -> None:
        assignment, item_id = ready
        with pytest.raises(SignerRoleNotAskedError):
            service.sign(_request(assignment, item_id, signer_role="clinician"))

    def test_an_item_that_is_not_on_this_form(
        self, service: IntakeSignatureService, ready: tuple[dict[str, object], str]
    ) -> None:
        assignment, _ = ready
        with pytest.raises(NotASignableItemError):
            service.sign(_request(assignment, "no-such-item"))

    def test_an_item_that_is_not_a_document_to_sign(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        """Indistinguishable from an item that is not there, on purpose."""
        document = _published_document(document_service)
        template = packet_service.create_template("Mixed", _CLINICIAN)
        version_id = str(packet_service.list_versions(str(template["id"]))[0]["id"])
        packet_service.replace_items(
            version_id,
            [
                ItemDraft(key="reason", item_type="reason"),
                ItemDraft(
                    key="consent",
                    item_type="consent_document",
                    config={"document_key": str(document["document_key"])},
                ),
            ],
        )
        packet_service.publish(version_id, _CLINICIAN)
        assignment = _assigned(assignment_service, version_id)
        reason_id = next(
            str(row["id"]) for row in assignment_service.items(version_id) if row["key"] == "reason"
        )
        with pytest.raises(NotASignableItemError):
            service.sign(_request(assignment, reason_id))

    def test_a_second_signature_by_the_same_role(
        self, service: IntakeSignatureService, ready: tuple[dict[str, object], str]
    ) -> None:
        assignment, item_id = ready
        service.sign(_request(assignment, item_id))
        with pytest.raises(AlreadySignedError):
            service.sign(_request(assignment, item_id))

    def test_a_form_that_has_been_handed_in(
        self,
        service: IntakeSignatureService,
        ready: tuple[dict[str, object], str],
    ) -> None:
        assignment, item_id = ready
        closed = {**assignment, "status": "submitted"}
        with pytest.raises(AssignmentClosedError):
            service.sign(_request(closed, item_id))

    def test_a_form_published_pointing_at_no_version(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packets: InMemoryIntakePacketRepository,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        """The publisher prevents this; the signer refuses it anyway.

        Signing whatever version happens to be newest would be the one
        failure this whole feature exists to make impossible, so an unpinned
        item is a refusal rather than a fallback.
        """
        document = _published_document(document_service)
        version_id = _form_with_consent(packet_service, document)
        item_id = _consent_item(assignment_service, version_id)
        packets.set_item_config(item_id, {"document_key": str(document["document_key"])})
        assignment = _assigned(assignment_service, version_id)
        with pytest.raises(UnsignableDocumentError):
            service.sign(_request(assignment, item_id))


class TestTheResignRule:
    def test_a_newer_version_refuses_a_signature_when_the_item_asks_for_one(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        document = _published_document(document_service)
        version_id = _form_with_consent(packet_service, document, resign=True)
        assignment = _assigned(assignment_service, version_id)
        item_id = _consent_item(assignment_service, version_id)

        _new_version(document_service, document)

        with pytest.raises(StaleDocumentVersionError):
            service.sign(_request(assignment, item_id))

    def test_without_the_rule_the_pinned_version_is_still_what_is_signed(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        """The settled decision: somebody signs the words they were shown."""
        document = _published_document(document_service)
        version_id = _form_with_consent(packet_service, document, resign=False)
        assignment = _assigned(assignment_service, version_id)
        item_id = _consent_item(assignment_service, version_id)

        newer = _new_version(document_service, document)
        assert newer["id"] != document["id"]

        signature = service.sign(_request(assignment, item_id))
        assert signature["document_version_id"] == document["id"]
        assert signature["document_digest"] == document["digest"]

    def test_the_rule_is_quiet_until_a_newer_version_exists(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        document = _published_document(document_service)
        version_id = _form_with_consent(packet_service, document, resign=True)
        assignment = _assigned(assignment_service, version_id)
        item_id = _consent_item(assignment_service, version_id)

        signature = service.sign(_request(assignment, item_id))
        assert signature["document_version_id"] == document["id"]


class TestSigningAnswersTheQuestion:
    def test_a_signed_consent_item_completes_the_form(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        document = _published_document(document_service)
        version_id = _form_with_consent(packet_service, document)
        assignment = _assigned(assignment_service, version_id)
        item_id = _consent_item(assignment_service, version_id)

        before = assignment_service.progress(assignment, _PATIENT)
        assert before.complete is False
        assert before.missing == [item_id]

        signature = service.sign(_request(assignment, item_id))

        current = assignment_service.get_for_patient(str(assignment["id"]), _PATIENT)
        assert current is not None
        after = assignment_service.progress(current, _PATIENT)
        assert after.complete is True
        assert after.missing == []

        answers = assignment_service.answers(str(assignment["id"]), _PATIENT)
        assert answers[item_id] == {"signed": True, "signature_id": str(signature["id"])}

    def test_signing_moves_the_form_out_of_sent(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        """Somebody who has signed something has started the form."""
        document = _published_document(document_service)
        version_id = _form_with_consent(packet_service, document)
        assignment = _assigned(assignment_service, version_id)
        assert assignment["status"] == "assigned"

        service.sign(_request(assignment, _consent_item(assignment_service, version_id)))

        current = assignment_service.get_for_patient(str(assignment["id"]), _PATIENT)
        assert current is not None
        assert current["status"] == "in_progress"

    def test_a_document_asking_for_two_signatures_is_not_done_with_one(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        """Reporting it finished would let the form go short of what was asked."""
        document = _published_document(document_service, signer_roles=["patient", "guardian"])
        version_id = _form_with_consent(packet_service, document)
        assignment = _assigned(assignment_service, version_id)
        item_id = _consent_item(assignment_service, version_id)

        service.sign(_request(assignment, item_id, signer_role="patient"))
        current = assignment_service.get_for_patient(str(assignment["id"]), _PATIENT)
        assert current is not None
        assert assignment_service.progress(current, _PATIENT).missing == [item_id]

        service.sign(
            _request(current, item_id, signer_role="guardian", typed_name="Mary Somerville")
        )
        current = assignment_service.get_for_patient(str(assignment["id"]), _PATIENT)
        assert current is not None
        assert assignment_service.progress(current, _PATIENT).complete is True

    def test_a_guardian_signature_carries_its_own_name(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        """v1 has no separate guardian identity — the role and the name are it."""
        document = _published_document(document_service, signer_roles=["patient", "guardian"])
        version_id = _form_with_consent(packet_service, document)
        assignment = _assigned(assignment_service, version_id)
        item_id = _consent_item(assignment_service, version_id)

        signature = service.sign(
            _request(assignment, item_id, signer_role="guardian", typed_name="Mary Somerville")
        )
        assert signature["signer_role"] == "guardian"
        assert signature["signer_typed_name"] == "Mary Somerville"
        assert signature["patient_id"] == _PATIENT

    def test_both_signatures_are_listed_for_the_patient(
        self,
        service: IntakeSignatureService,
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        document = _published_document(document_service, signer_roles=["patient", "guardian"])
        version_id = _form_with_consent(packet_service, document)
        assignment = _assigned(assignment_service, version_id)
        item_id = _consent_item(assignment_service, version_id)

        service.sign(_request(assignment, item_id, signer_role="patient"))
        service.sign(
            _request(assignment, item_id, signer_role="guardian", typed_name="Mary Somerville")
        )

        live = service.live_for_assignment(str(assignment["id"]), _PATIENT)
        assert [row["signer_role"] for row in live] == ["patient", "guardian"]
