# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for recording money the practice took itself.

``POST /api/patients/{id}/payments`` exists because a practice that does not
keep clients' cards on file could not record being paid at all. So the tests
that matter most are not about the route's shape but about the consequence:
the client's balance clears, and the statement stops saying they paid nothing.

Covered here:

* a recorded cheque clears the balance it pays off — the actual bug;
* the method must be one of the fixed set the CHECK constraint enforces;
* ``card`` is refused, because a card row has to have a processor behind it;
* ``other`` without a reference is refused, so no row lands unaccountable;
* overpayment is allowed and shows as a credit, rather than being clamped;
* the audit event names the method and amount but never the reference text;
* an unseen client is 404, never 403.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models.payments import PatientCharge
from app.payments.balance import patient_balance
from app.repositories.audit import InMemoryAuditRepository
from app.services import AuditService

from tests.test_patient_write_offs import (
    _PATIENT_ID,
    _USER_ID,
    _client,
    _FakePatients,
    _FakePayments,
)

_PAYMENTS_URL = f"/api/patients/{_PATIENT_ID}/payments"


def _session_row(amount_cents: int) -> PatientCharge:
    """An unpaid self-pay visit: billed, and nothing collected against it.

    ``status='pending'`` is what an un-charged session looks like on the
    ledger — the row is written when the visit is billed, and only a
    processor moves it to ``succeeded``. That is precisely the state a
    practice taking cheques is permanently stuck in today.
    """
    return PatientCharge(
        id="session-1",
        patient_id=_PATIENT_ID,
        kind="session",
        amount_cents=amount_cents,
        currency="usd",
        status="pending",
        method="card",
        created_by_user_id=_USER_ID,
        created_at=datetime.now(UTC),
    )


def test_recorded_cheque_clears_the_balance() -> None:
    """The bug, stated as arithmetic.

    Before this route existed the session below could be billed and never
    settled, because the only writer of a collecting row demanded a card on
    file. The balance is what a statement reads, so a balance that never
    clears is a statement telling a client they owe money they have paid.
    """
    payments = _FakePayments([_session_row(15_000)])
    client, _ = _client(payments, _FakePatients())

    assert patient_balance(payments.charges).balance_cents == 15_000

    response = client.post(
        _PAYMENTS_URL,
        json={"amount_cents": 15_000, "method": "check", "reference": "1042"},
    )

    assert response.status_code == 200
    assert patient_balance(payments.charges).balance_cents == 0


def test_recorded_payment_is_final_on_insert() -> None:
    """No processor was called, so there is nothing to wait for.

    A ``pending`` row here would be a payment that never lands: nothing will
    ever arrive to close it, and ``pending`` does not count as collected.
    """
    payments = _FakePayments()
    client, _ = _client(payments, _FakePatients())

    response = client.post(_PAYMENTS_URL, json={"amount_cents": 5_000, "method": "cash"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["kind"] == "payment"
    assert body["method"] == "cash"


def test_overpayment_is_allowed_and_reads_as_a_credit() -> None:
    """A client may pay more than they owe, and the ledger must say so.

    Clamping to the balance would make the ledger disagree with the bank.
    The balance is allowed to go negative — the practice owes a refund.
    """
    payments = _FakePayments([_session_row(10_000)])
    client, _ = _client(payments, _FakePatients())

    response = client.post(_PAYMENTS_URL, json={"amount_cents": 15_000, "method": "cash"})

    assert response.status_code == 200
    assert patient_balance(payments.charges).balance_cents == -5_000


def test_payment_ahead_of_any_bill_is_allowed() -> None:
    """Paying for a block of sessions up front is ordinary, not an error."""
    payments = _FakePayments()
    client, _ = _client(payments, _FakePatients())

    response = client.post(
        _PAYMENTS_URL, json={"amount_cents": 60_000, "method": "check", "reference": "1043"}
    )

    assert response.status_code == 200
    assert patient_balance(payments.charges).balance_cents == -60_000


def test_unknown_method_is_refused() -> None:
    payments = _FakePayments()
    client, _ = _client(payments, _FakePatients())

    response = client.post(_PAYMENTS_URL, json={"amount_cents": 5_000, "method": "bitcoin"})

    assert response.status_code == 422
    assert payments.charges == []


def test_card_is_refused_because_it_needs_a_processor() -> None:
    """The one method with a real path is the one this route will not fake.

    A hand-written card row would read identically on a statement while
    having no charge, no fee and nothing to reconcile against.
    """
    payments = _FakePayments()
    client, _ = _client(payments, _FakePatients())

    response = client.post(_PAYMENTS_URL, json={"amount_cents": 5_000, "method": "card"})

    assert response.status_code == 422
    assert "card on file" in response.json()["detail"]
    assert payments.charges == []


def test_other_without_a_reference_is_refused() -> None:
    """An unlabelled 'other' is the row nobody can account for later."""
    payments = _FakePayments()
    client, _ = _client(payments, _FakePatients())

    response = client.post(_PAYMENTS_URL, json={"amount_cents": 5_000, "method": "other"})

    assert response.status_code == 422
    assert payments.charges == []


def test_other_with_a_reference_is_accepted() -> None:
    payments = _FakePayments()
    client, _ = _client(payments, _FakePatients())

    response = client.post(
        _PAYMENTS_URL,
        json={"amount_cents": 5_000, "method": "other", "reference": "Zelle 14 Mar"},
    )

    assert response.status_code == 200
    assert response.json()["payment_reference"] == "Zelle 14 Mar"


@pytest.mark.parametrize("method", ["cash", "check"])
def test_cash_and_cheque_need_no_reference(method: str) -> None:
    """Only ``other`` is held to a reference — a cheque number is useful, not
    required, and cash has nothing to reference."""
    payments = _FakePayments()
    client, _ = _client(payments, _FakePatients())

    response = client.post(_PAYMENTS_URL, json={"amount_cents": 5_000, "method": method})

    assert response.status_code == 200


def test_audit_names_the_method_but_never_the_reference() -> None:
    """The reference is free text a clinician can type anything into.

    The audit trail records that money was recorded, by whom, how much and by
    what means. What the clinician typed to find it again is not part of that
    and must not be copied into an audit payload.
    """
    payments = _FakePayments()
    audit = AuditService(InMemoryAuditRepository())
    client, audit_service = _client(payments, _FakePatients(), audit=audit)

    response = client.post(
        _PAYMENTS_URL,
        json={
            "amount_cents": 5_000,
            "method": "other",
            "reference": "her husband's account",
        },
    )
    assert response.status_code == 200

    logged = audit_service._repo.list_for_user(_USER_ID)
    entry = next(e for e in logged if e.action == "patient_payment_recorded")
    assert entry.resource_id == _PATIENT_ID
    assert entry.changes["method"] == "other"
    assert entry.changes["amount_cents"] == 5_000
    # The reference is free text. It is not in the payload, and no value in
    # the payload carries what the clinician typed.
    assert "reference" not in entry.changes
    assert "husband" not in str(entry.changes)


def test_unseen_client_is_404() -> None:
    """404 and not 403, matching every other route in this family: whether a
    client exists is itself a disclosure."""
    payments = _FakePayments()
    client, _ = _client(payments, _FakePatients(visible=False))

    response = client.post(_PAYMENTS_URL, json={"amount_cents": 5_000, "method": "cash"})

    assert response.status_code == 404
    assert payments.charges == []
