# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Enrolling the practice with payers (``app.claims.enrollment``).

The lifecycle runs against a real SQLAlchemy session over in-memory SQLite
with the four tables it touches, and a clearinghouse that answers from the
recorded fixtures — the vendor's enrollment API refuses test-mode keys, so
recorded answers are the only way this flow is exercised outside production.

What is pinned down:

* the provider record is created once, from a complete billing profile,
  with the practice's inbox as its contact, and never again;
* filing follows the payer directory: remittance always, claims and
  eligibility only when the directory says they need enrolling;
* a second request for the same payer files nothing new;
* ``payers.enrollment_status`` is derived from the set and is ``active``
  only once remittance is live and claims are live or never needed a request;
* a request moving into ``provider_action_required`` writes one compliance
  reminder carrying the clearinghouse's instructions; moving on resolves it;
* the refresh is bounded and re-arms the session per request owner;
* the instructions text is stored and shown, never logged.
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.claims import enrollment
from app.claims.enrollment import (
    BillingProfileIncompleteError,
    PayerNotInDirectoryError,
    derive_payer_status,
    ensure_provider_record,
    refresh_enrollments,
    request_enrollments,
    required_transactions,
    sync_provider_record,
)
from app.claims.events import compliance_item_type
from app.db.models import (
    ComplianceItemRow,
    PayerEnrollmentRow,
    PayerRow,
    PracticeBillingProfileRow,
)
from app.models.claims_transport import Enrollment
from app.services.practice_billing_profile import SINGLETON_ID, update_billing_profile
from app.settings import get_settings
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.enrollment_fakes import (
    INSTRUCTIONS,
    PROVIDER_ID,
    TEST_PAYER_ID,
    TEST_PAYER_STEDI_ID,
    FakeClearinghouse,
    enrollment_fixture,
)
from tests.sqlite_engine import sqlite_engine

if TYPE_CHECKING:
    from collections.abc import Iterator

_USER_ID = "11111111-1111-4111-8111-111111111111"
_OTHER_USER_ID = "22222222-2222-4222-8222-222222222222"
_PAYER_ROW_ID = "33333333-3333-4333-8333-333333333333"
_NOW = datetime(2026, 9, 6, 15, 30, tzinfo=UTC)

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

_TABLES = (
    PracticeBillingProfileRow.__table__,
    PayerRow.__table__,
    PayerEnrollmentRow.__table__,
    ComplianceItemRow.__table__,
)


def _no_arm(_session: Session, _user_id: str) -> None:
    """SQLite has no RLS GUC to arm."""


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine() -> Iterator[Engine]:
    with sqlite_engine(_TABLES) as eng:
        yield eng


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def _seed_profile(session: Session, **overrides: str | None) -> None:
    update_billing_profile(session, {**_PROFILE, **overrides})


def _seed_payer(session: Session, *, payer_id: str = TEST_PAYER_ID) -> PayerRow:
    payer = PayerRow(
        id=_PAYER_ROW_ID,
        name="Stedi Test Payer",
        payer_id=payer_id,
        created_at=_NOW,
        updated_at=_NOW,
    )
    session.add(payer)
    session.flush()
    return payer


def _rows(session: Session) -> dict[str, PayerEnrollmentRow]:
    return {
        row.transaction_type: row
        for row in session.execute(select(PayerEnrollmentRow)).scalars().all()
    }


def _reminders(session: Session) -> list[ComplianceItemRow]:
    return list(session.execute(select(ComplianceItemRow)).scalars().all())


def _listing_for(session: Session, status: str, *transaction_types: str) -> list[dict]:
    rows = _rows(session)
    return [
        enrollment_fixture(vendor_id=rows[tx].vendor_request_id, status=status)
        for tx in transaction_types
    ]


# --- the provider record ------------------------------------------------------


