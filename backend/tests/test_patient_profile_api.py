# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP tests for the patient's own demographics.

The real handlers on a fresh app, over the in-memory patient repository —
which projects field by field exactly as the Postgres one does, so a test
written against it is a test of what production returns.

What these are FOR:

* **The response is the pinned field set and nothing else.** A staff-authored
  column reaching this body is the failure the whole ``patient_facing``
  control exists to prevent, and a serializer that leaked by default would
  satisfy every registry assertion while doing it.
* **Identity is read-only.** A request naming ``first_name`` is refused
  rather than accepted and ignored — a form that silently drops a
  correction is worse than one that says no.
* **The write needs the second factor**, because changing the email address
  changes where the next sign-in link goes.
* **Two patients cannot reach each other**, which here is structural: there
  is no id in the request to point anywhere.
* **The audit payload carries field names and hashes, never values.**
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.api_errors import register_exception_handlers
from app.auth.patient_context import (
    AuthStrength,
    PatientContext,
    PatientCredential,
    PatientResolverRegistry,
    get_patient_resolver_registry,
)
from app.auth.patient_context import (
    __name__ as patient_context_name,
)
from app.models import Patient
from app.models.audit import AuditAction
from app.models.patient_facing import PATIENT_COLUMN_DECISIONS, shown_columns, withheld_columns
from app.repositories import InMemoryPatientRepository, get_patient_repository
from app.routes.patient_profile import router
from app.services.audit_service import get_audit_service
from app.settings import get_settings
from app.utcnow import utc_now
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

TENANT = "practice_abc123"
SIGNING_KEY = "profile-route-test-key-not-a-real-secret"
PROFILE = "/api/patient/profile"

PATIENT_A = "11111111-1111-4111-8111-111111111111"
PATIENT_B = "22222222-2222-4222-8222-222222222222"

TOKEN_A = "credential-of-patient-a"
TOKEN_A_WEAK = "single-factor-credential-of-patient-a"
TOKEN_B = "credential-of-patient-b"

# Values seeded onto the chart that a patient must never be shown. Asserted
# against the whole response text, not just its keys.
DIAGNOSIS = "F41.1 generalized anxiety disorder"
SLIDING_SCALE_NOTE = "Agreed 60 a session while between jobs"

_PRINCIPALS: dict[str, tuple[str, AuthStrength]] = {
    TOKEN_A: (PATIENT_A, AuthStrength.STEPPED_UP),
    TOKEN_A_WEAK: (PATIENT_A, AuthStrength.SINGLE_FACTOR),
    TOKEN_B: (PATIENT_B, AuthStrength.STEPPED_UP),
}


class _StubResolver:
    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        found = _PRINCIPALS.get(credential.value)
        if found is None:
            return None
        patient_id, strength = found
        return PatientContext(
            patient_id=patient_id,
            practice_schema=TENANT,
            credential_kind="portal_session",
            auth_strength=strength,
            session_id="session-handle",
        )


