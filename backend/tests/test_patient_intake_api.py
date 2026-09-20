# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for ``/api/patient/intake``.

The suite runs through the real ``get_patient_context`` with a synthesized
front door and the session arming patched out, so what is under test is what
the ROUTES do with a principal: which id they trust, which columns they let
out, what they write, and what the audit says about it.

Two-patient isolation at the database layer is the integration suite's job
(``tests_integration/database/test_patient_intake_routes_rls.py``); it cannot
be proven here, because there is no database here. What can be proven here is
that a body naming another patient changes nothing — which is the half a
policy cannot fix.
"""

from __future__ import annotations

import uuid
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
from app.models import Patient
from app.models.audit import ACTOR_TYPE_PATIENT, AuditAction, ResourceType
from app.models.patient_facing import (
    PATIENT_COLUMN_DECISIONS,
    shown_columns,
    withheld_columns,
)
from app.rate_limit import get_chat_send_limiter, get_intake_submit_limiter
from app.repositories import (
    InMemoryPatientIntakeSubmissionRepository,
    InMemoryPatientRepository,
    get_outcome_measure_repository,
    get_patient_intake_submission_repository,
    get_patient_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.outcome_measure import InMemoryOutcomeMeasureRepository
from app.routes import patient_intake
from app.services.audit_service import AuditService, get_audit_service
from app.utcnow import utc_now
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.audit import AuditLogEntry

_PATIENT_A = "11111111-1111-4111-8111-111111111111"
_PATIENT_B = "22222222-2222-4222-8222-222222222222"
_TOKEN_A = "credential-of-patient-a"
_TOKEN_B = "credential-of-patient-b"
_TOKEN_A_WEAK = "single-factor-credential-of-patient-a"

FORM = "/api/patient/intake/form"
SUBMISSIONS = "/api/patient/intake/submissions"

# Staff-authored text seeded onto every test chart. Distinctive strings, so a
# body check catches the CONTENT reaching a patient and not only the field name.
_DIAGNOSIS = "F41.1 generalised anxiety, provisional"
_SLIDING_SCALE_NOTE = "agreed 90 a session while between jobs"

_ALL_ZERO_PHQ9 = {str(i): 0 for i in range(1, 10)}
_ALL_THREE_PHQ9 = {str(i): 3 for i in range(1, 10)}
_ALL_ZERO_GAD7 = {str(i): 0 for i in range(1, 8)}
# 15 exactly: five items at 3, two at 0 — the bottom of the "severe" band.
_SEVERE_GAD7 = {"1": 3, "2": 3, "3": 3, "4": 3, "5": 3, "6": 0, "7": 0}


class _TwoPatientResolver:
    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        principals = {
            _TOKEN_A: (_PATIENT_A, AuthStrength.STEPPED_UP),
            _TOKEN_B: (_PATIENT_B, AuthStrength.STEPPED_UP),
            _TOKEN_A_WEAK: (_PATIENT_A, AuthStrength.SINGLE_FACTOR),
        }
        found = principals.get(credential.value)
        if found is None:
            return None
        patient_id, strength = found
        return PatientContext(
            patient_id=patient_id,
            practice_schema="practice_test_intake",
            credential_kind="bearer",
            auth_strength=strength,
        )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _patient(patient_id: str, first: str, last: str, dob: str) -> Patient:
    now = utc_now()
    return Patient(
        id=patient_id,
        first_name=first,
        last_name=last,
        created_at=now,
        updated_at=now,
        date_of_birth=dob,
        # Staff-authored columns, populated so the withholding assertions in
        # ``TestTheFormWithholdsStaffColumns`` have something to catch. Left at
        # their defaults, the chart would pass against a route that returned
        # every one of them.
        email=f"{first.lower()}@example.test",
        phone="+15555550100",
        diagnosis=_DIAGNOSIS,
        rate_cents=9000,
        sliding_scale_note=_SLIDING_SCALE_NOTE,
        origin="voice",
        chart_closure_reason="Moved out of state",
        address_line1="12 Analytical Engine Way",
        city="London",
        state="NY",
        postal_code="10001",
        sex="F",
    )


def _submission_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name_confirmed": True,
        "dob_confirmed": True,
        "corrections": None,
        "reason_text": "Panic at work for about two months.",
        "phq9": dict(_ALL_ZERO_PHQ9),
        "gad7": dict(_ALL_ZERO_GAD7),
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def patients() -> InMemoryPatientRepository:
    repo = InMemoryPatientRepository()
    repo.create(_patient(_PATIENT_A, "Ada", "Lovelace", "1990-03-14"), "seed")
    repo.create(_patient(_PATIENT_B, "Grace", "Hopper", "1985-12-09"), "seed")
    return repo


@pytest.fixture
def measures() -> InMemoryOutcomeMeasureRepository:
    repo = InMemoryOutcomeMeasureRepository()
    # Read-back only. The self-report write path takes no user id and asks
    # for no grant, which is the point of it; the in-memory reads do.
    repo.grant_all_access()
    return repo


@pytest.fixture
def submissions() -> InMemoryPatientIntakeSubmissionRepository:
    return InMemoryPatientIntakeSubmissionRepository()


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    """The real AuditService with its storage swapped.

    A hand-written double would imitate what the service records, and then
    pass whether or not the service records it. This way ``actor_type`` and
    ``changes`` are the values production writes.
    """
    return InMemoryAuditRepository()


@pytest.fixture(autouse=True)
def _fresh_limiters() -> Iterator[None]:
    """Budgets do not carry between tests."""
    get_intake_submit_limiter().reset()
    get_chat_send_limiter().reset()
    yield
    get_intake_submit_limiter().reset()
    get_chat_send_limiter().reset()


@pytest.fixture
def intake_app(
    patients: InMemoryPatientRepository,
    measures: InMemoryOutcomeMeasureRepository,
    submissions: InMemoryPatientIntakeSubmissionRepository,
    audit_repo: InMemoryAuditRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """The intake router alone, with a patient front door and no database.

    Its own app rather than the shared one so a test can stamp the
    clinician-credential state ``DatabaseSessionMiddleware`` sets in
    production — the state the patient dependency refuses on.
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(patient_intake.router)

    @app.middleware("http")
    async def _stash_clinician_identity(request: Request, call_next):  # type: ignore[no-untyped-def]
        # What DatabaseSessionMiddleware does once a clinician token
        # verifies. Driven by a header so a test can ask for it.
        if request.headers.get("x-test-clinician-verified"):
            request.state.verified_identity = {"uid": "clinician-1"}
        return await call_next(request)

    registry = PatientResolverRegistry()
    registry.register(_TwoPatientResolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    app.dependency_overrides[get_patient_repository] = lambda: patients
    app.dependency_overrides[get_outcome_measure_repository] = lambda: measures
    app.dependency_overrides[get_patient_intake_submission_repository] = lambda: submissions
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)

    monkeypatch.setattr(patient_context_module, "get_db_session", object)
    monkeypatch.setattr(patient_context_module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(patient_context_module, "arm_current_patient_id", lambda _s, _p: None)
    return app


@pytest.fixture
def client(intake_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(intake_app) as c:
        yield c


def _entries(audit_repo: InMemoryAuditRepository, patient_id: str) -> list[AuditLogEntry]:
    return list(audit_repo.list_for_user(patient_id))


def _spend_until_refused(limiter: object, key: str, *, attempts: int = 500) -> bool:
    """Exhaust *limiter* for *key*. True once it refuses."""
    for _ in range(attempts):
        try:
            limiter.check(key)  # type: ignore[attr-defined]
        except HTTPException:
            return True
    return False


# ---------------------------------------------------------------------------
# Authentication and step-up
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_form_without_a_credential_is_401(self, client: TestClient) -> None:
        assert client.get(FORM).status_code == 401

    def test_submit_without_a_credential_is_401(self, client: TestClient) -> None:
        assert client.post(SUBMISSIONS, json=_submission_body()).status_code == 401

    def test_an_unknown_credential_is_401(self, client: TestClient) -> None:
        assert client.get(FORM, headers=_auth("forged")).status_code == 401

    @pytest.mark.parametrize("path", [FORM, SUBMISSIONS])
    def test_a_verified_clinician_credential_is_401(self, client: TestClient, path: str) -> None:
        """A clinician token is also ``Bearer``. The patient door refuses it.

        The credential resolves perfectly well — it is one of the two the
        test front door accepts — so the 401 can only come from the
        clinician short-circuit.
        """
        headers = {**_auth(_TOKEN_A), "X-Test-Clinician-Verified": "1"}
        response = client.request(
            "POST" if path == SUBMISSIONS else "GET",
            path,
            json=_submission_body() if path == SUBMISSIONS else None,
            headers=headers,
        )
        assert response.status_code == 401
        assert response.json()["detail"]["error"]["code"] == "PATIENT_NOT_AUTHENTICATED"

    @pytest.mark.parametrize("path", [FORM, SUBMISSIONS])
    def test_a_single_factor_principal_is_refused(self, client: TestClient, path: str) -> None:
        response = client.request(
            "POST" if path == SUBMISSIONS else "GET",
            path,
            json=_submission_body() if path == SUBMISSIONS else None,
            headers=_auth(_TOKEN_A_WEAK),
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "STEP_UP_REQUIRED"

    def test_a_refused_submission_writes_nothing(
        self,
        client: TestClient,
        submissions: InMemoryPatientIntakeSubmissionRepository,
        measures: InMemoryOutcomeMeasureRepository,
    ) -> None:
        """Step-up is checked before any write, not after."""
        client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A_WEAK))
        assert submissions.rows == {}
        assert measures.list_by_patient(_PATIENT_A) == []


# ---------------------------------------------------------------------------
# GET /form
# ---------------------------------------------------------------------------


class TestForm:
    def test_the_form_carries_the_callers_own_identity(self, client: TestClient) -> None:
        body = client.get(FORM, headers=_auth(_TOKEN_A)).json()
        assert body["identity"] == {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "date_of_birth": "1990-03-14",
        }

    def test_another_patients_identity_is_absent(self, client: TestClient) -> None:
        text = client.get(FORM, headers=_auth(_TOKEN_A)).text
        assert "Grace" not in text
        assert "Hopper" not in text
        assert _PATIENT_B not in text

    def test_patient_b_gets_patient_bs_form(self, client: TestClient) -> None:
        """The symmetric case: the route is not accidentally keyed to A."""
        body = client.get(FORM, headers=_auth(_TOKEN_B)).json()
        assert body["identity"]["first_name"] == "Grace"

    def test_the_response_carries_no_patient_id(self, client: TestClient) -> None:
        text = client.get(FORM, headers=_auth(_TOKEN_A)).text
        assert _PATIENT_A not in text


class TestTheFormWithholdsStaffColumns:
    """The only patient-facing route that returns a chart row, on the wire.

    ``rls_patient_self_read`` grants this principal their whole ``patients``
    row and row-level security cannot narrow that to columns, so the projection
    is the only thing between a patient and the clinician's working diagnosis
    or the note about what they can afford. Which columns those are is read
    from the decisions in ``app.models.patient_facing`` rather than listed
    again here: a list copied into a test agrees with the copy, not with the
    table.
    """

    def test_no_withheld_column_reaches_the_identity_block(self, client: TestClient) -> None:
        identity = client.get(FORM, headers=_auth(_TOKEN_A)).json()["identity"]

        for withheld in withheld_columns(PATIENT_COLUMN_DECISIONS):
            assert withheld not in identity, f"{withheld} reached a patient-facing response"

    def test_the_identity_block_is_the_three_identity_columns(self, client: TestClient) -> None:
        """Narrower than the shown set, on purpose.

        The form asks one question — is this who the chart says you are —
        so it shows a name and a date of birth and stops. The patient may
        also SEE their contact details and address (the profile screen shows
        them, and they are shown in ``PATIENT_COLUMN_DECISIONS``), but
        putting them on this form would be asking a second question in the
        middle of the first.

        Asserted as a subset of the shown set rather than as an equality, so
        the two can differ deliberately while a column that is withheld
        everywhere still cannot appear here — which the case above checks.
        """
        identity = client.get(FORM, headers=_auth(_TOKEN_A)).json()["identity"]
        assert set(identity) == {"first_name", "last_name", "date_of_birth"}
        assert set(identity) <= shown_columns(PATIENT_COLUMN_DECISIONS)

    def test_no_withheld_column_reaches_the_response_at_all(self, client: TestClient) -> None:
        """Not just the identity block — the whole body, keys and values.

        The form carries instrument text alongside the identity, so a widening
        anywhere in the envelope is in scope here.
        """
        text = client.get(FORM, headers=_auth(_TOKEN_A)).text
        for content in (_DIAGNOSIS, _SLIDING_SCALE_NOTE, "9000", "voice", "Moved out of state"):
            assert content not in text, f"{content!r} reached a patient-facing response"

    def test_the_repository_never_reads_the_withheld_columns(self) -> None:
        """One layer down: the projection is what makes the body check hold.

        A route that happened to drop a column would pass the assertions above
        while the column was still selected, sitting in memory for the next
        refactor to reach for.
        """
        patients = InMemoryPatientRepository()
        patients.create(_patient(_PATIENT_A, "Ada", "Lovelace", "1990-03-14"), "seed")

        own = patients.get_for_patient_principal(_PATIENT_A)

        assert own is not None
        assert set(type(own).model_fields) == shown_columns(PATIENT_COLUMN_DECISIONS)
        for withheld in withheld_columns(PATIENT_COLUMN_DECISIONS):
            assert not hasattr(own, withheld), f"{withheld} was read for a patient principal"

    def test_it_asks_both_screeners_in_order(self, client: TestClient) -> None:
        body = client.get(FORM, headers=_auth(_TOKEN_A)).json()
        assert [i["code"] for i in body["instruments"]] == ["phq9", "gad7"]

    @pytest.mark.parametrize(("index", "code", "count"), [(0, "phq9", 9), (1, "gad7", 7)])
    def test_item_keys_are_the_keys_the_scorer_accepts(
        self, client: TestClient, index: int, code: str, count: int
    ) -> None:
        from app.outcome_measures.instruments import INSTRUMENT_REGISTRY  # noqa: PLC0415

        instrument = client.get(FORM, headers=_auth(_TOKEN_A)).json()["instruments"][index]
        assert instrument["code"] == code
        assert set(instrument["items"]) == INSTRUMENT_REGISTRY[code].valid_keys
        assert len(instrument["items"]) == count
        assert all(text.strip() for text in instrument["items"].values())

    @pytest.mark.parametrize("index", [0, 1])
    def test_the_response_options_are_the_published_anchors(
        self, client: TestClient, index: int
    ) -> None:
        options = client.get(FORM, headers=_auth(_TOKEN_A)).json()["instruments"][index][
            "response_options"
        ]
        assert options == [
            {"value": 0, "label": "Not at all"},
            {"value": 1, "label": "Several days"},
            {"value": 2, "label": "More than half the days"},
            {"value": 3, "label": "Nearly every day"},
        ]

    def test_it_asks_what_brings_them_in(self, client: TestClient) -> None:
        body = client.get(FORM, headers=_auth(_TOKEN_A)).json()
        assert body["reason_prompt"] == "What brings you in?"

    def test_the_disclosure_is_audited(
        self, client: TestClient, audit_repo: InMemoryAuditRepository
    ) -> None:
        client.get(FORM, headers=_auth(_TOKEN_A))

        entries = _entries(audit_repo, _PATIENT_A)
        assert len(entries) == 1
        assert entries[0].action == AuditAction.PATIENT_VIEWED.value
        assert entries[0].actor_type == ACTOR_TYPE_PATIENT
        assert entries[0].patient_id == _PATIENT_A

    def test_a_patient_with_no_chart_row_is_404(
        self, client: TestClient, patients: InMemoryPatientRepository
    ) -> None:
        patients.delete(_PATIENT_A, "seed")
        assert client.get(FORM, headers=_auth(_TOKEN_A)).status_code == 404


# ---------------------------------------------------------------------------
# POST /submissions
# ---------------------------------------------------------------------------


class TestSubmit:
    def test_a_complete_form_is_201(self, client: TestClient) -> None:
        response = client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A))
        assert response.status_code == 201, response.text

    def test_it_records_exactly_two_measures(
        self, client: TestClient, measures: InMemoryOutcomeMeasureRepository
    ) -> None:
        client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A))

        rows = measures.list_by_patient(_PATIENT_A)
        assert sorted(str(r["instrument"]) for r in rows) == ["gad7", "phq9"]

    def test_every_measure_is_a_complete_self_report_by_the_patient(
        self, client: TestClient, measures: InMemoryOutcomeMeasureRepository
    ) -> None:
        client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A))

        for row in measures.list_by_patient(_PATIENT_A):
            assert row["source"] == "patient_self_report"
            assert row["created_by"] == _PATIENT_A
            assert row["patient_id"] == _PATIENT_A
            assert row["is_complete"] is True

    def test_it_records_exactly_one_submission(
        self, client: TestClient, submissions: InMemoryPatientIntakeSubmissionRepository
    ) -> None:
        response = client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A))

        assert len(submissions.rows) == 1
        row = next(iter(submissions.rows.values()))
        assert row["id"] == response.json()["id"]
        assert row["patient_id"] == _PATIENT_A
        assert row["created_by"] == _PATIENT_A

    def test_the_submission_keeps_the_reason_and_the_attestation(
        self, client: TestClient, submissions: InMemoryPatientIntakeSubmissionRepository
    ) -> None:
        client.post(
            SUBMISSIONS,
            json=_submission_body(
                name_confirmed=False,
                dob_confirmed=True,
                corrections="My last name is spelled Lovelase.",
            ),
            headers=_auth(_TOKEN_A),
        )

        payload = next(iter(submissions.rows.values()))["payload"]
        assert isinstance(payload, dict)
        assert payload["reason_text"] == "Panic at work for about two months."
        assert payload["name_confirmed"] is False
        assert payload["dob_confirmed"] is True
        assert payload["corrections"] == "My last name is spelled Lovelase."

    def test_the_envelope_keeps_the_answers_and_names_the_rows_they_scored(
        self, client: TestClient, submissions: InMemoryPatientIntakeSubmissionRepository
    ) -> None:
        response = client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A))

        payload = next(iter(submissions.rows.values()))["payload"]
        assert isinstance(payload, dict)
        assert payload["instruments"] == {"phq9": _ALL_ZERO_PHQ9, "gad7": _ALL_ZERO_GAD7}
        recorded = {m["instrument"]: m["id"] for m in response.json()["measures"]}
        assert payload["outcome_measure_ids"] == recorded

    def test_a_mismatched_name_does_not_edit_the_chart(
        self, client: TestClient, patients: InMemoryPatientRepository
    ) -> None:
        """Attestation, not adjudication."""
        client.post(
            SUBMISSIONS,
            json=_submission_body(name_confirmed=False, corrections="It is Ada Byron now."),
            headers=_auth(_TOKEN_A),
        )
        assert patients.get_for_patient_principal(_PATIENT_A).last_name == "Lovelace"  # type: ignore[union-attr]

    def test_resubmitting_appends_rather_than_replaces(
        self,
        client: TestClient,
        measures: InMemoryOutcomeMeasureRepository,
        submissions: InMemoryPatientIntakeSubmissionRepository,
    ) -> None:
        client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A))
        client.post(
            SUBMISSIONS,
            json=_submission_body(phq9=dict(_ALL_THREE_PHQ9)),
            headers=_auth(_TOKEN_A),
        )

        assert len(submissions.rows) == 2
        assert len(measures.list_by_patient(_PATIENT_A)) == 4


