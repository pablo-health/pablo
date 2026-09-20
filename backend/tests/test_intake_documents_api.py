# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for the practice's consent documents.

Four things are checked here that the service tests cannot see.

**The doors.** The clinician routes are the ordinary clinician surface, so
an unauthenticated caller is a 401. The portal route takes a patient
principal, refuses a single-factor one, and refuses a clinician bearer —
neither credential satisfies the other's door.

**The status codes.** A 409 means the version is published and the change
belongs on a new one. A 404 on the portal route is the one answer a draft,
a missing id and another practice's id all get, so nothing on that surface
confirms somebody else's document is real.

**The audit row.** Publishing is the only write here that is logged, and
what it logs is the document, the version and the digest — never the text.

**What each surface hands back.** The practice gets the markdown it typed
plus the server's own rendering of it, so its preview is not a second
renderer's guess. The portal gets the rendering and not the source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
from app.main import app as real_app
from app.models.audit import AuditAction, ResourceType
from app.repositories import InMemoryIntakeDocumentRepository
from app.repositories.audit import InMemoryAuditRepository
from app.routes import intake_documents
from app.routes.intake_documents import (
    get_intake_document_service,
    get_patient_intake_document_service,
)
from app.services.audit_service import AuditService, get_audit_service
from app.services.intake_document_service import IntakeDocumentService
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

BASE = "/api/intake/documents"
PORTAL = "/api/patient/intake/documents"

_PATIENT = "11111111-1111-4111-8111-111111111111"
_TOKEN = "credential-of-the-patient"
_TOKEN_WEAK = "single-factor-credential-of-the-patient"

_BODY = "# Consent\n\nPlease read this **carefully**."


class _OnePatientResolver:
    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        strengths = {
            _TOKEN: AuthStrength.STEPPED_UP,
            _TOKEN_WEAK: AuthStrength.SINGLE_FACTOR,
        }
        strength = strengths.get(credential.value)
        if strength is None:
            return None
        return PatientContext(
            patient_id=_PATIENT,
            practice_schema="practice_test_documents",
            credential_kind="bearer",
            auth_strength=strength,
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
def audit_repo() -> InMemoryAuditRepository:
    """The real AuditService with its storage swapped."""
    return InMemoryAuditRepository()


@pytest.fixture
def service(documents: InMemoryIntakeDocumentRepository) -> IntakeDocumentService:
    return IntakeDocumentService(documents)


@pytest.fixture
def practice(
    client: TestClient,
    service: IntakeDocumentService,
    audit_repo: InMemoryAuditRepository,
) -> TestClient:
    """The shared clinician client, with the document store in memory."""
    real_app.dependency_overrides[get_intake_document_service] = lambda: service
    real_app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)
    return client


