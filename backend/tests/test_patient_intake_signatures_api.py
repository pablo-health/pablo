# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for signing a consent document from the portal.

The service tests prove the rules. These prove the door: which principal
the route trusts, which status code each refusal gets, what the response
hands back and what the audit entry says about it.

The patient front door is the real ``get_patient_context`` with a
synthesized resolver and the session arming patched out, exactly as the
assignment tests do it — so what is under test is what the ROUTE does with
a principal. Two-patient isolation at the database layer belongs to
``tests_integration/database/test_patient_intake_signatures_rls.py``; what
is provable here is the half a row policy cannot fix, which is that no
field of the request names a patient at all.

The audit repository is the real service with its storage swapped, so
``actor_type`` and ``changes`` are the values production writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.api_errors import register_exception_handlers
from app.auth import patient_context as patient_context_module
from app.auth.patient_context import (
    AuthStrength,
    PatientContext,
    PatientCredential,
    PatientResolverRegistry,
    get_patient_resolver_registry,
)
from app.intake.consent_statement import CURRENT_CONSENT_STATEMENT_VERSION, consent_statement
from app.intake.items import ItemDraft
from app.intake.signatures import evidence_digest
from app.models.audit import ACTOR_TYPE_PATIENT, AuditAction, ResourceType
from app.repositories import (
    InMemoryIntakeDocumentRepository,
    InMemoryIntakePacketRepository,
    InMemoryPatientIntakeAssignmentRepository,
    InMemoryPatientIntakeSignatureRepository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.routes import patient_intake_assignments
from app.routes.patient_intake_assignments import (
    get_patient_intake_assignment_service,
    get_patient_intake_signature_service,
)
from app.services.audit_service import AuditService, get_audit_service
from app.services.intake_document_service import IntakeDocumentService
from app.services.intake_packet_service import IntakePacketService
from app.services.patient_intake_assignment_service import IntakeAssignmentService
from app.services.patient_intake_signature_service import IntakeSignatureService
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.audit import AuditLogEntry

_PATIENT_A = "11111111-1111-4111-8111-111111111111"
_PATIENT_B = "22222222-2222-4222-8222-222222222222"
_TOKEN_A = "credential-of-patient-a"
_TOKEN_B = "credential-of-patient-b"
_TOKEN_A_WEAK = "single-factor-credential-of-patient-a"
_SESSION_A = "session-handle-a"
_CLINICIAN = "clinician-1"

ASSIGNMENTS = "/api/patient/intake/assignments"
_CONSENT_BODY = "# Consent to treatment\n\nYou are agreeing to be treated here."


class _TwoPatientResolver:
    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        principals = {
            _TOKEN_A: (_PATIENT_A, AuthStrength.STEPPED_UP, _SESSION_A),
            _TOKEN_B: (_PATIENT_B, AuthStrength.STEPPED_UP, "session-handle-b"),
            _TOKEN_A_WEAK: (_PATIENT_A, AuthStrength.SINGLE_FACTOR, "session-handle-weak"),
        }
        found = principals.get(credential.value)
        if found is None:
            return None
        patient_id, strength, session_id = found
        return PatientContext(
            patient_id=patient_id,
            practice_schema="practice_test_intake",
            credential_kind="bearer",
            auth_strength=strength,
            session_id=session_id,
        )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures
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
    packets: InMemoryIntakePacketRepository, documents: InMemoryIntakeDocumentRepository
) -> IntakePacketService:
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
def signature_service(
    signature_repo: InMemoryPatientIntakeSignatureRepository,
    packets: InMemoryIntakePacketRepository,
    documents: InMemoryIntakeDocumentRepository,
    assignments: InMemoryPatientIntakeAssignmentRepository,
) -> IntakeSignatureService:
    return IntakeSignatureService(signature_repo, packets, documents, assignments)


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def patient_app(
    assignment_service: IntakeAssignmentService,
    signature_service: IntakeSignatureService,
    audit_repo: InMemoryAuditRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """The patient router alone, with a patient front door and no database."""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(patient_intake_assignments.router)

    @app.middleware("http")
    async def _stash_clinician_identity(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.headers.get("x-test-clinician-verified"):
            request.state.verified_identity = {"uid": _CLINICIAN}
        return await call_next(request)

    registry = PatientResolverRegistry()
    registry.register(_TwoPatientResolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    app.dependency_overrides[get_patient_intake_assignment_service] = lambda: assignment_service
    app.dependency_overrides[get_patient_intake_signature_service] = lambda: signature_service
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)

    monkeypatch.setattr(patient_context_module, "get_db_session", object)
    monkeypatch.setattr(patient_context_module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(patient_context_module, "arm_current_patient_id", lambda _s, _p: None)
    return app


@pytest.fixture
def portal(patient_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(patient_app) as c:
        yield c


@pytest.fixture
def document(document_service: IntakeDocumentService) -> dict[str, object]:
    draft = document_service.create(title="Consent to treatment", body_markdown=_CONSENT_BODY)
    return document_service.publish(str(draft["id"]), _CLINICIAN)


@pytest.fixture
def form(packet_service: IntakePacketService, document: dict[str, object]) -> str:
    return _publish_consent_form(packet_service, document)


@pytest.fixture
def assignment(assignment_service: IntakeAssignmentService, form: str) -> dict[str, object]:
    row, _ = assignment_service.assign(_PATIENT_A, form, _CLINICIAN)
    return row


@pytest.fixture
def item_id(assignment_service: IntakeAssignmentService, form: str) -> str:
    return next(
        str(row["id"])
        for row in assignment_service.items(form)
        if row["item_type"] == "consent_document"
    )


def _publish_consent_form(
    packet_service: IntakePacketService,
    document: dict[str, object],
    *,
    resign: bool = False,
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
    packet_service.publish(version_id, _CLINICIAN)
    return version_id


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "item_id": "",
        "signer_role": "patient",
        "typed_name": "Ada Lovelace",
        "affirm": True,
    }
    body.update(overrides)
    return body


def _signatures_url(assignment_id: str) -> str:
    return f"{ASSIGNMENTS}/{assignment_id}/signatures"


def _signed_entries(audit_repo: InMemoryAuditRepository) -> list[AuditLogEntry]:
    """Every signing entry written for patient A, newest first.

    Read back through the repository's own query rather than off a private
    list, so the entry has to be findable the way the self-audit screen
    finds it — which is the property that matters about an audit row.
    """
    return [
        entry
        for entry in audit_repo.list_for_user(_PATIENT_A)
        if entry.action == AuditAction.PATIENT_CONSENT_SIGNED.value
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSigning:
    def test_a_stepped_up_patient_signs_and_gets_the_evidence_back(
        self,
        portal: TestClient,
        assignment: dict[str, object],
        item_id: str,
        document: dict[str, object],
    ) -> None:
        response = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id),
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["document_version_id"] == document["id"]
        assert body["document_digest"] == document["digest"]
        assert body["signer_role"] == "patient"
        assert body["signer_typed_name"] == "Ada Lovelace"
        assert body["auth_strength"] == AuthStrength.STEPPED_UP.value
        assert body["session_id"] == _SESSION_A
        assert body["consent_statement_version"] == CURRENT_CONSENT_STATEMENT_VERSION
        assert body["consent_statement"] == consent_statement()
        assert body["assignment_id"] == assignment["id"]
        assert body["item_id"] == item_id

    def test_the_response_leaves_the_request_details_behind(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        """Stored as evidence, not read back out to whoever just signed."""
        response = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id),
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 201
        assert "ip" not in response.json()
        assert "user_agent" not in response.json()

    def test_the_stored_row_carries_the_request_details(
        self,
        portal: TestClient,
        assignment: dict[str, object],
        item_id: str,
        signature_repo: InMemoryPatientIntakeSignatureRepository,
    ) -> None:
        """They are evidence about the circumstances, so they are recorded."""
        portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id),
            headers={**_auth(_TOKEN_A), "User-Agent": "portal-test/1.0"},
        )
        stored = signature_repo.list_live_for_assignment(str(assignment["id"]), _PATIENT_A)
        assert len(stored) == 1
        assert stored[0]["user_agent"] == "portal-test/1.0"
        assert stored[0]["evidence_digest"] == evidence_digest(stored[0])

    def test_the_form_is_ready_to_send_once_it_is_signed(
        self,
        portal: TestClient,
        assignment: dict[str, object],
        item_id: str,
    ) -> None:
        before = portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth(_TOKEN_A)).json()
        assert before["progress"]["complete"] is False

        portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id),
            headers=_auth(_TOKEN_A),
        )

        after = portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth(_TOKEN_A)).json()
        assert after["progress"]["complete"] is True
        assert after["items"][0]["value"]["signed"] is True

    def test_signing_is_audited_without_the_name_that_was_typed(
        self,
        portal: TestClient,
        assignment: dict[str, object],
        item_id: str,
        document: dict[str, object],
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id),
            headers=_auth(_TOKEN_A),
        )
        entries = _signed_entries(audit_repo)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.actor_type == ACTOR_TYPE_PATIENT
        assert entry.user_id == _PATIENT_A
        assert entry.patient_id == _PATIENT_A
        assert entry.resource_type == ResourceType.PATIENT_INTAKE_ASSIGNMENT.value
        assert entry.resource_id == assignment["id"]
        assert entry.changes == {
            "document_version_id": str(document["id"]),
            "signer_role": "patient",
        }
        assert "Ada" not in str(entry.changes)