class TestProviderRecord:
    def test_created_once_from_a_complete_profile(self, session: Session) -> None:
        _seed_profile(session)
        client = FakeClearinghouse()

        first = ensure_provider_record(session, client)
        second = ensure_provider_record(session, client)

        assert first == second == PROVIDER_ID
        assert session.get(PracticeBillingProfileRow, SINGLETON_ID).clearinghouse_provider_id == (
            PROVIDER_ID
        )
        assert len(client.calls_named("create_provider")) == 1

    def test_registration_carries_the_practice_not_a_clinician(self, session: Session) -> None:
        _seed_profile(session)
        client = FakeClearinghouse()

        ensure_provider_record(session, client)

        [registration] = client.calls_named("create_provider")
        assert registration.taxId == "844459714"
        assert registration.taxIdType == "EIN"
        assert registration.npi == "1999999984"
        [contact] = registration.contacts
        assert contact.email == "billing@example.com"
        assert contact.organizationName == "Pablo Health Test Provider"

    def test_incomplete_profile_names_what_is_missing(self, session: Session) -> None:
        _seed_profile(session, contact_email=None, billing_npi=None)

        with pytest.raises(BillingProfileIncompleteError) as excinfo:
            ensure_provider_record(session, FakeClearinghouse())

        assert excinfo.value.missing == ("billing_npi", "contact_email")

    def test_no_profile_at_all_is_incomplete(self, session: Session) -> None:
        with pytest.raises(BillingProfileIncompleteError) as excinfo:
            ensure_provider_record(session, FakeClearinghouse())

        assert "tax_id" in excinfo.value.missing


class TestSyncOnProfileSave:
    @pytest.fixture(autouse=True)
    def clearinghouse(self) -> Iterator[FakeClearinghouse]:
        client = FakeClearinghouse()
        enrollment.register_clearinghouse_client_factory(lambda _practice_id: client)
        yield client
        enrollment.register_clearinghouse_client_factory(None)

    def test_registers_when_the_save_completes_the_profile(
        self, session: Session, clearinghouse: FakeClearinghouse
    ) -> None:
        _seed_profile(session)

        assert sync_provider_record(session, "practice-1") == PROVIDER_ID
        assert len(clearinghouse.calls_named("create_provider")) == 1

    def test_quiet_while_the_profile_is_incomplete(
        self, session: Session, clearinghouse: FakeClearinghouse
    ) -> None:
        _seed_profile(session, contact_email=None)

        assert sync_provider_record(session, "practice-1") is None
        assert clearinghouse.calls == []

    def test_quiet_without_a_clearinghouse(self, session: Session) -> None:
        enrollment.register_clearinghouse_client_factory(lambda _practice_id: None)
        _seed_profile(session)

        assert sync_provider_record(session, "practice-1") is None


# --- filing ---------------------------------------------------------------------