@pytest.fixture
def portal(service: IntakeDocumentService, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The portal router alone, with a patient front door and no database."""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(intake_documents.patient_router)

    registry = PatientResolverRegistry()
    registry.register(_OnePatientResolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    app.dependency_overrides[get_patient_intake_document_service] = lambda: service

    monkeypatch.setattr(patient_context_module, "get_db_session", object)
    monkeypatch.setattr(patient_context_module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(patient_context_module, "arm_current_patient_id", lambda _s, _p: None)

    with TestClient(app) as test_client:
        yield test_client


def _create(client: TestClient, title: str = "Consent", body: str = _BODY) -> dict[str, Any]:
    response = client.post(BASE, json={"title": title, "body_markdown": body})
    assert response.status_code == 201, response.text
    return response.json()


def _publish(client: TestClient, document_id: str) -> dict[str, Any]:
    response = client.post(f"{BASE}/{document_id}/publish")
    assert response.status_code == 200, response.text
    return response.json()


def _entries(audit_repo: InMemoryAuditRepository, actor_id: str) -> list[Any]:
    return list(audit_repo.list_for_user(actor_id))


# ---------------------------------------------------------------------------
# The doors
# ---------------------------------------------------------------------------


class TestTheClinicianDoor:
    def test_without_a_credential_is_401(self) -> None:
        with TestClient(real_app) as anonymous:
            assert anonymous.get(BASE).status_code == 401


class TestThePortalDoor:
    def test_without_a_credential_is_401(self, portal: TestClient) -> None:
        assert portal.get(f"{PORTAL}/anything").status_code == 401

    def test_an_unknown_credential_is_401(self, portal: TestClient) -> None:
        response = portal.get(f"{PORTAL}/anything", headers=_auth("not-a-token"))
        assert response.status_code == 401

    def test_one_factor_is_refused(self, portal: TestClient, practice: TestClient) -> None:
        published = _publish(practice, _create(practice)["id"])
        response = portal.get(f"{PORTAL}/{published['id']}", headers=_auth(_TOKEN_WEAK))
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "STEP_UP_REQUIRED"


# ---------------------------------------------------------------------------
# Writing a document
# ---------------------------------------------------------------------------


class TestWritingADocument:
    def test_it_starts_as_an_unpublished_version_one(self, practice: TestClient) -> None:
        created = _create(practice)
        assert created["version"] == 1
        assert created["published_at"] is None

    def test_the_practice_gets_its_own_markdown_back(self, practice: TestClient) -> None:
        assert _create(practice)["body_markdown"] == _BODY

    def test_the_practice_also_gets_the_servers_rendering(self, practice: TestClient) -> None:
        """So a preview is the server's answer, not a second renderer's."""
        created = _create(practice)
        assert "<h2>Consent</h2>" in created["rendered_html"]
        assert "<strong>carefully</strong>" in created["rendered_html"]

    def test_a_signer_nobody_recognises_is_422(self, practice: TestClient) -> None:
        response = practice.post(
            BASE, json={"title": "Consent", "body_markdown": "x", "signer_roles": ["lawyer"]}
        )
        assert response.status_code == 422
        assert "sign" in response.json()["error"]["message"]

    def test_the_list_shows_the_newest_version_of_each(self, practice: TestClient) -> None:
        first = _create(practice, title="Consent")
        _create(practice, title="Telehealth")
        _publish(practice, first["id"])
        practice.post(f"{BASE}/{first['id']}/new-version")

        listed = practice.get(BASE).json()
        assert [(row["title"], row["version"]) for row in listed] == [
            ("Consent", 2),
            ("Telehealth", 1),
        ]

    def test_an_id_that_does_not_exist_is_404(self, practice: TestClient) -> None:
        assert practice.get(f"{BASE}/33333333-3333-4333-8333-333333333333").status_code == 404


class TestTheFreeze:
    def test_a_draft_can_be_edited(self, practice: TestClient) -> None:
        created = _create(practice)
        response = practice.put(
            f"{BASE}/{created['id']}", json={"body_markdown": "Rewritten words."}
        )
        assert response.status_code == 200
        assert response.json()["body_markdown"] == "Rewritten words."
        assert response.json()["digest"] != created["digest"]

    def test_editing_a_published_version_is_409(self, practice: TestClient) -> None:
        published = _publish(practice, _create(practice)["id"])
        response = practice.put(f"{BASE}/{published['id']}", json={"body_markdown": "New."})
        assert response.status_code == 409
        assert "new version" in response.json()["error"]["message"]

    def test_publishing_twice_is_409(self, practice: TestClient) -> None:
        published = _publish(practice, _create(practice)["id"])
        assert practice.post(f"{BASE}/{published['id']}/publish").status_code == 409

    def test_a_new_version_carries_the_text_and_the_key(self, practice: TestClient) -> None:
        published = _publish(practice, _create(practice)["id"])
        response = practice.post(f"{BASE}/{published['id']}/new-version")
        assert response.status_code == 201
        draft = response.json()
        assert draft["version"] == 2
        assert draft["document_key"] == published["document_key"]
        assert draft["body_markdown"] == published["body_markdown"]
        assert draft["published_at"] is None


class TestTheAuditRow:
    def test_publishing_is_recorded(
        self, practice: TestClient, audit_repo: InMemoryAuditRepository, mock_user_id: str
    ) -> None:
        published = _publish(practice, _create(practice)["id"])
        entries = _entries(audit_repo, mock_user_id)
        assert len(entries) == 1
        assert entries[0].action == AuditAction.INTAKE_DOCUMENT_PUBLISHED
        assert entries[0].resource_type == ResourceType.INTAKE_DOCUMENT
        assert entries[0].resource_id == published["id"]

    def test_it_names_the_text_by_its_digest_and_not_by_its_words(
        self, practice: TestClient, audit_repo: InMemoryAuditRepository, mock_user_id: str
    ) -> None:
        published = _publish(practice, _create(practice)["id"])
        changes = _entries(audit_repo, mock_user_id)[0].changes
        assert changes == {
            "document_key": published["document_key"],
            "version": 1,
            "digest": published["digest"],
        }
        assert "Consent" not in str(changes)

    def test_a_draft_edit_is_not_recorded(
        self, practice: TestClient, audit_repo: InMemoryAuditRepository, mock_user_id: str
    ) -> None:
        created = _create(practice)
        practice.put(f"{BASE}/{created['id']}", json={"body_markdown": "Changed."})
        assert _entries(audit_repo, mock_user_id) == []


# ---------------------------------------------------------------------------
# Reading one to sign
# ---------------------------------------------------------------------------


class TestReadingOneToSign:
    def test_a_published_document_comes_back_rendered(
        self, portal: TestClient, practice: TestClient
    ) -> None:
        published = _publish(practice, _create(practice)["id"])
        response = portal.get(f"{PORTAL}/{published['id']}", headers=_auth(_TOKEN))
        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Consent"
        assert body["version"] == 1
        assert body["digest"] == published["digest"]
        assert "<h2>Consent</h2>" in body["rendered_html"]

    def test_the_source_is_not_handed_out(self, portal: TestClient, practice: TestClient) -> None:
        """The rendered words are what was read. There is no second copy."""
        published = _publish(practice, _create(practice)["id"])
        body = portal.get(f"{PORTAL}/{published['id']}", headers=_auth(_TOKEN)).json()
        assert "body_markdown" not in body

    def test_a_draft_is_404(self, portal: TestClient, practice: TestClient) -> None:
        created = _create(practice)
        response = portal.get(f"{PORTAL}/{created['id']}", headers=_auth(_TOKEN))
        assert response.status_code == 404

    def test_a_document_that_does_not_exist_is_the_same_404(
        self, portal: TestClient, practice: TestClient
    ) -> None:
        """Which is what stops the surface confirming somebody else's is real."""
        created = _create(practice)
        missing = portal.get(
            f"{PORTAL}/33333333-3333-4333-8333-333333333333", headers=_auth(_TOKEN)
        )
        draft = portal.get(f"{PORTAL}/{created['id']}", headers=_auth(_TOKEN))
        assert missing.status_code == draft.status_code == 404
        assert missing.json()["error"]["message"] == draft.json()["error"]["message"]

    def test_a_superseded_version_is_still_readable(
        self, portal: TestClient, practice: TestClient
    ) -> None:
        """A signature points at a version, so that version has to stay up."""
        first = _publish(practice, _create(practice)["id"])
        draft = practice.post(f"{BASE}/{first['id']}/new-version").json()
        practice.put(f"{BASE}/{draft['id']}", json={"body_markdown": "Newer words."})
        _publish(practice, draft["id"])

        response = portal.get(f"{PORTAL}/{first['id']}", headers=_auth(_TOKEN))
        assert response.status_code == 200
        assert response.json()["digest"] == first["digest"]

    def test_nothing_a_practice_typed_comes_back_as_markup(
        self, portal: TestClient, practice: TestClient
    ) -> None:
        created = _create(practice, body="<script>alert(1)</script>\n\n[x](javascript:alert(1))")
        published = _publish(practice, created["id"])
        html = portal.get(f"{PORTAL}/{published['id']}", headers=_auth(_TOKEN)).json()[
            "rendered_html"
        ]
        assert "<script>" not in html
        assert "<a " not in html