class TestTheBodyCannotNameAnotherPatient:
    @pytest.mark.parametrize("field", ["patient_id", "created_by"])
    def test_an_unknown_field_is_ignored_and_the_rows_stay_the_callers(
        self,
        client: TestClient,
        measures: InMemoryOutcomeMeasureRepository,
        submissions: InMemoryPatientIntakeSubmissionRepository,
        field: str,
    ) -> None:
        """The request shape has no patient id, so naming one changes nothing."""
        response = client.post(
            SUBMISSIONS,
            json=_submission_body(**{field: _PATIENT_B}),
            headers=_auth(_TOKEN_A),
        )
        assert response.status_code == 201, response.text

        assert all(r["patient_id"] == _PATIENT_A for r in measures.list_by_patient(_PATIENT_A))
        assert measures.list_by_patient(_PATIENT_B) == []
        row = next(iter(submissions.rows.values()))
        assert row["patient_id"] == _PATIENT_A
        assert row["created_by"] == _PATIENT_A


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    def test_an_all_zero_phq9_is_minimal(self, client: TestClient) -> None:
        body = client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A)).json()
        phq9 = next(m for m in body["measures"] if m["instrument"] == "phq9")
        assert phq9["total_score"] == 0
        assert phq9["severity"] == "minimal"

    def test_an_all_threes_phq9_is_27_and_severe(self, client: TestClient) -> None:
        body = client.post(
            SUBMISSIONS,
            json=_submission_body(phq9=dict(_ALL_THREE_PHQ9)),
            headers=_auth(_TOKEN_A),
        ).json()
        phq9 = next(m for m in body["measures"] if m["instrument"] == "phq9")
        assert phq9["total_score"] == 27
        assert phq9["severity"] == "severe"

    def test_a_gad7_totalling_15_is_severe(self, client: TestClient) -> None:
        body = client.post(
            SUBMISSIONS,
            json=_submission_body(gad7=dict(_SEVERE_GAD7)),
            headers=_auth(_TOKEN_A),
        ).json()
        gad7 = next(m for m in body["measures"] if m["instrument"] == "gad7")
        assert gad7["total_score"] == 15
        assert gad7["severity"] == "severe"

    def test_the_stored_total_matches_the_response(
        self, client: TestClient, measures: InMemoryOutcomeMeasureRepository
    ) -> None:
        client.post(
            SUBMISSIONS,
            json=_submission_body(phq9=dict(_ALL_THREE_PHQ9), gad7=dict(_SEVERE_GAD7)),
            headers=_auth(_TOKEN_A),
        )
        totals = {
            str(r["instrument"]): r["total_score"] for r in measures.list_by_patient(_PATIENT_A)
        }
        assert totals == {"phq9": 27, "gad7": 15}