class TestRequestEnrollments:
    def test_files_what_the_directory_requires(self, session: Session) -> None:
        """The recorded directory: remittance needs enrolling, claims and eligibility do not."""
        _seed_profile(session)
        payer = _seed_payer(session)
        client = FakeClearinghouse()

        created = request_enrollments(
            session, client, payer_row_id=payer.id, user_id=_USER_ID, now=_NOW
        )

        assert [row.transaction_type for row in created] == ["835"]
        [row] = created
        assert row.status == "stedi_action_required"
        assert row.vendor_request_id == "enr-0001"
        assert row.requested_by_user_id == _USER_ID
        assert payer.enrollment_status == "filed"
        assert payer.clearinghouse_payer_id == TEST_PAYER_STEDI_ID

    def test_request_names_the_transaction_and_the_practice_inbox(self, session: Session) -> None:
        _seed_profile(session)
        payer = _seed_payer(session)
        client = FakeClearinghouse()

        request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)

        [request] = client.calls_named("create_enrollment")
        assert request.provider.id == PROVIDER_ID
        assert request.payer.idOrAlias == TEST_PAYER_STEDI_ID
        assert request.transactions.claimPayment is not None
        assert request.transactions.claimPayment.enroll is True
        assert request.transactions.professionalClaimSubmission is None
        assert request.userEmail == "billing@example.com"
        assert request.primaryContact.email == "billing@example.com"
        assert request.status == "STEDI_ACTION_REQUIRED"

    def test_claims_and_eligibility_when_the_directory_says_so(self, session: Session) -> None:
        _seed_profile(session)
        payer = _seed_payer(session)
        client = FakeClearinghouse(
            transaction_support={
                "professionalClaimSubmission": "ENROLLMENT_REQUIRED",
                "eligibilityCheck": "ENROLLMENT_REQUIRED",
                "claimPayment": "ENROLLMENT_REQUIRED",
            }
        )

        created = request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)

        assert [row.transaction_type for row in created] == ["837P", "270", "835"]
        assert len({row.vendor_request_id for row in created}) == 3

    def test_a_second_request_files_nothing_new(self, session: Session) -> None:
        _seed_profile(session)
        payer = _seed_payer(session)
        client = FakeClearinghouse()
        request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)

        again = request_enrollments(session, client, payer_row_id=payer.id, user_id=_OTHER_USER_ID)

        assert again == []
        assert len(client.calls_named("create_enrollment")) == 1
        assert len(_rows(session)) == 1

    def test_a_payer_the_directory_does_not_know_is_refused(self, session: Session) -> None:
        _seed_profile(session)
        payer = _seed_payer(session, payer_id="99999")

        with pytest.raises(PayerNotInDirectoryError):
            request_enrollments(
                session, FakeClearinghouse(), payer_row_id=payer.id, user_id=_USER_ID
            )
        assert _rows(session) == {}

    def test_a_payer_typed_without_an_id_is_refused_before_any_call(self, session: Session) -> None:
        _seed_profile(session)
        payer = _seed_payer(session, payer_id="UNKNOWN")
        client = FakeClearinghouse()

        with pytest.raises(PayerNotInDirectoryError):
            request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)
        assert client.calls == []

    def test_incomplete_profile_files_nothing(self, session: Session) -> None:
        _seed_profile(session, phone=None)
        payer = _seed_payer(session)
        client = FakeClearinghouse()

        with pytest.raises(BillingProfileIncompleteError):
            request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)
        assert client.calls == []

    def test_a_request_already_needing_action_writes_the_reminder_at_once(
        self, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_profile(session)
        payer = _seed_payer(session)
        client = FakeClearinghouse()
        monkeypatch.setattr(
            client,
            "create_enrollment",
            lambda _request: Enrollment.model_validate(
                enrollment_fixture(vendor_id="enr-par", status="PROVIDER_ACTION_REQUIRED")
            ),
        )

        [row] = request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)

        assert row.status == "provider_action_required"
        [reminder] = _reminders(session)
        assert reminder.user_id == _USER_ID
        assert INSTRUCTIONS in (reminder.notes or "")


