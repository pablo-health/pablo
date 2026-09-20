# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for the practice's own empty paperwork.

Two surfaces on one table: the practice uploads a blank form, and a patient
filling in a question that offers one downloads it.

The test that matters most is the last one. A blank form lives in its own
table precisely so the portal's download route cannot be pointed at
somebody's chart — not because a filter was remembered, but because the
rows are not there to find. ``TestItCannotReachAChart`` is what makes that
a fact rather than an intention.
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
from app.auth.service import require_baa_acceptance
from app.models import User
from app.models.audit import ACTOR_TYPE_PATIENT, AuditAction
from app.repositories import InMemoryIntakeBlankFormRepository
from app.repositories.audit import InMemoryAuditRepository
from app.routes import intake_blank_forms
from app.routes.intake_blank_forms import (
    get_intake_blank_form_service,
    get_patient_intake_blank_form_service,
)
from app.services.audit_service import AuditService, get_audit_service
from app.services.file_storage import FileStorageProvider, UploadTarget
from app.services.intake_blank_form_service import IntakeBlankFormService
from app.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

_PATIENT_A = "11111111-1111-4111-8111-111111111111"
_TOKEN_A = "credential-of-patient-a"
_TOKEN_A_WEAK = "single-factor-credential-of-patient-a"
_CLINICIAN = "clinician-1"

FORMS = "/api/intake/blank-forms"
PORTAL_FORMS = "/api/patient/intake/blank-forms"
_BUCKET = "pablo-docs-test"
_MAX_BYTES = 25 * 1024 * 1024


class _FakeStorage(FileStorageProvider):
    """Enough of a storage backend to prove the two-phase upload.

    Objects that were never written are simply absent, which is what makes
    "finalize before the browser finished" a real case rather than a mocked
    one.
    """

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, object_name: str, data: bytes = b"%PDF-1.7 blank", ct: str = "application/pdf"):  # type: ignore[no-untyped-def]
        self.objects[object_name] = (data, ct)

    def make_upload_target(
        self, *, bucket: str, object_name: str, content_type: str, max_bytes: int, ttl_seconds: int
    ) -> UploadTarget:
        return UploadTarget(url=f"https://storage.example/{object_name}", method="PUT")

    def make_download_url(
        self,
        *,
        bucket: str,
        object_name: str,
        ttl_seconds: int,
        response_disposition: str | None = None,
    ) -> str:
        return f"https://storage.example/{object_name}?sig=abc"

    def fetch_metadata(self, *, bucket: str, object_name: str) -> tuple[int, str | None] | None:
        found = self.objects.get(object_name)
        return (len(found[0]), found[1]) if found else None

    def download_bytes(self, *, bucket: str, object_name: str) -> bytes:
        return self.objects[object_name][0]

    def upload_bytes(
        self, *, bucket: str, object_name: str, data: bytes, content_type: str
    ) -> None:
        self.objects[object_name] = (data, content_type)

    def upload_stream(self, **kwargs: Any) -> None:  # pragma: no cover — unused here
        raise NotImplementedError

    def delete(self, *, bucket: str, object_name: str) -> None:
        self.objects.pop(object_name, None)

    def list_names(self, *, bucket: str, prefix: str) -> list[str]:
        return [name for name in self.objects if name.startswith(prefix)]


class _Resolver:
    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        principals = {
            _TOKEN_A: AuthStrength.STEPPED_UP,
            _TOKEN_A_WEAK: AuthStrength.SINGLE_FACTOR,
        }
        strength = principals.get(credential.value)
        if strength is None:
            return None
        return PatientContext(
            patient_id=_PATIENT_A,
            practice_schema="practice_test_intake",
            credential_kind="bearer",
            auth_strength=strength,
            session_id="session-handle-a",
        )


def _auth(token: str = _TOKEN_A) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def storage() -> _FakeStorage:
    return _FakeStorage()


@pytest.fixture
def forms_repo() -> InMemoryIntakeBlankFormRepository:
    return InMemoryIntakeBlankFormRepository()


@pytest.fixture
def blank_form_settings() -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        patient_documents_gcs_bucket=_BUCKET,
        patient_documents_max_bytes=_MAX_BYTES,
        patient_documents_upload_url_ttl_seconds=300,
        patient_documents_download_url_ttl_seconds=300,
    )


