# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for a patient sending a document to their own chart.

The service tests prove the rules about the file. These prove the door:
which principal the route trusts, which status code each refusal gets, and
what the audit entry says about what happened.

The patient front door is the real ``get_patient_context`` with a
synthesized resolver and the session arming patched out, exactly as the
intake-signature tests do it — so what is under test is what the ROUTE does
with a principal. Two-patient isolation at the database layer belongs to
``tests_integration/database/test_patient_documents_rls.py``; what is
provable here is the half a row policy cannot fix, which is that no field
of any request names a patient at all.

Storage is the same fake GCS the clinician route tests use, so the signed
URL, the blob metadata re-check and the size the finalize writes are all
exercised rather than stubbed.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from app.models import DocumentCategory, PatientDocument
from app.models.audit import ACTOR_TYPE_PATIENT, AuditAction, ResourceType
from app.rate_limit import get_patient_document_init_limiter
from app.repositories import InMemoryPatientDocumentRepository
from app.repositories.audit import InMemoryAuditRepository
from app.routes import patient_documents
from app.routes.patient_documents import get_patient_documents_service_for_patient
from app.services import PatientDocumentsService
from app.services.audit_service import AuditService, get_audit_service
from app.services.file_storage import GcsFileStorage
from app.settings import Settings
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from google.cloud.exceptions import NotFound

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.audit import AuditLogEntry

_PATIENT_A = "11111111-1111-4111-8111-111111111111"
_PATIENT_B = "22222222-2222-4222-8222-222222222222"
_TOKEN_A = "credential-of-patient-a"
_TOKEN_B = "credential-of-patient-b"
_TOKEN_A_WEAK = "single-factor-credential-of-patient-a"
_SESSION_A = "session-handle-a"
_CLINICIAN = "33333333-3333-4333-8333-333333333333"

DOCUMENTS = "/api/patient/documents"
_BUCKET = "pablo-docs-test"
_MAX_BYTES = 25 * 1024 * 1024
_PDF = b"%PDF-1.7 fixture body"


# ---------------------------------------------------------------------------
# Fake storage — same shape as the clinician route tests
# ---------------------------------------------------------------------------


class _FakeBlob:
    def __init__(self, name: str) -> None:
        self.name = name
        self.size: int | None = None
        self.content_type: str | None = None
        self._data: bytes = b""
        self.last_signed_kwargs: dict[str, Any] = {}

    def reload(self) -> None:
        if self.size is None:
            raise NotFound("blob not found")

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self._data = data
        self.size = len(data)
        self.content_type = content_type

    def download_as_bytes(self) -> bytes:
        return self._data

    def generate_signed_url(self, **kwargs: Any) -> str:
        self.last_signed_kwargs = kwargs
        return f"https://fake.googleusercontent.example/{self.name}?sig=xyz"


class _FakeBucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self._blobs: dict[str, _FakeBlob] = {}

    def blob(self, object_name: str) -> _FakeBlob:
        return self._blobs.setdefault(object_name, _FakeBlob(object_name))


class _FakeStorageClient:
    def __init__(self) -> None:
        self._buckets: dict[str, _FakeBucket] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return self._buckets.setdefault(name, _FakeBucket(name))


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
            practice_schema="practice_test_documents",
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
def documents_settings() -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        patient_documents_gcs_bucket=_BUCKET,
        patient_documents_max_bytes=_MAX_BYTES,
        patient_documents_upload_url_ttl_seconds=300,
        patient_documents_download_url_ttl_seconds=300,
    )


@pytest.fixture
def doc_repo() -> InMemoryPatientDocumentRepository:
    repo = InMemoryPatientDocumentRepository()
    # The clinician who treats both patients, for the rows a patient must
    # not see: without a grant those rows would be invisible for the wrong
    # reason and the category assertions would prove nothing.
    repo.grant_access(_PATIENT_A, _CLINICIAN)
    repo.grant_access(_PATIENT_B, _CLINICIAN)
    return repo