class TestEnrollIfNew:
    @pytest.fixture(autouse=True)
    def clearinghouse(self) -> Iterator[FakeClearinghouse]:
        client = FakeClearinghouse()
        enrollment.register_clearinghouse_client_factory(lambda _practice_id: client)
        yield client
        enrollment.register_clearinghouse_client_factory(None)

    def test_files_for_a_payer_with_nothing_on_file(
        self, session: Session, clearinghouse: FakeClearinghouse
    ) -> None:
        _seed_profile(session)
        payer = _seed_payer(session)

        enrollment.enroll_if_new(session, "practice-1", payer_row_id=payer.id, user_id=_USER_ID)

        assert set(_rows(session)) == {"835"}

    def test_leaves_a_payer_with_requests_alone(
        self, session: Session, clearinghouse: FakeClearinghouse
    ) -> None:
        _seed_profile(session)
        payer = _seed_payer(session)
        enrollment.enroll_if_new(session, "practice-1", payer_row_id=payer.id, user_id=_USER_ID)

        enrollment.enroll_if_new(session, "practice-1", payer_row_id=payer.id, user_id=_USER_ID)

        assert len(clearinghouse.calls_named("create_enrollment")) == 1

    def test_never_raises_for_an_incomplete_profile(
        self, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        _seed_profile(session, contact_email=None)
        payer = _seed_payer(session)

        with caplog.at_level(logging.INFO):
            enrollment.enroll_if_new(session, "practice-1", payer_row_id=payer.id, user_id=_USER_ID)

        assert _rows(session) == {}
        assert "payer_enrollment_skipped_profile_incomplete" in caplog.text


# --- the mirror ------------------------------------------------------------------


class TestPayerStatusMirror:
    @pytest.mark.parametrize(
        ("requests", "expected"),
        [
            ((), "none"),
            ((("835", "stedi_action_required"),), "filed"),
            ((("835", "draft"), ("837P", "canceled")), "filed"),
            ((("835", "provisioning"),), "pending"),
            ((("835", "provider_action_required"),), "pending"),
            ((("835", "live"), ("837P", "provisioning")), "pending"),
            ((("835", "provisioning"), ("837P", "live")), "pending"),
            ((("835", "live"), ("837P", "live")), "active"),
            ((("835", "live"), ("837P", "live"), ("270", "provisioning")), "active"),
            ((("835", "live"),), "active"),
            ((("835", "live"), ("837P", "rejected")), "error"),
            ((("835", "rejected"), ("837P", "live")), "error"),
        ],
    )
    def test_derived_from_the_set(
        self, requests: tuple[tuple[str, str], ...], expected: str
    ) -> None:
        assert derive_payer_status(requests) == expected

    def test_active_only_once_both_claims_and_remittance_are_live(self, session: Session) -> None:
        _seed_profile(session)
        payer = _seed_payer(session)
        client = FakeClearinghouse(
            transaction_support={
                "professionalClaimSubmission": "ENROLLMENT_REQUIRED",
                "claimPayment": "ENROLLMENT_REQUIRED",
            }
        )
        request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)

        client.listing = [
            *_listing_for(session, "LIVE", "835"),
            *_listing_for(session, "PROVISIONING", "837P"),
        ]
        refresh_enrollments(session, client, arm=_no_arm)
        assert payer.enrollment_status == "pending"

        client.listing = _listing_for(session, "LIVE", "835", "837P")
        refresh_enrollments(session, client, arm=_no_arm)
        assert payer.enrollment_status == "active"

    @pytest.mark.parametrize(
        ("support", "expected"),
        [
            ({}, ["835"]),
            ({"claimPayment": "ENROLLMENT_REQUIRED"}, ["835"]),
            ({"claimPayment": "NOT_SUPPORTED"}, []),
            ({"professionalClaimSubmission": "SUPPORTED", "claimPayment": "SUPPORTED"}, []),
            (
                {
                    "professionalClaimSubmission": "ENROLLMENT_REQUIRED",
                    "eligibilityCheck": "SUPPORTED",
                },
                ["837P", "835"],
            ),
        ],
    )
    def test_required_transactions_follow_the_directory(
        self, support: dict[str, str], expected: list[str]
    ) -> None:
        assert required_transactions(support) == expected


# --- action required -> reminder -> resolved -------------------------------------


