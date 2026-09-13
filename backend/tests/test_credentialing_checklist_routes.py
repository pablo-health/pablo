# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The checklist endpoints the wizard talks to.

* the question set comes back narrowed to this clinician, and the supervision
  fork changes it on the same request that answers it;
* Tier 1 can be completed and left, and what comes back then says so — every
  claims-ready question answered, Tier 2 untouched, nothing in an error state;
* a confirmation is recorded rather than merely rendered, and one marked wrong
  must say what the right value is;
* progress is reported per tier, with no overall figure to render a stopped
  Tier 1 as half done.

Runs the real router over an in-memory SQLite session.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.api_errors import register_exception_handlers
from app.auth.route_access import subscription_exempt
from app.auth.service import get_current_user, get_tenant_context
from app.credentialing import confirmations, government_ids
from app.credentialing.checklist import CHECKLIST_FIELDS, Tier, applicable, fields_for_tier
from app.db import get_db_session
from app.db.models import (
    Base,
    ClinicianProfileRow,
    ComplianceDocumentRow,
    CredentialBankAccountRow,
    CredentialLiabilityPolicyRow,
    CredentialLicenseRow,
    CredentialServiceLocationRow,
    PayerParticipationRow,
    PayerRow,
    PracticeBillingProfileRow,
)
from app.models import User
from app.routes import credentialing as credentialing_routes
from app.services.audit_service import AuditService, InMemoryAuditRepository
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.sqlite_engine import sqlite_engine

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine

_USER_ID = "11111111-1111-4111-8111-111111111111"
_OTHER_USER_ID = "22222222-2222-4222-8222-222222222222"
_URL = "/api/credentialing/checklist"


def _user() -> User:
    return User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=datetime.now(UTC),
        baa_accepted_at=datetime.now(UTC),
        baa_version="2024-01-01",
    )


#: Every table the question set writes into, derived from the targets rather
#: than listed — a retargeted field brings its table along instead of failing
#: here with a missing-table error nobody can read.
def _intake_tables() -> list[Any]:
    names = {f.target.partition(".")[0] for f in CHECKLIST_FIELDS}
    # ``payers`` is not a target — a participation row points at it, and the
    # fixture needs one to point at.
    names.add("payers")
    return [Base.metadata.tables[n] for n in sorted(names) if n in Base.metadata.tables]


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine() -> Iterator[Engine]:
    with sqlite_engine(_intake_tables()) as eng:
        yield eng


@pytest.fixture
def harness(engine: Engine) -> Iterator[dict[str, Any]]:
    session = Session(engine)
    app = FastAPI()
    # The same handlers main.py installs, so an APIError arrives as the status
    # it names rather than as a 500 this harness invented.
    register_exception_handlers(app)
    app.include_router(credentialing_routes.router)
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_tenant_context] = lambda: None
    app.dependency_overrides[subscription_exempt] = lambda: None
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield {"client": client, "session": session}
    finally:
        session.close()


def _confirm(client: TestClient, field_key: str, **overrides: Any) -> Any:
    field = next(f for f in CHECKLIST_FIELDS if f.key == field_key)
    payload = {"source": field.source, "confirmed": True, "presented_value": "as shown"}
    payload.update(overrides)
    return client.put(f"{_URL}/confirmations/{field_key}", json=payload)