@pytest.fixture
def fake_gcs() -> _FakeStorageClient:
    return _FakeStorageClient()


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def documents_service(
    doc_repo: InMemoryPatientDocumentRepository,
    documents_settings: Settings,
    fake_gcs: _FakeStorageClient,
) -> PatientDocumentsService:
    return PatientDocumentsService(
        repo=doc_repo,
        settings=documents_settings,
        storage=GcsFileStorage(client_factory=lambda: fake_gcs),
        tenant_id="practice_test_documents",
    )


@pytest.fixture(autouse=True)
def _fresh_limiter() -> Iterator[None]:
    """The init limiter is a process-wide singleton; reset it per test.

    Without this the fourteenth init in the file meets the per-minute
    window and a test fails for a reason that has nothing to do with it.
    """
    get_patient_document_init_limiter().reset()
    yield
    get_patient_document_init_limiter().reset()


@pytest.fixture
def patient_app(
    documents_service: PatientDocumentsService,
    audit_repo: InMemoryAuditRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """The patient router alone, with a patient front door and no database."""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(patient_documents.patient_router)

    @app.middleware("http")
    async def _stash_clinician_identity(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.headers.get("x-test-clinician-verified"):
            request.state.verified_identity = {"uid": _CLINICIAN}
        return await call_next(request)

    registry = PatientResolverRegistry()
    registry.register(_TwoPatientResolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    app.dependency_overrides[get_patient_documents_service_for_patient] = lambda: documents_service
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)

    monkeypatch.setattr(patient_context_module, "get_db_session", object)
    monkeypatch.setattr(patient_context_module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(patient_context_module, "arm_current_patient_id", lambda _s, _p: None)
    return app


@pytest.fixture
def portal(patient_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(patient_app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init(
    portal: TestClient,
    token: str = _TOKEN_A,
    *,
    filename: str = "insurance-card.pdf",
    mime_type: str = "application/pdf",
    size_bytes: int = 2048,
    category: str = "intake_artifact",
    extra: dict[str, Any] | None = None,
) -> Any:
    body: dict[str, Any] = {
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "category": category,
    }
    if extra:
        body.update(extra)
    return portal.post(f"{DOCUMENTS}/init", json=body, headers=_auth(token))


def _put_blob(
    fake_gcs: _FakeStorageClient,
    repo: InMemoryPatientDocumentRepository,
    document_id: str,
    *,
    data: bytes = _PDF,
    content_type: str = "application/pdf",
) -> None:
    """Put the object where the signed URL pointed, as a browser would."""
    document = repo._by_id[document_id]
    fake_gcs.bucket(_BUCKET).blob(document.gcs_path).upload_from_string(
        data, content_type=content_type
    )


def _upload(
    portal: TestClient,
    fake_gcs: _FakeStorageClient,
    repo: InMemoryPatientDocumentRepository,
    token: str = _TOKEN_A,
    *,
    category: str = "intake_artifact",
) -> str:
    """Init, put the object, finalize. Returns the document id."""
    document_id = str(_init(portal, token, category=category).json()["document_id"])
    _put_blob(fake_gcs, repo, document_id)
    response = portal.post(f"{DOCUMENTS}/{document_id}/finalize", headers=_auth(token))
    assert response.status_code == 200, response.text
    return document_id


def _seed_clinician_document(
    repo: InMemoryPatientDocumentRepository,
    *,
    patient_id: str,
    category: DocumentCategory,
) -> str:
    """A row a clinician uploaded, finalized, on ``patient_id``'s chart."""
    document_id = f"clinician-doc-{patient_id}-{category.value}"
    repo.add(
        PatientDocument(
            id=document_id,
            patient_id=patient_id,
            user_id=_CLINICIAN,
            filename="from-the-practice.pdf",
            mime_type="application/pdf",
            gcs_path=f"practice_test_documents/{category.value}/{document_id}",
            size_bytes=10,
            category=category,
            created_at=datetime.now(UTC),
            finalized_at=datetime.now(UTC),
        )
    )
    return document_id


def _entries(
    audit_repo: InMemoryAuditRepository,
    action: AuditAction,
    patient_id: str = _PATIENT_A,
) -> list[AuditLogEntry]:
    """Read back through the repository's own query, as a reviewer would.

    ``list_for_user`` is keyed on the ACTOR, and on this surface the actor
    is the patient — which is itself part of what the assertions check.
    """
    return [e for e in audit_repo.list_for_user(patient_id) if e.action == action.value]


def _all_entries(audit_repo: InMemoryAuditRepository) -> list[AuditLogEntry]:
    return audit_repo.list_for_user(_PATIENT_A) + audit_repo.list_for_user(_PATIENT_B)


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


class TestWhoMayCall:
    def test_no_credential_is_401(self, portal: TestClient) -> None:
        refusals = {
            ("post", f"{DOCUMENTS}/init"): portal.post(f"{DOCUMENTS}/init", json={}),
            ("post", f"{DOCUMENTS}/x/finalize"): portal.post(f"{DOCUMENTS}/x/finalize"),
            ("get", DOCUMENTS): portal.get(DOCUMENTS),
            ("get", f"{DOCUMENTS}/x/file"): portal.get(f"{DOCUMENTS}/x/file"),
        }
        for route, response in refusals.items():
            assert response.status_code == 401, f"{route}: {response.text}"

    def test_a_clinician_bearer_is_401(self, portal: TestClient) -> None:
        """A verified clinician token is refused before any resolver runs.

        Not 403: this surface answers the same way to every credential it
        does not accept, so nothing distinguishes "wrong principal" from
        "no principal".
        """
        response = portal.get(
            DOCUMENTS,
            headers={**_auth(_TOKEN_A), "x-test-clinician-verified": "1"},
        )
        assert response.status_code == 401, response.text

    def test_an_unknown_credential_is_401(self, portal: TestClient) -> None:
        response = portal.get(DOCUMENTS, headers=_auth("not-a-credential"))
        assert response.status_code == 401

    def test_single_factor_is_refused_everywhere(self, portal: TestClient) -> None:
        """Control first: the same calls succeed for the stepped-up token."""
        assert _init(portal, _TOKEN_A).status_code == 201
        assert portal.get(DOCUMENTS, headers=_auth(_TOKEN_A)).status_code == 200

        assert _init(portal, _TOKEN_A_WEAK).status_code == 403
        weak_list = portal.get(DOCUMENTS, headers=_auth(_TOKEN_A_WEAK))
        assert weak_list.status_code == 403
        assert weak_list.json()["error"]["code"] == "STEP_UP_REQUIRED"


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


class TestInit:
    def test_returns_an_upload_target(self, portal: TestClient) -> None:
        response = _init(portal)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["document_id"]
        assert body["upload"]["url"]
        assert body["max_bytes"] == _MAX_BYTES

    def test_the_row_takes_its_patient_from_the_principal(
        self, portal: TestClient, doc_repo: InMemoryPatientDocumentRepository
    ) -> None:
        """The body names patient B; both columns still say A.

        Not a filter that happened to run — there is no patient id in the
        route's inputs at all, so the extra keys are simply ignored by the
        request model and the ids come off the principal.
        """
        response = _init(
            portal,
            _TOKEN_A,
            extra={"patient_id": _PATIENT_B, "uploaded_by_patient_id": _PATIENT_B},
        )
        assert response.status_code == 201, response.text
        document = doc_repo._by_id[response.json()["document_id"]]
        assert document.patient_id == _PATIENT_A
        assert document.uploaded_by_patient_id == _PATIENT_A
        assert document.user_id is None
        assert document.uploaded_by == "patient"

    def test_a_disallowed_mime_type_is_422(self, portal: TestClient) -> None:
        response = _init(portal, mime_type="application/zip")
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "UNSUPPORTED_MIME_TYPE"

    def test_over_the_size_cap_is_400(self, portal: TestClient) -> None:
        response = _init(portal, size_bytes=_MAX_BYTES + 1)
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"

    @pytest.mark.parametrize(
        "category", ["chart", "consent", "therapist_private", "psychotherapy_notes"]
    )
    def test_a_category_this_surface_does_not_carry_is_422(
        self, portal: TestClient, category: str
    ) -> None:
        """Including ``chart``, which the clinician request model defaults to.

        Refused by the request model, so it never reaches the service —
        which is why the status is FastAPI's own 422 rather than one of
        ours.
        """
        assert _init(portal, category=category).status_code == 422

    def test_an_omitted_category_is_422(self, portal: TestClient) -> None:
        """No default here: a client that forgot must say so, not guess."""
        response = portal.post(
            f"{DOCUMENTS}/init",
            json={"filename": "x.pdf", "mime_type": "application/pdf", "size_bytes": 10},
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 422

    def test_it_is_rate_limited_per_patient(self, portal: TestClient) -> None:
        # Control: the window is 10 a minute, so ten succeed.
        for _ in range(10):
            assert _init(portal).status_code == 201
        assert _init(portal).status_code == 429
        # The budget is the caller's own: B is untouched by A spending theirs.
        assert _init(portal, _TOKEN_B).status_code == 201

    def test_it_writes_an_audit_entry_without_the_filename(
        self, portal: TestClient, audit_repo: InMemoryAuditRepository
    ) -> None:
        _init(portal, filename="my-diagnosis-letter.pdf")
        entries = _entries(audit_repo, AuditAction.PATIENT_DOCUMENT_UPLOAD_INITIATED)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.actor_type == ACTOR_TYPE_PATIENT
        assert entry.user_id == _PATIENT_A
        assert entry.patient_id == _PATIENT_A
        assert entry.resource_type == ResourceType.PATIENT_DOCUMENT.value
        assert entry.changes == {
            "category": "intake_artifact",
            "mime_type": "application/pdf",
            "size_bytes": 2048,
        }
        assert "my-diagnosis-letter" not in str(entry.changes)


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------


class TestFinalize:
    def test_it_puts_the_document_on_the_chart(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        document_id = str(_init(portal).json()["document_id"])
        # Control: an unfinalized upload is on nobody's list.
        assert portal.get(DOCUMENTS, headers=_auth(_TOKEN_A)).json()["total"] == 0

        _put_blob(fake_gcs, doc_repo, document_id)
        response = portal.post(f"{DOCUMENTS}/{document_id}/finalize", headers=_auth(_TOKEN_A))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == document_id
        assert body["finalized_at"] is not None
        # The size written is the object's, not the one the client claimed.
        assert body["size_bytes"] == len(_PDF)
        assert body["uploaded_by"] == "patient"

    def test_a_missing_object_is_400(self, portal: TestClient) -> None:
        document_id = str(_init(portal).json()["document_id"])
        response = portal.post(f"{DOCUMENTS}/{document_id}/finalize", headers=_auth(_TOKEN_A))
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "UPLOAD_NOT_COMPLETE"

    def test_an_oversized_object_is_400(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """The signed URL capped it; the stored blob is checked anyway."""
        document_id = str(_init(portal).json()["document_id"])
        _put_blob(fake_gcs, doc_repo, document_id, data=b"x" * (_MAX_BYTES + 1))
        response = portal.post(f"{DOCUMENTS}/{document_id}/finalize", headers=_auth(_TOKEN_A))
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"

    def test_an_object_of_the_wrong_type_is_422(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        document_id = str(_init(portal).json()["document_id"])
        _put_blob(fake_gcs, doc_repo, document_id, content_type="application/zip")
        response = portal.post(f"{DOCUMENTS}/{document_id}/finalize", headers=_auth(_TOKEN_A))
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "UNSUPPORTED_MIME_TYPE"

    def test_another_patients_document_is_404(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        document_id = str(_init(portal, _TOKEN_A).json()["document_id"])
        _put_blob(fake_gcs, doc_repo, document_id)
        response = portal.post(f"{DOCUMENTS}/{document_id}/finalize", headers=_auth(_TOKEN_B))
        assert response.status_code == 404, response.text
        # Control: A can still finish it, so the 404 was about B.
        assert (
            portal.post(f"{DOCUMENTS}/{document_id}/finalize", headers=_auth(_TOKEN_A)).status_code
            == 200
        )

    def test_it_is_idempotent(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        document_id = _upload(portal, fake_gcs, doc_repo)
        again = portal.post(f"{DOCUMENTS}/{document_id}/finalize", headers=_auth(_TOKEN_A))
        assert again.status_code == 200, again.text
        assert portal.get(DOCUMENTS, headers=_auth(_TOKEN_A)).json()["total"] == 1

    def test_it_writes_an_audit_entry_without_the_filename(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        document_id = str(_init(portal, filename="my-diagnosis-letter.pdf").json()["document_id"])
        _put_blob(fake_gcs, doc_repo, document_id)
        portal.post(f"{DOCUMENTS}/{document_id}/finalize", headers=_auth(_TOKEN_A))

        entries = _entries(audit_repo, AuditAction.PATIENT_DOCUMENT_UPLOADED)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.actor_type == ACTOR_TYPE_PATIENT
        assert entry.user_id == _PATIENT_A
        assert entry.resource_id == document_id
        assert entry.session_id == _SESSION_A
        assert entry.changes == {
            "category": "intake_artifact",
            "mime_type": "application/pdf",
            "size_bytes": len(_PDF),
        }
        assert "my-diagnosis-letter" not in str(entry.changes)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    def test_it_returns_only_the_callers_own(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        mine = _upload(portal, fake_gcs, doc_repo, _TOKEN_A)
        theirs = _upload(portal, fake_gcs, doc_repo, _TOKEN_B)

        body = portal.get(DOCUMENTS, headers=_auth(_TOKEN_A)).json()
        assert [d["id"] for d in body["data"]] == [mine]
        assert body["total"] == 1
        # Control: B's row exists and B can see it, so A's list is filtered
        # rather than the table being empty.
        assert [d["id"] for d in portal.get(DOCUMENTS, headers=_auth(_TOKEN_B)).json()["data"]] == [
            theirs
        ]

    @pytest.mark.parametrize(
        "category",
        [
            DocumentCategory.CHART,
            DocumentCategory.CONSENT,
            DocumentCategory.THERAPIST_PRIVATE,
            DocumentCategory.PSYCHOTHERAPY_NOTES,
        ],
    )
    def test_the_rest_of_the_chart_is_not_on_it(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        category: DocumentCategory,
    ) -> None:
        """Their own chart, their own patient id, and still not this surface.

        ``psychotherapy_notes`` is the one that matters most — patient
        right-of-access does not reach it at all — but ``chart`` and
        ``consent`` are here too, because they come out through a records
        request rather than through a portal list.
        """
        mine = _upload(portal, fake_gcs, doc_repo, _TOKEN_A)
        hidden = _seed_clinician_document(doc_repo, patient_id=_PATIENT_A, category=category)

        listed = [d["id"] for d in portal.get(DOCUMENTS, headers=_auth(_TOKEN_A)).json()["data"]]
        assert listed == [mine]
        assert hidden not in listed

    def test_a_clinician_upload_in_a_patient_facing_category_is_on_it(
        self,
        portal: TestClient,
        doc_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        """The filter is the category, not the uploader.

        A practice attaching a file to a message thread is something the
        patient is meant to receive, and it arrives the same way anything
        else on this surface does.
        """
        sent = _seed_clinician_document(
            doc_repo, patient_id=_PATIENT_A, category=DocumentCategory.MESSAGE
        )
        body = portal.get(DOCUMENTS, headers=_auth(_TOKEN_A)).json()
        assert [d["id"] for d in body["data"]] == [sent]
        assert body["data"][0]["uploaded_by"] == "clinician"

    def test_it_is_not_audited(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        """Reading your own record is not a disclosure — the settled model.

        Asserted rather than assumed, because the reviewed exemption in
        ``check_route_audit.py`` says this route does not audit and the two
        should not be able to drift apart quietly.
        """
        _upload(portal, fake_gcs, doc_repo)
        before = len(_all_entries(audit_repo))
        assert portal.get(DOCUMENTS, headers=_auth(_TOKEN_A)).status_code == 200
        assert len(_all_entries(audit_repo)) == before


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


class TestDownload:
    def test_it_returns_a_signed_url(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        document_id = _upload(portal, fake_gcs, doc_repo)
        response = portal.get(f"{DOCUMENTS}/{document_id}/file", headers=_auth(_TOKEN_A))
        assert response.status_code == 200, response.text
        assert response.json()["url"].startswith("https://")

    def test_another_patients_document_is_404(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        document_id = _upload(portal, fake_gcs, doc_repo, _TOKEN_A)
        # Control: A can fetch it, so the refusal below is about B.
        assert (
            portal.get(f"{DOCUMENTS}/{document_id}/file", headers=_auth(_TOKEN_A)).status_code
            == 200
        )
        response = portal.get(f"{DOCUMENTS}/{document_id}/file", headers=_auth(_TOKEN_B))
        assert response.status_code == 404, response.text

    def test_an_id_that_never_existed_answers_the_same_way(self, portal: TestClient) -> None:
        response = portal.get(f"{DOCUMENTS}/nothing-here/file", headers=_auth(_TOKEN_B))
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "category", [DocumentCategory.CHART, DocumentCategory.PSYCHOTHERAPY_NOTES]
    )
    def test_the_rest_of_the_chart_is_404_by_id(
        self,
        portal: TestClient,
        doc_repo: InMemoryPatientDocumentRepository,
        category: DocumentCategory,
    ) -> None:
        """Knowing the id changes nothing: the category decides."""
        hidden = _seed_clinician_document(doc_repo, patient_id=_PATIENT_A, category=category)
        response = portal.get(f"{DOCUMENTS}/{hidden}/file", headers=_auth(_TOKEN_A))
        assert response.status_code == 404, response.text

    def test_it_is_audited(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        """A minted URL works without a credential, so it goes on the record."""
        document_id = _upload(portal, fake_gcs, doc_repo)
        portal.get(f"{DOCUMENTS}/{document_id}/file", headers=_auth(_TOKEN_A))

        entries = _entries(audit_repo, AuditAction.PATIENT_DOCUMENT_DOWNLOADED)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.actor_type == ACTOR_TYPE_PATIENT
        assert entry.user_id == _PATIENT_A
        assert entry.resource_id == document_id
        assert entry.resource_type == ResourceType.PATIENT_DOCUMENT.value
        assert entry.changes == {"category": "intake_artifact", "disposition": "attachment"}

    def test_a_refused_fetch_writes_nothing(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        document_id = _upload(portal, fake_gcs, doc_repo, _TOKEN_A)
        before = len(_entries(audit_repo, AuditAction.PATIENT_DOCUMENT_DOWNLOADED))
        portal.get(f"{DOCUMENTS}/{document_id}/file", headers=_auth(_TOKEN_B))
        assert len(_entries(audit_repo, AuditAction.PATIENT_DOCUMENT_DOWNLOADED)) == before


class TestNoDeleteExists:
    """A file somebody sent their practice is the practice's record of it.

    Asserted on the router rather than by calling a URL, because "the route
    is not there" and "the route is there and refused me" both answer 404
    from the outside, and only one of them is the claim.
    """

    def test_the_patient_router_has_no_destructive_method(self) -> None:
        methods = {
            method
            for route in patient_documents.patient_router.routes
            for method in getattr(route, "methods", set())
        }
        assert methods == {"GET", "POST"}, methods

    def test_deleting_one_reaches_nothing(
        self,
        portal: TestClient,
        fake_gcs: _FakeStorageClient,
        doc_repo: InMemoryPatientDocumentRepository,
    ) -> None:
        document_id = _upload(portal, fake_gcs, doc_repo)
        response = portal.delete(f"{DOCUMENTS}/{document_id}", headers=_auth(_TOKEN_A))
        assert response.status_code == 404, response.text
        # Control: it is still there afterwards.
        assert portal.get(DOCUMENTS, headers=_auth(_TOKEN_A)).json()["total"] == 1