class TestRejectedForms:
    @pytest.mark.parametrize("bad", [4, -1])
    def test_an_out_of_range_item_is_400(self, client: TestClient, bad: int) -> None:
        answers = dict(_ALL_ZERO_PHQ9)
        answers["3"] = bad
        response = client.post(
            SUBMISSIONS, json=_submission_body(phq9=answers), headers=_auth(_TOKEN_A)
        )
        assert response.status_code == 400

    def test_a_missing_item_is_400(self, client: TestClient) -> None:
        answers = dict(_ALL_ZERO_PHQ9)
        del answers["9"]
        response = client.post(
            SUBMISSIONS, json=_submission_body(phq9=answers), headers=_auth(_TOKEN_A)
        )
        assert response.status_code == 400

    def test_an_unknown_item_key_is_400(self, client: TestClient) -> None:
        answers = dict(_ALL_ZERO_PHQ9)
        answers["10"] = 1
        response = client.post(
            SUBMISSIONS, json=_submission_body(phq9=answers), headers=_auth(_TOKEN_A)
        )
        assert response.status_code == 400

    def test_an_empty_reason_is_refused(self, client: TestClient) -> None:
        response = client.post(
            SUBMISSIONS, json=_submission_body(reason_text=""), headers=_auth(_TOKEN_A)
        )
        assert response.status_code == 422

    def test_a_rejected_form_leaves_nothing_behind(
        self,
        client: TestClient,
        measures: InMemoryOutcomeMeasureRepository,
        submissions: InMemoryPatientIntakeSubmissionRepository,
    ) -> None:
        """The GAD-7 is bad; the PHQ-9 before it must not survive on its own."""
        answers = dict(_ALL_ZERO_GAD7)
        answers["2"] = 9
        client.post(SUBMISSIONS, json=_submission_body(gad7=answers), headers=_auth(_TOKEN_A))

        assert submissions.rows == {}

    def test_the_rejection_does_not_quote_the_answer(self, client: TestClient) -> None:
        answers = dict(_ALL_ZERO_PHQ9)
        answers["9"] = 3
        answers["3"] = 42
        response = client.post(
            SUBMISSIONS, json=_submission_body(phq9=answers), headers=_auth(_TOKEN_A)
        )
        assert response.status_code == 400
        assert "Panic at work" not in response.text


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_one_entry_names_the_submission(
        self,
        client: TestClient,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        response = client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A))

        submitted = [
            e
            for e in _entries(audit_repo, _PATIENT_A)
            if e.action == AuditAction.PATIENT_INTAKE_SUBMITTED.value
        ]
        assert len(submitted) == 1
        entry = submitted[0]
        assert entry.actor_type == ACTOR_TYPE_PATIENT
        assert entry.resource_type == ResourceType.PATIENT_INTAKE_SUBMISSION.value
        assert entry.resource_id == response.json()["id"]
        assert entry.patient_id == _PATIENT_A

    def test_the_entry_names_the_instruments_and_nothing_they_said(
        self,
        client: TestClient,
        audit_repo: InMemoryAuditRepository,
    ) -> None:
        client.post(
            SUBMISSIONS,
            json=_submission_body(phq9=dict(_ALL_THREE_PHQ9)),
            headers=_auth(_TOKEN_A),
        )

        entry = next(
            e
            for e in _entries(audit_repo, _PATIENT_A)
            if e.action == AuditAction.PATIENT_INTAKE_SUBMITTED.value
        )
        assert entry.changes == {"instruments": ["phq9", "gad7"]}

    def test_neither_the_scores_nor_the_reason_reach_the_log(
        self,
        client: TestClient,
        audit_repo: InMemoryAuditRepository,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level("DEBUG"):
            client.post(
                SUBMISSIONS,
                json=_submission_body(
                    reason_text="I have been having panic attacks since March.",
                    phq9=dict(_ALL_THREE_PHQ9),
                ),
                headers=_auth(_TOKEN_A),
            )

        entry = next(
            e
            for e in _entries(audit_repo, _PATIENT_A)
            if e.action == AuditAction.PATIENT_INTAKE_SUBMITTED.value
        )
        recorded = str(entry.changes)
        assert "panic" not in recorded.lower()
        assert "27" not in recorded
        assert "item_scores" not in recorded

        logged = caplog.text.lower()
        assert "panic attacks" not in logged
        assert "lovelace" not in logged


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_a_burst_of_submissions_is_eventually_refused(self, client: TestClient) -> None:
        codes = [
            client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A)).status_code
            for _ in range(8)
        ]
        assert 429 in codes
        assert codes.count(201) >= 1

    def test_the_budget_is_per_patient(self, client: TestClient) -> None:
        for _ in range(8):
            client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A))

        # B has spent nothing.
        response = client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_B))
        assert response.status_code == 201, response.text

    def test_it_shares_no_budget_with_chat_send(self, client: TestClient) -> None:
        """Same raw key, different namespace — see NamespacedLimiter."""
        assert _spend_until_refused(get_chat_send_limiter(), _PATIENT_A), (
            "the chat-send budget never ran out, so the assertion below is vacuous"
        )

        response = client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A))
        assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# Mounting
# ---------------------------------------------------------------------------


def test_the_router_is_mounted_unconditionally() -> None:
    """No flag: with no resolver registered every URL answers 401 anyway."""
    from app.main import app as real_app  # noqa: PLC0415
    from app.route_introspection import iter_api_routes  # noqa: PLC0415

    mounted = {
        (path, method) for path, route in iter_api_routes(real_app) for method in route.methods
    }
    assert (FORM, "GET") in mounted
    assert (SUBMISSIONS, "POST") in mounted


def test_a_submission_id_is_a_uuid(
    client: TestClient, submissions: InMemoryPatientIntakeSubmissionRepository
) -> None:
    client.post(SUBMISSIONS, json=_submission_body(), headers=_auth(_TOKEN_A))
    uuid.UUID(str(next(iter(submissions.rows))))