class TestTheQuestionSetComesBackNarrowed:
    def test_an_ordinary_clinician_is_not_asked_the_supervised_questions(
        self, harness: dict[str, Any]
    ) -> None:
        body = harness["client"].get(_URL).json()

        assert body["supervised"] is False
        assert "supervisor" not in {f["key"] for f in body["fields"]}

    def test_answering_the_fork_changes_the_same_page_it_was_asked_on(
        self, harness: dict[str, Any]
    ) -> None:
        """The fork has to narrow the set before the answer is even stored.

        She is answering "are you independently licensed?" on the page whose
        remaining questions depend on it, so the caller can say which branch it
        is rendering without a round-trip through the record first.
        """
        body = harness["client"].get(_URL, params={"supervised": True}).json()

        assert body["supervised"] is True
        assert "supervisor" in {f["key"] for f in body["fields"]}

    def test_the_branch_is_taken_from_the_record_once_it_is_saved(
        self, harness: dict[str, Any]
    ) -> None:
        harness["client"].patch(f"{_URL}/answers", json={"supervision_status": "supervised"})

        body = harness["client"].get(_URL).json()

        assert body["supervised"] is True
        assert "supervisor" in {f["key"] for f in body["fields"]}

    def test_a_prescriber_is_asked_the_dea_questions(self, harness: dict[str, Any]) -> None:
        harness["session"].add(
            ClinicianProfileRow(
                user_id=_USER_ID,
                practice_id="practice-1",
                joined_at=datetime.now(UTC),
                dea_number="XB1234563",
            )
        )
        harness["session"].commit()

        body = harness["client"].get(_URL).json()

        assert body["prescriber"] is True
        assert "dea_certificate" in {f["key"] for f in body["fields"]}


class TestTierZeroConfirmations:
    def test_every_tier_zero_field_arrives_with_its_provenance(
        self, harness: dict[str, Any]
    ) -> None:
        """Nothing in this tier renders as an empty box awaiting typing."""
        fields = harness["client"].get(_URL).json()["fields"]
        tier_zero = [f for f in fields if f["tier"] == Tier.CONFIRM.value]

        assert tier_zero
        assert all(f["source"] for f in tier_zero)

    def test_a_confirmation_is_recorded_not_merely_rendered(self, harness: dict[str, Any]) -> None:
        response = _confirm(harness["client"], "npi_number")

        assert response.status_code == 200
        assert response.json()["confirmed"] is True
        listed = harness["client"].get(f"{_URL}/confirmations").json()
        assert [c["field_key"] for c in listed] == ["npi_number"]

    def test_confirming_twice_replaces_rather_than_accumulates(
        self, harness: dict[str, Any]
    ) -> None:
        """The question is "is this right now", not a history of what she saw."""
        _confirm(harness["client"], "npi_number")
        _confirm(
            harness["client"],
            "npi_number",
            confirmed=False,
            correction="1999999984",
        )

        listed = harness["client"].get(f"{_URL}/confirmations").json()

        assert len(listed) == 1
        assert listed[0]["confirmed"] is False
        assert listed[0]["correction"] == "1999999984"

    def test_marking_a_value_wrong_must_say_what_is_right(self, harness: dict[str, Any]) -> None:
        """The mirror image of the disclosure rule, and the reason for the table.

        ``credential_disclosures`` demands an explanation for a ``true``. Here
        ``true`` means "correct" and needs nothing, while a rejection with no
        correction records only that a source is wrong, not what it should say.
        """
        response = _confirm(harness["client"], "npi_number", confirmed=False)

        assert response.status_code == 400
        assert harness["client"].get(f"{_URL}/confirmations").json() == []

    def test_a_source_the_schema_does_not_know_is_refused(self, harness: dict[str, Any]) -> None:
        response = _confirm(harness["client"], "npi_number", source="a_hunch")

        assert response.status_code == 400

    def test_only_a_tier_zero_field_can_be_confirmed(self, harness: dict[str, Any]) -> None:
        response = harness["client"].put(
            f"{_URL}/confirmations/ssn",
            json={"source": "nppes", "confirmed": True},
        )

        assert response.status_code == 404

    def test_confirming_answers_the_question(self, harness: dict[str, Any]) -> None:
        """A Tier-0 field with no home column is answered by its confirmation."""
        before = harness["client"].get(_URL).json()["fields"]
        assert not next(f for f in before if f["key"] == "exclusion_clearance")["answered"]

        _confirm(harness["client"], "exclusion_clearance")

        after = harness["client"].get(_URL).json()["fields"]
        assert next(f for f in after if f["key"] == "exclusion_clearance")["answered"]


