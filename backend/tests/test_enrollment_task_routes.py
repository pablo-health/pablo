# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Answering a payer's task from inside Pablo, over ``/api/payers``.

* ``GET  .../enrollments/{transaction}/tasks`` — the form to fill in, read
  live because a task's fields only exist on the clearinghouse's copy.
* ``POST .../enrollments/{transaction}/tasks/{id}`` — the answer, multipart,
  typed values alongside PDFs.
* ``GET  .../enrollments/{transaction}/documents/{id}`` — where to fetch a
  PDF from, and only for a document that is on this practice's enrollment.

Runs the payer router over an in-memory SQLite session against the shared
enrollment fake, which takes documents through the vendor's two steps and
refuses a completion naming one whose bytes never arrived.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.auth.service import (
    TenantContext,
    get_tenant_context,
    require_active_subscription,
)
from app.claims import enrollment
from app.db import get_db_session
from app.db.models import (
    ComplianceItemRow,
    PayerEnrollmentRow,
    PayerRow,
    PracticeBillingProfileRow,
)
from app.models import User
from app.repositories import get_payer_repository
from app.repositories.postgres.coverage import PostgresPayerRepository
from app.routes import coverage as coverage_routes
from app.services.coverage_intake import new_payer
from app.services.practice_billing_profile import update_billing_profile
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.enrollment_fakes import ENROLLMENTS_BASE, TEST_PAYER_ID, FakeClearinghouse
from tests.sqlite_engine import sqlite_engine

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine

_USER_ID = "11111111-1111-4111-8111-111111111111"
_SIGNED_FORM_TASK = "01a0a1b2-3c4d-7e5f-8a6b-7c8d9e0f1a2b"
_A_PDF = b"%PDF-1.7\nsigned\n%%EOF\n"

_PROFILE = {
    "legal_name": "Pablo Health Test Provider",
    "tax_id": "84-4459714",
    "tax_id_type": "ein",
    "billing_npi": "1999999984",
    "address_line1": "1 Test St",
    "city": "Atlanta",
    "state": "GA",
    "postal_code": "30301",
    "phone": "4045550100",
    "contact_email": "billing@example.com",
}


def _user() -> User:
    return User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=datetime.now(UTC),
        baa_accepted_at=datetime.now(UTC),
        baa_version="2024-01-01",
    )


def _tenant() -> TenantContext:
    return TenantContext(user_id=_USER_ID, practice_id="practice-1", practice_schema="practice_x")


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _no_arm(_session: Session, _user_id: str) -> None:
    """The RLS arm, on a database that has no GUC to arm."""


@pytest.fixture
def engine() -> Iterator[Engine]:
    with sqlite_engine(
        [
            PracticeBillingProfileRow.__table__,
            PayerRow.__table__,
            PayerEnrollmentRow.__table__,
            ComplianceItemRow.__table__,
        ]
    ) as eng:
        yield eng


@pytest.fixture
def harness(engine: Engine) -> Iterator[dict[str, Any]]:
    """A payer with an 835 enrollment on file, waiting on a signed form."""
    clearinghouse = FakeClearinghouse()
    enrollment.register_clearinghouse_client_factory(lambda _practice_id: clearinghouse)
    session = Session(engine)
    payers = PostgresPayerRepository(session)
    payer = payers.create(new_payer(name="Stedi Test Payer", payer_id=TEST_PAYER_ID))
    update_billing_profile(session, dict(_PROFILE))
    session.flush()

    app = FastAPI()
    app.include_router(coverage_routes.payers_router)
    app.dependency_overrides[require_active_subscription] = _user
    app.dependency_overrides[get_tenant_context] = _tenant
    app.dependency_overrides[get_payer_repository] = lambda: payers
    app.dependency_overrides[get_db_session] = lambda: session
    # SQLite has no ``set_config``, so the RLS arm is a no-op here; what it
    # guards is exercised in the Postgres suite.
    app.dependency_overrides[coverage_routes.get_principal_armer] = lambda: _no_arm
    client = TestClient(app)
    client.post(f"/api/payers/{payer.id}/enrollments")
    clearinghouse.wants_a_signed_form("enr-0001")

    try:
        yield {
            "client": client,
            "session": session,
            "payer": payer,
            "clearinghouse": clearinghouse,
            "tasks": f"/api/payers/{payer.id}/enrollments/835/tasks",
            "documents": f"/api/payers/{payer.id}/enrollments/835/documents",
            "links": f"/api/payers/{payer.id}/enrollments/835/tasks/{_SIGNED_FORM_TASK}/links",
        }
    finally:
        session.close()
        enrollment.register_clearinghouse_client_factory(None)