class TestWhoMaySign:
    def test_a_single_factor_session_is_refused(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        response = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id),
            headers=_auth(_TOKEN_A_WEAK),
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "STEP_UP_REQUIRED"

    def test_no_credential_at_all_is_refused(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        response = portal.post(_signatures_url(str(assignment["id"])), json=_body(item_id=item_id))
        assert response.status_code == 401

    def test_a_clinician_credential_on_a_patient_route_is_refused(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        response = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id),
            headers={**_auth(_TOKEN_A), "x-test-clinician-verified": "1"},
        )
        assert response.status_code == 401

    def test_another_patients_form_is_a_404(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        """Indistinguishable from an id that names nothing."""
        response = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id),
            headers=_auth(_TOKEN_B),
        )
        assert response.status_code == 404

    def test_the_request_names_no_patient(self) -> None:
        """The structural guarantee: there is no field to put an id in."""
        from app.models.patient_intake_signature_api import (  # noqa: PLC0415
            SignDocumentRequest,
        )

        assert "patient_id" not in SignDocumentRequest.model_fields
        assert set(SignDocumentRequest.model_fields) == {
            "item_id",
            "signer_role",
            "typed_name",
            "affirm",
        }

    def test_nothing_the_patient_signed_for_is_written_by_a_client_field(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        """Extra fields are refused rather than ignored."""
        response = portal.post(
            _signatures_url(str(assignment["id"])),
            json={**_body(item_id=item_id), "patient_id": _PATIENT_B},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 422


class TestRefusals:
    def test_an_unticked_affirmation_is_422(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        response = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id, affirm=False),
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 422
        assert "box" in response.json()["error"]["message"].lower()

    def test_a_missing_affirmation_is_422(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        """Absent defaults to false: a client that forgot to ask is refused."""
        body = _body(item_id=item_id)
        del body["affirm"]
        response = portal.post(
            _signatures_url(str(assignment["id"])), json=body, headers=_auth(_TOKEN_A)
        )
        assert response.status_code == 422

    def test_an_empty_name_is_422(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        response = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id, typed_name="   "),
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 422
        assert "name" in response.json()["error"]["message"].lower()

    def test_a_role_the_document_does_not_ask_for_is_422(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        response = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id, signer_role="guardian"),
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 422

    def test_an_item_that_is_not_a_document_to_sign_is_404(
        self, portal: TestClient, assignment: dict[str, object]
    ) -> None:
        response = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id="no-such-item"),
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 404

    def test_signing_twice_is_409(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        first = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id),
            headers=_auth(_TOKEN_A),
        )
        assert first.status_code == 201
        second = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id),
            headers=_auth(_TOKEN_A),
        )
        assert second.status_code == 409
        assert "already been signed" in second.json()["error"]["message"]

    def test_a_second_attempt_writes_no_second_audit_row(
        self,
        portal: TestClient,
        assignment: dict[str, object],
        item_id: str,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        for _ in range(2):
            portal.post(
                _signatures_url(str(assignment["id"])),
                json=_body(item_id=item_id),
                headers=_auth(_TOKEN_A),
            )
        assert len(_signed_entries(audit_repo)) == 1


class TestTheResignRule:
    def test_a_newer_version_is_409_when_the_item_asks_for_a_new_signature(
        self,
        portal: TestClient,
        document: dict[str, object],
        document_service: IntakeDocumentService,
        packet_service: IntakePacketService,
        assignment_service: IntakeAssignmentService,
    ) -> None:
        version_id = _publish_consent_form(packet_service, document, resign=True)
        row, _ = assignment_service.assign(_PATIENT_A, version_id, _CLINICIAN)
        consent_id = next(
            str(item["id"])
            for item in assignment_service.items(version_id)
            if item["item_type"] == "consent_document"
        )

        draft = document_service.new_version(str(document["id"]))
        document_service.update_draft(str(draft["id"]), body_markdown=f"{_CONSENT_BODY}\n\nMore.")
        document_service.publish(str(draft["id"]), _CLINICIAN)

        response = portal.post(
            _signatures_url(str(row["id"])),
            json=_body(item_id=consent_id),
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 409
        assert "newer version" in response.json()["error"]["message"]

    def test_without_the_rule_the_pinned_version_is_signed(
        self,
        portal: TestClient,
        document: dict[str, object],
        document_service: IntakeDocumentService,
        assignment: dict[str, object],
        item_id: str,
    ) -> None:
        draft = document_service.new_version(str(document["id"]))
        document_service.update_draft(str(draft["id"]), body_markdown=f"{_CONSENT_BODY}\n\nMore.")
        newer = document_service.publish(str(draft["id"]), _CLINICIAN)
        assert newer["id"] != document["id"]

        response = portal.post(
            _signatures_url(str(assignment["id"])),
            json=_body(item_id=item_id),
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 201
        assert response.json()["document_version_id"] == document["id"]


class TestTheSaveRouteCannotForgeASignature:
    def test_saving_an_answer_to_a_consent_item_is_refused(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        """The hole this closes: a value that asserts a signature nobody took.

        Completion reads the answer rather than the signature, so a patient
        who could write this value through the ordinary save route would be
        handing in a form with an unsigned consent on it.
        """
        response = portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{item_id}",
            json={"value": {"signed": True, "signature_id": "made-up"}},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 422
        assert "signed" in response.json()["error"]["message"].lower()

    def test_and_the_form_is_still_not_ready_to_send(
        self, portal: TestClient, assignment: dict[str, object], item_id: str
    ) -> None:
        portal.put(
            f"{ASSIGNMENTS}/{assignment['id']}/items/{item_id}",
            json={"value": {"signed": True, "signature_id": "made-up"}},
            headers=_auth(_TOKEN_A),
        )
        detail = portal.get(f"{ASSIGNMENTS}/{assignment['id']}", headers=_auth(_TOKEN_A)).json()
        assert detail["progress"]["complete"] is False
        assert detail["progress"]["missing"] == [item_id]