class TestStoppingAfterTierOne:
    def test_progress_is_per_tier_with_no_overall_figure(self, harness: dict[str, Any]) -> None:
        body = harness["client"].get(_URL).json()

        assert {p["tier"] for p in body["progress"]} == {t.value for t in Tier}
        assert "percent" not in body
        assert "overall" not in body

    def test_a_finished_tier_one_reads_as_finished(self, harness: dict[str, Any]) -> None:
        """The design's promise, asserted end to end.

        Every claims-ready question answered and Tier 2 untouched is a complete
        outcome: ``claims_ready`` is true, Tier 1 is complete, and nothing in
        the response marks the record as wanting.
        """
        _answer_tier_one(harness)

        body = harness["client"].get(_URL).json()
        by_tier = {p["tier"]: p for p in body["progress"]}

        assert body["claims_ready"] is True
        assert by_tier[Tier.CLAIMS_READY.value]["complete"] is True
        assert by_tier[Tier.CREDENTIALING.value]["answered"] == 0
        assert by_tier[Tier.CREDENTIALING.value]["complete"] is False

    def test_the_caqh_question_is_answerable_during_tier_one(self, harness: dict[str, Any]) -> None:
        harness["client"].patch(f"{_URL}/answers", json={"caqh_id": "16273849"})

        fields = harness["client"].get(_URL).json()["fields"]
        caqh = next(f for f in fields if f["key"] == "caqh_id")

        assert caqh["tier"] == Tier.CLAIMS_READY.value
        assert caqh["answered"] is True


class TestSavingAnswers:
    def test_a_partial_save_leaves_the_rest_alone(self, harness: dict[str, Any]) -> None:
        """The checklist is meant to be left and resumed mid-tier."""
        harness["client"].patch(f"{_URL}/answers", json={"caqh_id": "16273849"})
        harness["client"].patch(f"{_URL}/answers", json={"business_structure": "llc"})

        saved = harness["client"].patch(f"{_URL}/answers", json={"medicare_intent": True}).json()

        assert saved["caqh_id"] == "16273849"
        assert saved["business_structure"] == "llc"
        assert saved["medicare_intent"] is True

    def test_an_empty_save_is_refused_rather_than_silently_accepted(
        self, harness: dict[str, Any]
    ) -> None:
        assert harness["client"].patch(f"{_URL}/answers", json={}).status_code == 400

    def test_the_encrypted_identifiers_are_not_reachable_here(
        self, harness: dict[str, Any]
    ) -> None:
        """SSN, DOB and tax id have one way in, and it audits the write."""
        response = harness["client"].patch(f"{_URL}/answers", json={"ssn": "123-45-6789"})

        assert response.status_code == 400
        assert "123-45-6789" not in response.text


class TestOneCliniciansIntakeIsHerOwn:
    def test_another_clinicians_confirmations_are_not_returned(
        self, harness: dict[str, Any]
    ) -> None:
        """Belt to the RLS policy's braces.

        Row-level security enforces this in Postgres; the query is scoped by
        ``user_id`` as well, so a misconfigured GUC cannot turn a read of her
        record into a read of everyone's.
        """
        confirmations.record(
            harness["session"],
            _OTHER_USER_ID,
            field_key="npi_number",
            source="nppes",
            confirmed=True,
            presented_value="someone else's",
        )
        harness["session"].commit()

        listed = harness["client"].get(f"{_URL}/confirmations").json()

        assert listed == []

    def test_another_clinicians_answers_do_not_count_as_hers(self, harness: dict[str, Any]) -> None:
        confirmations.record(
            harness["session"],
            _OTHER_USER_ID,
            field_key="exclusion_clearance",
            source="leie_sam",
            confirmed=True,
        )
        harness["session"].commit()

        fields = harness["client"].get(_URL).json()["fields"]

        assert not next(f for f in fields if f["key"] == "exclusion_clearance")["answered"]