def _answer(
    client: TestClient, url: str, *, values: dict[str, str] | None = None, pdf: bytes | None = None
) -> Any:
    data: dict[str, Any] = {"values": json.dumps(values or {})}
    files = []
    if pdf is not None:
        data["document_fields"] = ["signed_eft_form"]
        files.append(("documents", ("eft.pdf", pdf, "application/pdf")))
    return client.post(url, data=data, files=files or None)


class TestReadingTheTasks:
    def test_answers_with_the_form_the_payer_wants(self, harness: dict[str, Any]) -> None:
        response = harness["client"].get(harness["tasks"])

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "provider_action_required"
        [task] = body["data"]
        assert task["id"] == _SIGNED_FORM_TASK
        assert task["instructions"].startswith("Sign the EFT authorization")
        assert task["fields"] == [
            {
                "key": "signed_eft_form",
                "label": "Signed EFT authorization",
                "field_type": "DOCUMENT",
                "description": None,
            }
        ]
        assert task["links"][0]["url"].endswith("eft-authorization.pdf")

    def test_leaves_out_what_the_clearinghouse_owes(self, harness: dict[str, Any]) -> None:
        """The second task on the fixture is the vendor's, not the practice's."""
        response = harness["client"].get(harness["tasks"])

        assert [task["id"] for task in response.json()["data"]] == [_SIGNED_FORM_TASK]

    def test_reading_records_the_status_the_vendor_now_reports(
        self, harness: dict[str, Any]
    ) -> None:
        harness["client"].get(harness["tasks"])

        row = harness["session"].query(PayerEnrollmentRow).one()
        assert row.status == "provider_action_required"
        assert row.instructions is not None

    def test_unfiled_transaction_is_404(self, harness: dict[str, Any]) -> None:
        payer = harness["payer"]

        response = harness["client"].get(f"/api/payers/{payer.id}/enrollments/270/tasks")

        assert response.status_code == 404

    def test_no_clearinghouse_is_503(self, harness: dict[str, Any]) -> None:
        enrollment.register_clearinghouse_client_factory(lambda _practice_id: None)

        response = harness["client"].get(harness["tasks"])

        assert response.status_code == 503


class TestAnsweringATask:
    def test_uploads_the_pdf_then_completes(self, harness: dict[str, Any]) -> None:
        response = _answer(harness["client"], f"{harness['tasks']}/{_SIGNED_FORM_TASK}", pdf=_A_PDF)

        assert response.status_code == 200, response.text
        assert response.json()["data"] == []
        assert response.json()["status"] == "provisioning"
        assert [
            name
            for name, _ in harness["clearinghouse"].calls
            if name in {"upload_enrollment_document", "put_document", "complete_enrollment_task"}
        ] == ["upload_enrollment_document", "put_document", "complete_enrollment_task"]

    def test_the_document_comes_back_on_the_enrollment(self, harness: dict[str, Any]) -> None:
        response = _answer(harness["client"], f"{harness['tasks']}/{_SIGNED_FORM_TASK}", pdf=_A_PDF)

        [document] = response.json()["documents"]
        assert document["name"] == "eft.pdf"
        assert document["status"] == "UPLOADED"

    def test_answering_takes_the_ask_off_the_row(self, harness: dict[str, Any]) -> None:
        """The row stops asking for the form. The payer's note about why stays."""
        _answer(harness["client"], f"{harness['tasks']}/{_SIGNED_FORM_TASK}", pdf=_A_PDF)

        row = harness["session"].query(PayerEnrollmentRow).one()
        assert row.status == "provisioning"
        assert "Sign the EFT authorization" not in (row.instructions or "")

    def test_no_pdf_is_422_naming_the_field(self, harness: dict[str, Any]) -> None:
        response = _answer(harness["client"], f"{harness['tasks']}/{_SIGNED_FORM_TASK}")

        assert response.status_code == 422
        assert "signed_eft_form" in response.json()["detail"]
        assert harness["clearinghouse"].calls_named("upload_enrollment_document") == []

    def test_something_that_is_not_a_pdf_never_leaves_the_building(
        self, harness: dict[str, Any]
    ) -> None:
        response = _answer(
            harness["client"], f"{harness['tasks']}/{_SIGNED_FORM_TASK}", pdf=b"GIF89a not a pdf"
        )

        assert response.status_code == 422
        assert harness["clearinghouse"].calls_named("upload_enrollment_document") == []

    def test_an_oversized_document_is_413(self, harness: dict[str, Any]) -> None:
        too_big = _A_PDF + b"x" * coverage_routes.MAX_ENROLLMENT_DOCUMENT_BYTES

        response = _answer(
            harness["client"], f"{harness['tasks']}/{_SIGNED_FORM_TASK}", pdf=too_big
        )

        assert response.status_code == 413
        assert harness["clearinghouse"].calls_named("upload_enrollment_document") == []

    def test_a_file_with_no_field_to_answer_is_422(self, harness: dict[str, Any]) -> None:
        response = harness["client"].post(
            f"{harness['tasks']}/{_SIGNED_FORM_TASK}",
            data={"values": "{}"},
            files=[("documents", ("eft.pdf", _A_PDF, "application/pdf"))],
        )

        assert response.status_code == 422

    def test_unreadable_typed_answers_are_422(self, harness: dict[str, Any]) -> None:
        response = harness["client"].post(
            f"{harness['tasks']}/{_SIGNED_FORM_TASK}", data={"values": "not json"}
        )

        assert response.status_code == 422

    def test_a_task_that_is_not_ours_is_404(self, harness: dict[str, Any]) -> None:
        """The fixture's second task belongs to the vendor."""
        response = _answer(
            harness["client"],
            f"{harness['tasks']}/01a0a1b2-3c4d-7e5f-8a6b-7c8d9e0f1a2c",
            pdf=_A_PDF,
        )

        assert response.status_code == 404

    def test_answering_twice_is_404_rather_than_a_second_completion(
        self, harness: dict[str, Any]
    ) -> None:
        url = f"{harness['tasks']}/{_SIGNED_FORM_TASK}"
        _answer(harness["client"], url, pdf=_A_PDF)

        response = _answer(harness["client"], url, pdf=_A_PDF)

        assert response.status_code == 404
        assert len(harness["clearinghouse"].calls_named("complete_enrollment_task")) == 1