class TestActionRequired:
    def _filed(self, session: Session) -> tuple[PayerRow, FakeClearinghouse]:
        _seed_profile(session)
        payer = _seed_payer(session)
        client = FakeClearinghouse()
        request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)
        return payer, client

    def test_moving_into_action_required_writes_one_reminder_with_the_instructions(
        self, session: Session
    ) -> None:
        payer, client = self._filed(session)
        client.listing = _listing_for(session, "PROVIDER_ACTION_REQUIRED", "835")

        assert refresh_enrollments(session, client, arm=_no_arm) == 1
        assert refresh_enrollments(session, client, arm=_no_arm) == 0

        row = _rows(session)["835"]
        assert row.status == "provider_action_required"
        assert row.instructions is not None
        assert INSTRUCTIONS in row.instructions
        assert "EFT authorization form: https://example.com" in row.instructions
        assert "signed EFT authorization" in row.instructions
        assert "Forward the signed form" not in row.instructions  # the vendor's own task
        [reminder] = _reminders(session)
        assert reminder.user_id == _USER_ID
        assert reminder.item_type == compliance_item_type("enrollment_action_required")
        assert INSTRUCTIONS in (reminder.notes or "")
        assert reminder.completed_at is None
        assert payer.enrollment_status == "pending"

    def test_moving_on_resolves_the_reminder(self, session: Session) -> None:
        payer, client = self._filed(session)
        client.listing = _listing_for(session, "PROVIDER_ACTION_REQUIRED", "835")
        refresh_enrollments(session, client, arm=_no_arm)

        client.listing = _listing_for(session, "LIVE", "835")
        refresh_enrollments(session, client, arm=_no_arm)

        row = _rows(session)["835"]
        assert row.status == "live"
        assert row.instructions is None
        [reminder] = _reminders(session)
        assert reminder.completed_at is not None
        assert payer.enrollment_status == "active"

    def test_a_rejection_is_an_error_on_the_payer(self, session: Session) -> None:
        payer, client = self._filed(session)
        client.listing = _listing_for(session, "REJECTED", "835")

        refresh_enrollments(session, client, arm=_no_arm)

        assert payer.enrollment_status == "error"
        assert _rows(session)["835"].status == "rejected"

    def test_instructions_are_stored_and_never_logged(
        self, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        _, client = self._filed(session)
        client.listing = _listing_for(session, "PROVIDER_ACTION_REQUIRED", "835")

        with caplog.at_level(logging.DEBUG):
            refresh_enrollments(session, client, arm=_no_arm)

        assert _rows(session)["835"].instructions is not None
        assert "EFT" not in caplog.text
        assert "example.com" not in caplog.text


# --- the refresh ------------------------------------------------------------------


class TestRefresh:
    def test_bounded_and_armed_per_request_owner(self, session: Session) -> None:
        _seed_profile(session)
        payer = _seed_payer(session)
        client = FakeClearinghouse(
            transaction_support={
                "professionalClaimSubmission": "ENROLLMENT_REQUIRED",
                "claimPayment": "ENROLLMENT_REQUIRED",
            }
        )
        request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)
        client.listing = _listing_for(session, "LIVE", "835", "837P")
        armed: list[str] = []

        changed = refresh_enrollments(
            session, client, limit=1, arm=lambda _s, user_id: armed.append(user_id)
        )

        assert changed == 1
        assert armed == [_USER_ID]
        assert sorted(row.status for row in _rows(session).values()) == [
            "live",
            "stedi_action_required",
        ]

    def test_one_listing_call_per_pass_and_none_with_nothing_open(self, session: Session) -> None:
        _seed_profile(session)
        payer = _seed_payer(session)
        client = FakeClearinghouse()
        request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)
        client.listing = _listing_for(session, "LIVE", "835")

        refresh_enrollments(session, client, arm=_no_arm)
        refresh_enrollments(session, client, arm=_no_arm)

        assert len(client.calls_named("list_enrollments")) == 1

    def test_a_request_the_listing_does_not_mention_is_left_alone(self, session: Session) -> None:
        _seed_profile(session)
        payer = _seed_payer(session)
        client = FakeClearinghouse()
        request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)
        client.listing = [enrollment_fixture(vendor_id="someone-elses", status="LIVE")]

        assert refresh_enrollments(session, client, arm=_no_arm) == 0
        assert _rows(session)["835"].status == "stedi_action_required"

    def test_an_unrecognised_vendor_status_is_skipped(
        self, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        _seed_profile(session)
        payer = _seed_payer(session)
        client = FakeClearinghouse()
        request_enrollments(session, client, payer_row_id=payer.id, user_id=_USER_ID)
        client.listing = _listing_for(session, "SOMETHING_NEW", "835")

        with caplog.at_level(logging.WARNING):
            assert refresh_enrollments(session, client, arm=_no_arm) == 0

        assert "payer_enrollment_status_unrecognised" in caplog.text