@pytest.fixture
def service(
    forms_repo: InMemoryIntakeBlankFormRepository,
    blank_form_settings: Settings,
    storage: _FakeStorage,
) -> IntakeBlankFormService:
    return IntakeBlankFormService(
        repo=forms_repo,
        settings=blank_form_settings,
        storage=storage,
        tenant_id="practice_test_intake",
    )


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def app(
    service: IntakeBlankFormService,
    audit_repo: InMemoryAuditRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """Both routers, with the clinician door stubbed and no database."""
    built = FastAPI()
    register_exception_handlers(built)
    built.include_router(intake_blank_forms.router)
    built.include_router(intake_blank_forms.patient_router)

    registry = PatientResolverRegistry()
    registry.register(_Resolver())
    built.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    built.dependency_overrides[get_intake_blank_form_service] = lambda: service
    built.dependency_overrides[get_patient_intake_blank_form_service] = lambda: service
    built.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)
    built.dependency_overrides[require_baa_acceptance] = lambda: User(
        id=_CLINICIAN,
        email="clinician@example.com",
        name="A Clinician",
        created_at=datetime.now(UTC),
    )

    monkeypatch.setattr(patient_context_module, "get_db_session", object)
    monkeypatch.setattr(patient_context_module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(patient_context_module, "arm_current_patient_id", lambda _s, _p: None)
    return built


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _init(client: TestClient, *, mime_type: str = "application/pdf", size_bytes: int = 2048):  # type: ignore[no-untyped-def]
    return client.post(
        f"{FORMS}/init",
        json={
            "title": "Release of records",
            "filename": "roi.pdf",
            "mime_type": mime_type,
            "size_bytes": size_bytes,
        },
    )


def _uploaded(
    client: TestClient,
    forms_repo: InMemoryIntakeBlankFormRepository,
    storage: _FakeStorage,
) -> str:
    """One form all the way through the two-phase upload."""
    form_id = str(_init(client).json()["form_id"])
    storage.put(str(forms_repo.rows[form_id]["gcs_path"]))
    client.post(f"{FORMS}/{form_id}/finalize")
    return form_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestThePracticeUploadsOne:
    def test_the_two_phases_land_a_form(
        self,
        client: TestClient,
        forms_repo: InMemoryIntakeBlankFormRepository,
        storage: _FakeStorage,
    ) -> None:
        started = _init(client)
        assert started.status_code == 201
        form_id = str(started.json()["form_id"])

        # Control: an unfinished upload is on nobody's list.
        assert client.get(FORMS).json() == []

        storage.put(str(forms_repo.rows[form_id]["gcs_path"]))
        finished = client.post(f"{FORMS}/{form_id}/finalize")

        assert finished.status_code == 200
        assert finished.json()["title"] == "Release of records"
        # The size written is the object's, not the one the client claimed.
        assert finished.json()["size_bytes"] == len(b"%PDF-1.7 blank")
        assert [row["id"] for row in client.get(FORMS).json()] == [form_id]

    def test_finalizing_before_the_browser_finished_is_400(self, client: TestClient) -> None:
        form_id = str(_init(client).json()["form_id"])
        response = client.post(f"{FORMS}/{form_id}/finalize")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "UPLOAD_NOT_COMPLETE"

    def test_a_type_the_patients_own_browsers_would_be_refused_is_422(
        self, client: TestClient
    ) -> None:
        """A practice cannot ask for a file nobody could send back."""
        response = _init(client, mime_type="application/zip")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "UNSUPPORTED_MIME_TYPE"

    def test_an_oversized_form_is_400(self, client: TestClient) -> None:
        response = _init(client, size_bytes=_MAX_BYTES + 1)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"

    def test_finalizing_twice_is_the_same_form(
        self,
        client: TestClient,
        forms_repo: InMemoryIntakeBlankFormRepository,
        storage: _FakeStorage,
    ) -> None:
        """A retry after a dropped connection is not a second upload."""
        form_id = _uploaded(client, forms_repo, storage)
        again = client.post(f"{FORMS}/{form_id}/finalize")
        assert again.status_code == 200
        assert again.json()["id"] == form_id
        assert len(client.get(FORMS).json()) == 1

    def test_removing_one_stops_it_being_offered(
        self,
        client: TestClient,
        forms_repo: InMemoryIntakeBlankFormRepository,
        storage: _FakeStorage,
    ) -> None:
        """Never a hard delete: a published question may still name it."""
        form_id = _uploaded(client, forms_repo, storage)
        assert client.delete(f"{FORMS}/{form_id}").status_code == 200
        assert client.get(FORMS).json() == []
        assert client.get(f"{FORMS}/{form_id}/file").status_code == 404

    def test_removing_one_twice_is_404(
        self,
        client: TestClient,
        forms_repo: InMemoryIntakeBlankFormRepository,
        storage: _FakeStorage,
    ) -> None:
        form_id = _uploaded(client, forms_repo, storage)
        client.delete(f"{FORMS}/{form_id}")
        assert client.delete(f"{FORMS}/{form_id}").status_code == 404


class TestThePatientDownloadsOne:
    def test_a_stepped_up_patient_gets_a_url(
        self,
        client: TestClient,
        forms_repo: InMemoryIntakeBlankFormRepository,
        storage: _FakeStorage,
    ) -> None:
        form_id = _uploaded(client, forms_repo, storage)
        response = client.get(f"{PORTAL_FORMS}/{form_id}/file", headers=_auth())
        assert response.status_code == 200
        assert response.json()["url"].startswith("https://storage.example/")

    def test_a_single_factor_session_is_403(
        self,
        client: TestClient,
        forms_repo: InMemoryIntakeBlankFormRepository,
        storage: _FakeStorage,
    ) -> None:
        form_id = _uploaded(client, forms_repo, storage)
        response = client.get(f"{PORTAL_FORMS}/{form_id}/file", headers=_auth(_TOKEN_A_WEAK))
        assert response.status_code == 403

    def test_no_credential_is_401(
        self,
        client: TestClient,
        forms_repo: InMemoryIntakeBlankFormRepository,
        storage: _FakeStorage,
    ) -> None:
        form_id = _uploaded(client, forms_repo, storage)
        assert client.get(f"{PORTAL_FORMS}/{form_id}/file").status_code == 401

    def test_an_unfinished_upload_is_404(self, client: TestClient) -> None:
        """A row exists and there is no file behind it; it is not offerable."""
        form_id = str(_init(client).json()["form_id"])
        assert client.get(f"{PORTAL_FORMS}/{form_id}/file", headers=_auth()).status_code == 404

    def test_minting_the_url_is_recorded(
        self,
        client: TestClient,
        forms_repo: InMemoryIntakeBlankFormRepository,
        storage: _FakeStorage,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        """A signed URL leaves the request and works without a bearer token.

        The practice's own download beside it is not recorded, and that is
        the difference: this one hands a fetchable link to somebody outside
        the practice.
        """
        form_id = _uploaded(client, forms_repo, storage)
        client.get(f"{PORTAL_FORMS}/{form_id}/file", headers=_auth())

        entry = next(
            row
            for row in audit_repo.list_for_user(_PATIENT_A)
            if row.action == AuditAction.PATIENT_DOCUMENT_DOWNLOADED.value
        )
        assert entry.actor_type == ACTOR_TYPE_PATIENT
        assert entry.resource_id == form_id
        assert entry.changes == {"kind": "blank_form", "disposition": "attachment"}


class TestItCannotReachAChart:
    def test_an_id_that_is_not_a_blank_form_is_404(
        self, client: TestClient, storage: _FakeStorage
    ) -> None:
        """The reason for the separate table, asserted directly.

        The id below is a perfectly real object in the same bucket, under
        the layout a patient document uses. The portal route still answers
        404, because it reads the blank-form table and a chart document is
        not in it — there is no filter here to forget.
        """
        chart_document_id = "99999999-9999-4999-8999-999999999999"
        storage.put(f"practice_test_intake/chart/{chart_document_id}")

        response = client.get(f"{PORTAL_FORMS}/{chart_document_id}/file", headers=_auth())

        assert response.status_code == 404

    def test_and_an_id_that_never_existed_answers_the_same(self, client: TestClient) -> None:
        """Indistinguishable, so nothing here says what exists."""
        response = client.get(
            f"{PORTAL_FORMS}/88888888-8888-4888-8888-888888888888/file", headers=_auth()
        )
        assert response.status_code == 404