class TestOpeningATaskLink:
    """The task's link is the payer's blank form — sometimes ours to fetch."""

    def _link(self, harness: dict[str, Any], url: str) -> None:
        """Re-point the fixture task's one link at ``url``."""
        record = harness["clearinghouse"].enrollments["enr-0001"]
        record["tasks"][0]["definition"]["manualTask"]["links"][0]["url"] = url

    def test_a_clearinghouse_link_is_marked_for_resolving(self, harness: dict[str, Any]) -> None:
        self._link(harness, f"{ENROLLMENTS_BASE}/documents/tmpl-1")

        response = harness["client"].get(harness["tasks"])

        [link] = response.json()["data"][0]["links"]
        assert link["resolvable"] is True

    def test_a_link_on_the_open_web_is_not(self, harness: dict[str, Any]) -> None:
        response = harness["client"].get(harness["tasks"])

        [link] = response.json()["data"][0]["links"]
        assert link["url"].startswith("https://example.com/")
        assert link["resolvable"] is False

    def test_resolving_hands_back_the_short_lived_url(self, harness: dict[str, Any]) -> None:
        self._link(harness, f"{ENROLLMENTS_BASE}/documents/tmpl-1")

        response = harness["client"].get(f"{harness['links']}/0")

        assert response.status_code == 200, response.text
        assert response.json()["url"] == "https://downloads.test/tmpl-1"

    def test_a_link_on_the_open_web_is_not_resolved(self, harness: dict[str, Any]) -> None:
        """The fixture's link is a payer's own website. The browser has it already."""
        response = harness["client"].get(f"{harness['links']}/0")

        assert response.status_code == 404
        assert harness["clearinghouse"].calls_named("resolve_enrollment_link") == []

    def test_an_index_the_task_does_not_have_is_404(self, harness: dict[str, Any]) -> None:
        self._link(harness, f"{ENROLLMENTS_BASE}/documents/tmpl-1")

        response = harness["client"].get(f"{harness['links']}/7")

        assert response.status_code == 404
        assert harness["clearinghouse"].calls_named("resolve_enrollment_link") == []


class TestFetchingADocument:
    def test_hands_back_the_vendors_url(self, harness: dict[str, Any]) -> None:
        _answer(harness["client"], f"{harness['tasks']}/{_SIGNED_FORM_TASK}", pdf=_A_PDF)

        response = harness["client"].get(f"{harness['documents']}/doc-0001")

        assert response.status_code == 200, response.text
        assert response.json()["url"].startswith("https://downloads.test/doc-0001")

    def test_a_document_that_is_not_on_this_enrollment_is_404(
        self, harness: dict[str, Any]
    ) -> None:
        response = harness["client"].get(f"{harness['documents']}/doc-somebody-elses")

        assert response.status_code == 404
        assert harness["clearinghouse"].calls_named("download_enrollment_document") == []