def _answer_tier_one(harness: dict[str, Any]) -> None:
    """Fill every required Tier-1 field the way the surface would.

    Collections get one row each, the uploads a vault document, and the scalars
    go through the answers endpoint — the same paths the wizard uses, so the
    completion this produces is the completion a real sitting produces.
    """
    session: Session = harness["session"]
    now = datetime.now(UTC)

    harness["client"].patch(
        f"{_URL}/answers",
        json={"supervision_status": "independent", "business_structure": "sole_proprietor"},
    )

    government_ids.update_identifiers(
        session, _user(), AuditService(InMemoryAuditRepository()), {"dob": "1985-04-02"}
    )

    document = ComplianceDocumentRow(
        id=str(uuid.uuid4()),
        filename="coi.pdf",
        mime_type="application/pdf",
        size_bytes=1,
        storage_uri="gs://vault/coi.pdf",
        document_type="liability_certificate",
        uploaded_at=now,
        uploaded_by_user_id=_USER_ID,
    )
    session.add(document)

    session.add(
        CredentialLicenseRow(
            id=str(uuid.uuid4()),
            user_id=_USER_ID,
            license_type="LPC",
            license_number="GA-12345",
            state="GA",
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        CredentialLiabilityPolicyRow(
            id=str(uuid.uuid4()),
            user_id=_USER_ID,
            carrier_name="CPH",
            policy_number="P-1",
            document_id=document.id,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        CredentialServiceLocationRow(
            id=str(uuid.uuid4()),
            user_id=_USER_ID,
            address_line1="1 Test St",
            city="Atlanta",
            state="GA",
            postal_code="30301",
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        CredentialBankAccountRow(
            id=str(uuid.uuid4()),
            user_id=_USER_ID,
            account_holder_name="Test Therapist",
            routing_number_encrypted="ciphertext",
            account_number_encrypted="ciphertext",
            account_type="checking",
            document_id=document.id,
            created_at=now,
            updated_at=now,
        )
    )
    payer = PayerRow(
        id=str(uuid.uuid4()),
        name="Aetna",
        payer_id="60054",
        created_at=now,
        updated_at=now,
    )
    session.add(payer)
    session.add(
        PayerParticipationRow(
            id=str(uuid.uuid4()),
            user_id=_USER_ID,
            payer_id=payer.id,
            status="in_network",
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()


def test_the_tier_one_fixture_actually_covers_the_tier() -> None:
    """Guards the fixture above, which is the load-bearing part of the stop test.

    If a Tier-1 question is added and the fixture is not extended, the stop
    test would go on passing against a tier it no longer finishes. This fails
    instead, naming what is missing.
    """
    required = {f.key for f in applicable(fields_for_tier(Tier.CLAIMS_READY)) if f.required}
    covered = {
        "supervision_status",
        "business_structure",
        "date_of_birth",
        "licenses",
        "liability_policy",
        "liability_certificate",
        "service_locations",
        "payer_participation",
    }
    assert required == covered, f"Tier 1 changed; extend _answer_tier_one: {required ^ covered}"


class TestTierZeroShowsWhatItIsAskingAbout:
    """A confirm card has to carry the value it wants confirmed.

    The tier shipped able to record an answer and unable to show the question:
    ``GET /checklist`` returned provenance and no value, so every card rendered
    "Nothing on file" even for an NPI sitting on the profile, and she was asked
    to agree with a blank.

    Bug classes covered:
      * a value the record holds not reaching the card;
      * an unset column and an empty string rendering identically, so "nothing
        on file" and "confirmed as empty" become indistinguishable;
      * the card showing what she confirmed last year rather than what the
        column says today, which hides exactly the divergence this tier is for;
      * the other tiers picking up a pre-filled value and turning a question
        into a confirmation.
    """

    def _profile(self, session: Session, **columns: Any) -> None:
        session.add(
            ClinicianProfileRow(
                user_id=_USER_ID,
                practice_id="practice-1",
                joined_at=datetime.now(UTC),
                **columns,
            )
        )
        session.commit()

    def _billing_profile(self, session: Session, **columns: Any) -> None:
        now = datetime.now(UTC)
        session.add(PracticeBillingProfileRow(id=1, created_at=now, updated_at=now, **columns))
        session.commit()

    def _field(self, client: TestClient, key: str) -> Any:
        return next(f for f in client.get(_URL).json()["fields"] if f["key"] == key)

    def test_an_npi_on_the_profile_reaches_the_card(self, harness: dict[str, Any]) -> None:
        # Acceptance 1, and the whole bug: nothing was confirmed first.
        self._profile(harness["session"], npi_number="1999999984")

        assert self._field(harness["client"], "npi_number")["current_value"] == "1999999984"

    def test_a_practice_value_reaches_the_card_too(self, harness: dict[str, Any]) -> None:
        # Not every Tier-0 value is the clinician's own; the practice's billing
        # identity is confirmed here as well.
        self._billing_profile(harness["session"], legal_name="Cedar Therapy PLLC")

        assert self._field(harness["client"], "practice_name")["current_value"] == (
            "Cedar Therapy PLLC"
        )

    def test_a_field_with_nothing_behind_it_says_so(self, harness: dict[str, Any]) -> None:
        assert self._field(harness["client"], "npi_number")["current_value"] is None

    def test_an_empty_column_is_nothing_on_file_rather_than_a_value(
        self, harness: dict[str, Any]
    ) -> None:
        # Acceptance 2. A blank string would render as a confirmed fact that
        # happens to look empty — she would be agreeing to nothing, twice.
        self._profile(harness["session"], npi_number="   ")

        assert self._field(harness["client"], "npi_number")["current_value"] is None

    def test_the_record_wins_over_what_she_confirmed_before(self, harness: dict[str, Any]) -> None:
        """Acceptance 3: a column that has since changed must be visible.

        Showing the previously-confirmed value instead would mean she could
        never be told the record moved underneath her — which is the one thing
        a confirm tier exists to catch.
        """
        self._profile(harness["session"], npi_number="1999999984")
        _confirm(harness["client"], "npi_number", presented_value="1000000004")

        assert self._field(harness["client"], "npi_number")["current_value"] == "1999999984"

    def test_a_field_with_no_home_column_falls_back_to_what_she_was_shown(
        self, harness: dict[str, Any]
    ) -> None:
        # legal_name and the three yes/no confirmations have no column
        # anywhere; the confirmation row IS the value for those.
        _confirm(harness["client"], "legal_name", presented_value="Test Therapist")

        assert self._field(harness["client"], "legal_name")["current_value"] == "Test Therapist"

    def test_the_questions_are_not_given_answers(self, harness: dict[str, Any]) -> None:
        # Tier 1 and Tier 2 ask; only Tier 0 confirms. A value appearing on a
        # question would turn typing into agreeing.
        fields = harness["client"].get(_URL).json()["fields"]

        assert all(f["current_value"] is None for f in fields if f["tier"] != Tier.CONFIRM.value)

    def test_one_request_carries_the_whole_tier(self, harness: dict[str, Any]) -> None:
        # Acceptance 5. Fourteen cards must not mean fourteen round trips.
        self._profile(
            harness["session"],
            npi_number="1999999984",
            taxonomy_code="101YM0800X",
            license_number="LPC-4417",
            license_state="NC",
        )
        self._billing_profile(
            harness["session"], legal_name="Cedar Therapy PLLC", address_line1="14 Mill Street"
        )

        fields = harness["client"].get(_URL).json()["fields"]
        values = {f["key"]: f["current_value"] for f in fields if f["tier"] == Tier.CONFIRM.value}

        assert values["npi_number"] == "1999999984"
        assert values["taxonomy_code"] == "101YM0800X"
        assert values["primary_license"] == "LPC-4417"
        assert values["primary_license_state"] == "NC"
        assert values["practice_name"] == "Cedar Therapy PLLC"
        assert values["practice_address"] == "14 Mill Street"