class _RecordingAudit:
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.changes: list[dict[str, Any]] = []

    def log_patient_principal_action(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.actions.append(str(action))
        self.changes.append(kwargs.get("changes") or {})

    def changes_for(self, action: AuditAction) -> list[dict[str, Any]]:
        return [
            payload
            for name, payload in zip(self.actions, self.changes, strict=True)
            if action.value in name
        ]


def _chart(patient_id: str, **overrides: Any) -> Patient:
    now = utc_now()
    fields: dict[str, Any] = {
        "id": patient_id,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "created_at": now,
        "updated_at": now,
        "email": "ada@example.test",
        "phone": "+15005550006",
        "date_of_birth": "1815-12-10",
        "address_line1": "12 Marylebone Road",
        "city": "London",
        "state": "NY",
        "postal_code": "10001",
        "diagnosis": DIAGNOSIS,
        "sliding_scale_note": SLIDING_SCALE_NOTE,
        "rate_cents": 9000,
        "origin": "voice",
        "chart_closure_reason": "Moved out of state",
        "sex": "F",
    }
    fields.update(overrides)
    return Patient(**fields)


@pytest.fixture(autouse=True)
def _portal_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    monkeypatch.setenv("PORTAL_TOKEN_SIGNING_KEY", SIGNING_KEY)
    yield
    get_settings.cache_clear()


@pytest.fixture
def patients() -> InMemoryPatientRepository:
    repo = InMemoryPatientRepository()
    repo._patients[PATIENT_A] = _chart(PATIENT_A)
    repo._patients[PATIENT_B] = _chart(
        PATIENT_B, first_name="Grace", last_name="Hopper", email="grace@example.test"
    )
    return repo


@pytest.fixture
def audit() -> _RecordingAudit:
    return _RecordingAudit()


@pytest.fixture
def app(
    patients: InMemoryPatientRepository,
    audit: _RecordingAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(router)

    registry = PatientResolverRegistry()
    registry.register(_StubResolver())
    application.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    application.dependency_overrides[get_patient_repository] = lambda: patients
    application.dependency_overrides[get_audit_service] = lambda: audit

    import sys  # noqa: PLC0415

    module = sys.modules[patient_context_name]
    monkeypatch.setattr(module, "get_db_session", object)
    monkeypatch.setattr(module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(module, "arm_current_patient_id", lambda _s, _p: None)
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestReadingTheProfile:
    def test_it_returns_exactly_the_pinned_field_set(self, client: TestClient) -> None:
        """Key-set equality, read from the decisions rather than listed again.

        A list copied into a test agrees with the copy, not with the table.
        """
        body = client.get(PROFILE, headers=_auth(TOKEN_A)).json()

        assert set(body) == shown_columns(PATIENT_COLUMN_DECISIONS)

    def test_the_field_set_is_what_was_agreed(self, client: TestClient) -> None:
        """And named once, here, so the derived check above cannot drift.

        Both sides of a derivation moving together is exactly what a mistake
        would look like.
        """
        body = client.get(PROFILE, headers=_auth(TOKEN_A)).json()

        assert set(body) == {
            "first_name",
            "last_name",
            "preferred_name",
            "date_of_birth",
            "email",
            "phone",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
        }

    def test_no_withheld_column_appears_as_a_key(self, client: TestClient) -> None:
        body = client.get(PROFILE, headers=_auth(TOKEN_A)).json()

        for withheld in withheld_columns(PATIENT_COLUMN_DECISIONS):
            assert withheld not in body, f"{withheld} reached a patient-facing response"

    def test_no_staff_authored_value_appears_anywhere_in_the_body(self, client: TestClient) -> None:
        """Values, not just keys.

        A serializer that renamed a field while still carrying it would pass
        the key checks above and fail here.
        """
        text = client.get(PROFILE, headers=_auth(TOKEN_A)).text

        for secret in (DIAGNOSIS, SLIDING_SCALE_NOTE, "9000", "voice", "Moved out of state"):
            assert secret not in text, f"{secret!r} reached a patient-facing response"

    def test_it_returns_the_callers_own_chart(self, client: TestClient) -> None:
        body = client.get(PROFILE, headers=_auth(TOKEN_B)).json()

        assert body["first_name"] == "Grace"
        assert body["email"] == "grace@example.test"

    def test_a_single_factor_principal_is_refused(self, client: TestClient) -> None:
        response = client.get(PROFILE, headers=_auth(TOKEN_A_WEAK))

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "STEP_UP_REQUIRED"

    def test_no_credential_is_401(self, client: TestClient) -> None:
        assert client.get(PROFILE).status_code == 401

    def test_a_deleted_chart_is_404(
        self, client: TestClient, patients: InMemoryPatientRepository
    ) -> None:
        patients._deleted_at[PATIENT_A] = utc_now()

        assert client.get(PROFILE, headers=_auth(TOKEN_A)).status_code == 404

    def test_a_write_to_a_deleted_chart_is_404(
        self, client: TestClient, patients: InMemoryPatientRepository
    ) -> None:
        patients._deleted_at[PATIENT_A] = utc_now()

        response = client.patch(PROFILE, headers=_auth(TOKEN_A), json={"city": "Brooklyn"})

        assert response.status_code == 404

    def test_the_read_is_audited(self, client: TestClient, audit: _RecordingAudit) -> None:
        """Demographics are the chart, so opening them is a read of the record."""
        client.get(PROFILE, headers=_auth(TOKEN_A))

        assert AuditAction.PATIENT_VIEWED.value in " ".join(audit.actions)


class TestWritingContactDetails:
    def test_it_updates_the_named_fields(self, client: TestClient) -> None:
        response = client.patch(
            PROFILE, headers=_auth(TOKEN_A), json={"city": "Brooklyn", "postal_code": "11201"}
        )

        assert response.status_code == 200
        assert response.json()["city"] == "Brooklyn"
        assert response.json()["postal_code"] == "11201"

    def test_it_leaves_the_fields_it_was_not_given_alone(self, client: TestClient) -> None:
        """So a screen that edits one line does not blank the rest of the form."""
        client.patch(PROFILE, headers=_auth(TOKEN_A), json={"city": "Brooklyn"})

        body = client.get(PROFILE, headers=_auth(TOKEN_A)).json()
        assert body["address_line1"] == "12 Marylebone Road"
        assert body["email"] == "ada@example.test"

    def test_an_explicit_null_clears_the_field(self, client: TestClient) -> None:
        """``None`` means "set it to empty", not "leave it alone".

        An address line a patient clears should clear; the two are told
        apart by which fields the request named at all.
        """
        body = client.patch(
            PROFILE, headers=_auth(TOKEN_A), json={"address_line2": None, "city": None}
        ).json()

        assert body["city"] is None

    def test_a_preferred_name_can_be_set(self, client: TestClient) -> None:
        """The one name field the patient owns outright."""
        body = client.patch(PROFILE, headers=_auth(TOKEN_A), json={"preferred_name": "Addy"}).json()

        assert body["preferred_name"] == "Addy"
        assert body["first_name"] == "Ada"

    def test_writing_nothing_is_a_valid_no_op(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        response = client.patch(PROFILE, headers=_auth(TOKEN_A), json={})

        assert response.status_code == 200
        assert audit.changes_for(AuditAction.PATIENT_PROFILE_UPDATED) == []

    def test_writing_the_same_value_is_not_a_change(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        """A form that resubmits unchanged fields must not fill the log.

        It would also fire a contact-changed event for an address that did
        not move, which is the one event a clinician should be able to trust.
        """
        client.patch(PROFILE, headers=_auth(TOKEN_A), json={"email": "ada@example.test"})

        assert audit.changes_for(AuditAction.PATIENT_PROFILE_UPDATED) == []
        assert audit.changes_for(AuditAction.PATIENT_PROFILE_CONTACT_CHANGED) == []

    def test_a_single_factor_principal_is_refused(self, client: TestClient) -> None:
        response = client.patch(PROFILE, headers=_auth(TOKEN_A_WEAK), json={"city": "Brooklyn"})

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "STEP_UP_REQUIRED"

    def test_a_refused_write_changes_nothing(
        self, client: TestClient, patients: InMemoryPatientRepository
    ) -> None:
        client.patch(PROFILE, headers=_auth(TOKEN_A_WEAK), json={"city": "Brooklyn"})

        assert patients._patients[PATIENT_A].city == "London"

    def test_no_credential_is_401(self, client: TestClient) -> None:
        assert client.patch(PROFILE, json={"city": "Brooklyn"}).status_code == 401

    def test_a_malformed_email_is_refused(self, client: TestClient) -> None:
        """The same validator the clinician-facing write uses.

        A value the chart's own surfaces would have refused must not reach
        it from here — and an unparseable address is a delivery channel that
        silently stops working.
        """
        assert (
            client.patch(
                PROFILE, headers=_auth(TOKEN_A), json={"email": "not-an-address"}
            ).status_code
            == 422
        )


class TestIdentityIsReadOnly:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("first_name", "Augusta"),
            ("last_name", "Byron"),
            ("date_of_birth", "1900-01-01"),
        ],
    )
    def test_naming_an_identity_field_is_refused(
        self, client: TestClient, field: str, value: str
    ) -> None:
        """422, not a silent drop.

        Accepting and ignoring it would answer 200 with the unchanged name —
        a patient told their correction saved when it did not. A refusal
        that names the field is something a screen can turn into "ask your
        practice to change this".
        """
        response = client.patch(PROFILE, headers=_auth(TOKEN_A), json={field: value})

        assert response.status_code == 422

    def test_a_refused_request_writes_none_of_its_other_fields(
        self, client: TestClient, patients: InMemoryPatientRepository
    ) -> None:
        """All or nothing, so a mixed request cannot half-apply."""
        client.patch(
            PROFILE, headers=_auth(TOKEN_A), json={"city": "Brooklyn", "first_name": "Augusta"}
        )

        assert patients._patients[PATIENT_A].city == "London"

    @pytest.mark.parametrize("field", ["diagnosis", "rate_cents", "status", "sex"])
    def test_naming_a_staff_authored_field_is_refused(self, client: TestClient, field: str) -> None:
        """Not in the request model at all, so ``extra=forbid`` catches it.

        ``sex`` is the one that is easy to mistake for an oversight: it is
        the administrative code a claim carries, so editing it would be
        editing a billing field.
        """
        response = client.patch(PROFILE, headers=_auth(TOKEN_A), json={field: "anything"})

        assert response.status_code == 422


class TestTwoPatientsCannotReachEachOther:
    def test_each_read_returns_only_the_callers_own_chart(self, client: TestClient) -> None:
        """Structural rather than checked: there is no id in the request.

        A route shaped ``GET /patient/{id}/profile`` would have an IDOR
        surface by construction, and the only thing between two patients
        would be somebody remembering to compare.
        """
        a = client.get(PROFILE, headers=_auth(TOKEN_A)).json()
        b = client.get(PROFILE, headers=_auth(TOKEN_B)).json()

        assert a["first_name"] == "Ada"
        assert b["first_name"] == "Grace"

    def test_a_write_reaches_only_the_callers_own_chart(
        self, client: TestClient, patients: InMemoryPatientRepository
    ) -> None:
        client.patch(PROFILE, headers=_auth(TOKEN_A), json={"city": "Brooklyn"})

        assert patients._patients[PATIENT_B].city == "London"

    def test_naming_another_patients_id_in_the_body_is_refused(
        self, client: TestClient, patients: InMemoryPatientRepository
    ) -> None:
        """There is no ``id`` field to accept, so the attempt is a 422."""
        response = client.patch(
            PROFILE, headers=_auth(TOKEN_A), json={"id": PATIENT_B, "city": "Brooklyn"}
        )

        assert response.status_code == 422
        assert patients._patients[PATIENT_B].city == "London"


class TestTheAuditPayloadCarriesNoValues:
    def test_a_profile_write_records_field_names_only(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        """An address is PHI-adjacent and already lives on the row."""
        client.patch(
            PROFILE, headers=_auth(TOKEN_A), json={"city": "Brooklyn", "postal_code": "11201"}
        )

        assert audit.changes_for(AuditAction.PATIENT_PROFILE_UPDATED) == [
            {"fields": ["city", "postal_code"]}
        ]

    def test_an_address_change_is_not_a_contact_channel_change(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        """The second event is about where a CREDENTIAL would be sent."""
        client.patch(PROFILE, headers=_auth(TOKEN_A), json={"city": "Brooklyn"})

        assert audit.changes_for(AuditAction.PATIENT_PROFILE_CONTACT_CHANGED) == []

    def test_an_email_change_records_hashed_old_and_new(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        client.patch(PROFILE, headers=_auth(TOKEN_A), json={"email": "ada@newmail.test"})

        recorded = audit.changes_for(AuditAction.PATIENT_PROFILE_CONTACT_CHANGED)
        assert len(recorded) == 1
        handles = recorded[0]["email"]
        assert handles["old"]
        assert handles["new"]
        assert handles["old"] != handles["new"]

    def test_neither_address_appears_in_the_payload(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        """The whole point of hashing them.

        The row proves a change happened and lets one value be tested
        against it; it is not a second copy of the patient's contact book.
        """
        client.patch(PROFILE, headers=_auth(TOKEN_A), json={"email": "ada@newmail.test"})

        payload = str(audit.changes_for(AuditAction.PATIENT_PROFILE_CONTACT_CHANGED))
        assert "ada@newmail.test" not in payload
        assert "ada@example.test" not in payload

    def test_a_phone_change_is_recorded_too(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        client.patch(PROFILE, headers=_auth(TOKEN_A), json={"phone": "+15005550009"})

        recorded = audit.changes_for(AuditAction.PATIENT_PROFILE_CONTACT_CHANGED)
        assert list(recorded[0]) == ["phone"]
        assert "5550009" not in str(recorded[0])

    def test_clearing_a_channel_records_a_null_new_handle(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        """That a value was CLEARED is not a secret, and hashing the empty
        string would produce one constant that gave it away anyway."""
        client.patch(PROFILE, headers=_auth(TOKEN_A), json={"phone": None})

        recorded = audit.changes_for(AuditAction.PATIENT_PROFILE_CONTACT_CHANGED)
        assert recorded[0]["phone"]["new"] is None
        assert recorded[0]["phone"]["old"] is not None

    def test_both_rows_are_written_for_one_contact_change(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        """They answer different questions, so one does not stand in for the
        other: what the patient edited, and that a delivery channel moved."""
        client.patch(PROFILE, headers=_auth(TOKEN_A), json={"email": "ada@newmail.test"})

        assert len(audit.changes_for(AuditAction.PATIENT_PROFILE_UPDATED)) == 1
        assert len(audit.changes_for(AuditAction.PATIENT_PROFILE_CONTACT_CHANGED)) == 1


def test_a_changed_email_is_what_recovery_would_find(
    client: TestClient, patients: InMemoryPatientRepository
) -> None:
    """Contact changes take effect immediately, including for recovery.

    The alternative — a pending value a clinician confirms — means a patient
    who changed email providers cannot recover, which is the case recovery
    exists for. What it costs is recorded rather than mitigated: the change
    is audited as its own event, on the chart's activity.
    """
    client.patch(PROFILE, headers=_auth(TOKEN_A), json={"email": "ada@newmail.test"})

    assert patients._patients[PATIENT_A].email == "ada@newmail.test"
