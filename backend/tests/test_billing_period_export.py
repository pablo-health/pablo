# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the practice's period export.

Covers ``app.payments.period_export`` and the route that streams it,
``GET /api/billing/export``:

* both CSVs are byte-identical across two exports of the same window, so a
  practice can diff a re-export against the copy it filed away;
* the window includes both of its ends and a row sitting on either boundary
  comes out exactly once — for claims by service date, for ledger rows by the
  practice-local day they were recorded on;
* a window with nothing in it is a header-only file and a 200, never a 404,
  and it is audited like any other export;
* the audit row names the window and the row count, because the disclosure is
  a whole period rather than one record, and carries nothing off an insurance
  card;
* the files carry client ids and not names, no member id, no diagnosis and
  none of the free text a clinician wrote on a ledger row;
* the money columns come from the same arithmetic a client's balance does,
  and a figure nobody has stated is blank rather than zero;
* nothing materialises: the renderer and the route both drain a two-hundred-
  thousand-row ledger a chunk at a time.

Hermetic: in-process repositories, the claims fixture seeded directly.
"""

from __future__ import annotations

import asyncio
import csv
import json
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import require_baa_acceptance
from app.models import User
from app.models.payments import PatientCharge
from app.models.user import UserPreferences
from app.payments.period_export import (
    CHUNK_BYTES,
    CLAIM_COLUMNS,
    PAYMENT_COLUMNS,
    ExportTally,
    stream_payments_csv,
)
from app.repositories import (
    get_claim_repository,
    get_patient_payment_repository,
    get_user_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.claims import InMemoryClaimRepository
from app.routes import billing_export
from app.services import AuditService, get_audit_service
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tests.claims_fixtures import PATIENT_ID, USER_ID, claim, line

if TYPE_CHECKING:
    from collections.abc import Iterator

_NOW = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
_TZ_NAME = "America/New_York"
_TZ = ZoneInfo(_TZ_NAME)
_MEMBER_ID = "123456789"
_DOB = "2000-01-01"
_DX = "F41.1"
_APPOINTMENT_ID = "44444444-4444-4444-8444-444444444444"

# September 2026 in the practice's own timezone.
_WINDOW = {"from": "2026-09-01", "to": "2026-09-30"}
_WINDOW_OPENS = datetime(2026, 9, 1, 4, 0, tzinfo=UTC)  # 2026-09-01 00:00 in New York
_WINDOW_CLOSES = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)  # 2026-10-01 00:00 in New York


def _user(user_id: str = USER_ID) -> User:
    return User(
        id=user_id,
        email="therapist@example.com",
        name="Jane Smith",
        legal_name="Jane Smith",
        created_at=_NOW,
        baa_accepted_at=_NOW,
        baa_version="2024-01-01",
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeUsers:
    """Just enough user repository: everyone keeps the practice's timezone."""

    def get_preferences(self, user_id: str) -> UserPreferences:
        return UserPreferences(timezone=_TZ_NAME)


class _FakePayments:
    """A charge ledger held in a list, read the way the export reads it."""

    def __init__(self, charges: list[PatientCharge] | None = None) -> None:
        self.charges = list(charges or [])
        self.yielded = 0

    def iter_ledger_for_period(self, *, start: datetime, end: datetime) -> Iterator[PatientCharge]:
        selected = sorted(
            (c for c in self.charges if start <= c.created_at < end),
            key=lambda c: (c.created_at, c.id),
        )
        for charge in selected:
            self.yielded += 1
            yield charge


class _EndlessPayments:
    """A ledger far larger than memory, generated one row at a time.

    Nothing is ever held: the export either pulls a row or it does not, and
    :attr:`yielded` is the count of rows that have actually been produced when
    a test stops reading.
    """

    def __init__(self, rows: int) -> None:
        self.rows = rows
        self.yielded = 0

    def iter_ledger_for_period(self, *, start: datetime, end: datetime) -> Iterator[PatientCharge]:
        for index in range(self.rows):
            self.yielded += 1
            yield _charge(
                id=f"charge-{index:08d}",
                created_at=start + timedelta(seconds=index % 3600),
            )


def _charge(**overrides: Any) -> PatientCharge:
    fields: dict[str, Any] = {
        "id": "charge-1",
        "patient_id": PATIENT_ID,
        "appointment_id": _APPOINTMENT_ID,
        "kind": "session",
        "amount_cents": 15000,
        "currency": "usd",
        "status": "succeeded",
        "created_by_user_id": USER_ID,
        "created_at": datetime(2026, 9, 15, 17, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return PatientCharge(**fields)


@pytest.fixture
def harness() -> dict[str, Any]:
    claims = InMemoryClaimRepository()
    payments = _FakePayments()
    audit_repo = InMemoryAuditRepository()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(billing_export.router)
    app.dependency_overrides[require_baa_acceptance] = _user
    app.dependency_overrides[get_claim_repository] = lambda: claims
    app.dependency_overrides[get_patient_payment_repository] = lambda: payments
    app.dependency_overrides[get_user_repository] = _FakeUsers
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)
    return {
        "client": TestClient(app, raise_server_exceptions=False),
        "claims": claims,
        "payments": payments,
        "audit": audit_repo,
    }


def _seed_claim(harness: dict[str, Any], **overrides: Any) -> str:
    fields: dict[str, Any] = {"state": "submitted"}
    fields.update(overrides)
    seeded = claim(**fields)
    harness["claims"].create(seeded)
    return seeded.id


def _export(harness: dict[str, Any], kind: str, **window: str) -> Any:
    params = {**_WINDOW, **window, "kind": kind}
    return harness["client"].get("/api/billing/export", params=params)


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(StringIO(text)))


def _audit_rows(harness: dict[str, Any]) -> list[Any]:
    return [row for row in harness["audit"]._entries if row.user_id == USER_ID]


# ---------------------------------------------------------------------------
# The claims file
# ---------------------------------------------------------------------------


class TestClaims:
    def test_one_row_per_claim_with_the_money_the_payer_left(self, harness: dict[str, Any]) -> None:
        _seed_claim(
            harness,
            state="paid",
            total_paid_cents=11000,
            submitted_at=datetime(2026, 9, 3, 14, 0, tzinfo=UTC),
            adjudicated_at=datetime(2026, 9, 20, 2, 0, tzinfo=UTC),
            lines=[line(allowed_cents=13000, paid_cents=11000, patient_resp_cents=2000)],
        )
        resp = _export(harness, "claims")

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/csv")
        assert resp.headers["content-disposition"] == (
            'attachment; filename="claims-2026-09-01-to-2026-09-30.csv"'
        )
        assert next(csv.reader(StringIO(resp.text))) == list(CLAIM_COLUMNS)
        (row,) = _rows(resp.text)
        assert row["control_number"] == "88659891"
        assert row["client_id"] == PATIENT_ID
        assert row["payer_name"] == "Stedi Test Payer"
        assert row["first_service_date"] == "2026-09-01"
        assert row["last_service_date"] == "2026-09-01"
        assert row["state"] == "paid"
        assert row["billed"] == "150.00"
        assert row["allowed"] == "130.00"
        assert row["paid"] == "110.00"
        assert row["adjusted"] == "20.00"
        assert row["patient_responsibility"] == "20.00"
        assert row["filed_date"] == "2026-09-03"
        # 2026-09-20 02:00 UTC is still the 19th where the practice is.
        assert row["adjudicated_date"] == "2026-09-19"

    def test_a_figure_nobody_has_stated_is_blank_not_zero(self, harness: dict[str, Any]) -> None:
        _seed_claim(harness)
        (row,) = _rows(_export(harness, "claims").text)
        assert row["billed"] == "150.00"
        assert row["paid"] == "0.00"
        assert row["allowed"] == ""
        assert row["adjusted"] == ""
        assert row["patient_responsibility"] == ""

    def test_the_span_of_service_dates_covers_every_line(self, harness: dict[str, Any]) -> None:
        _seed_claim(
            harness,
            lines=[
                line(id="l2", line_number=2, service_date=date(2026, 9, 14)),
                line(id="l1", line_number=1, service_date=date(2026, 9, 2)),
            ],
        )
        (row,) = _rows(_export(harness, "claims").text)
        assert (row["first_service_date"], row["last_service_date"]) == (
            "2026-09-02",
            "2026-09-14",
        )

    def test_a_draft_is_left_out(self, harness: dict[str, Any]) -> None:
        _seed_claim(harness)
        _seed_claim(harness, id="draft", control_number="DRAFT1", state="draft")
        assert [r["control_number"] for r in _rows(_export(harness, "claims").text)] == ["88659891"]

    def test_both_ends_of_the_window_are_included_exactly_once(
        self, harness: dict[str, Any]
    ) -> None:
        _seed_claim(harness, id="first", control_number="FIRST1", lines=[line(claim_id="first")])
        _seed_claim(
            harness,
            id="last",
            control_number="LAST111",
            lines=[line(claim_id="last", service_date=date(2026, 9, 30))],
        )
        _seed_claim(
            harness,
            id="both",
            control_number="BOTH111",
            lines=[
                line(id="b1", claim_id="both", line_number=1),
                line(
                    id="b2",
                    claim_id="both",
                    line_number=2,
                    service_date=date(2026, 9, 30),
                ),
            ],
        )
        _seed_claim(
            harness,
            id="before",
            control_number="BEFORE1",
            lines=[line(claim_id="before", service_date=date(2026, 8, 31))],
        )
        _seed_claim(
            harness,
            id="after",
            control_number="AFTER11",
            lines=[line(claim_id="after", service_date=date(2026, 10, 1))],
        )
        numbers = [r["control_number"] for r in _rows(_export(harness, "claims").text)]
        assert sorted(numbers) == ["BOTH111", "FIRST1", "LAST111"]

    def test_a_period_with_no_claims_is_a_header_only_file(self, harness: dict[str, Any]) -> None:
        resp = _export(harness, "claims")
        assert resp.status_code == 200
        assert resp.text == ",".join(CLAIM_COLUMNS) + "\n"

    def test_the_same_window_renders_the_same_bytes_twice(self, harness: dict[str, Any]) -> None:
        _seed_claim(harness)
        _seed_claim(
            harness,
            id="second",
            control_number="SECOND1",
            created_at=datetime(2026, 9, 4, 9, 0, tzinfo=UTC),
            lines=[line(claim_id="second", service_date=date(2026, 9, 8))],
        )
        first = _export(harness, "claims").content
        second = _export(harness, "claims").content
        assert first == second
        assert first.count(b"\n") == 3

    def test_the_file_carries_client_ids_and_nothing_off_the_card(
        self, harness: dict[str, Any]
    ) -> None:
        _seed_claim(harness)
        body = _export(harness, "claims").text
        assert PATIENT_ID in body
        for secret in ("John", "Anon", _MEMBER_ID, _DOB, _DX):
            assert secret not in body


# ---------------------------------------------------------------------------
# The payments file
# ---------------------------------------------------------------------------


class TestPayments:
    def test_one_row_per_ledger_entry_bucketed_the_way_a_balance_is(
        self, harness: dict[str, Any]
    ) -> None:
        harness["payments"].charges = [
            _charge(id="a-collected", kind="copay", amount_cents=3000),
            _charge(
                id="b-owed",
                kind="patient_resp",
                status="pending",
                amount_cents=2000,
                claim_id="claim-1",
            ),
            _charge(
                id="c-written-off",
                kind="write_off",
                amount_cents=1000,
                write_off_reason="hardship",
                note="clinician's own words about why",
            ),
            _charge(id="d-adjusted", kind="contractual_adjustment", amount_cents=4000),
            _charge(id="e-credited", kind="credit", amount_cents=500),
            _charge(
                id="f-settled",
                kind="patient_resp",
                status="pending",
                amount_cents=2500,
                settled_by_charge_id="a-collected",
            ),
        ]
        resp = _export(harness, "payments")

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-disposition"] == (
            'attachment; filename="payments-2026-09-01-to-2026-09-30.csv"'
        )
        assert next(csv.reader(StringIO(resp.text))) == list(PAYMENT_COLUMNS)
        by_id = {r["charge_id"]: r for r in _rows(resp.text)}
        assert by_id["a-collected"]["collected"] == "30.00"
        assert by_id["a-collected"]["owed"] == ""
        assert by_id["b-owed"]["owed"] == "20.00"
        assert by_id["b-owed"]["claim_id"] == "claim-1"
        assert by_id["c-written-off"]["written_off"] == "10.00"
        assert by_id["c-written-off"]["write_off_reason"] == "hardship"
        assert by_id["d-adjusted"]["adjusted"] == "40.00"
        assert by_id["e-credited"]["credited"] == "5.00"
        # Settled by another charge: no longer owed, and it is not the row the
        # money arrived on either.
        settled = by_id["f-settled"]
        assert (settled["owed"], settled["collected"]) == ("", "")
        assert settled["settled_by_charge_id"] == "a-collected"

    def test_a_row_says_what_it_is_and_when_the_practice_recorded_it(
        self, harness: dict[str, Any]
    ) -> None:
        harness["payments"].charges = [
            _charge(status="failed", created_at=datetime(2026, 9, 16, 1, 30, tzinfo=UTC))
        ]
        (row,) = _rows(_export(harness, "payments").text)
        assert row["kind"] == "session"
        assert row["status"] == "failed"
        assert row["amount"] == "150.00"
        assert row["currency"] == "usd"
        assert row["client_id"] == PATIENT_ID
        assert row["appointment_id"] == _APPOINTMENT_ID
        # 01:30 UTC on the 16th is still the 15th where the practice is.
        assert row["recorded_date"] == "2026-09-15"
        # A failed charge is still owed: nothing arrived.
        assert row["owed"] == "150.00"

    def test_both_ends_of_the_practices_day_are_included_exactly_once(
        self, harness: dict[str, Any]
    ) -> None:
        harness["payments"].charges = [
            _charge(id="just-before", created_at=_WINDOW_OPENS - timedelta(minutes=1)),
            _charge(id="first-instant", created_at=_WINDOW_OPENS),
            _charge(id="last-instant", created_at=_WINDOW_CLOSES - timedelta(minutes=1)),
            _charge(id="just-after", created_at=_WINDOW_CLOSES),
        ]
        ids = [r["charge_id"] for r in _rows(_export(harness, "payments").text)]
        assert ids == ["first-instant", "last-instant"]

    def test_a_period_with_no_ledger_rows_is_a_header_only_file(
        self, harness: dict[str, Any]
    ) -> None:
        resp = _export(harness, "payments")
        assert resp.status_code == 200
        assert resp.text == ",".join(PAYMENT_COLUMNS) + "\n"

    def test_the_same_window_renders_the_same_bytes_twice(self, harness: dict[str, Any]) -> None:
        harness["payments"].charges = [
            _charge(id="b", created_at=datetime(2026, 9, 15, 18, 0, tzinfo=UTC)),
            _charge(id="a", created_at=datetime(2026, 9, 15, 17, 0, tzinfo=UTC)),
        ]
        first = _export(harness, "payments").content
        second = _export(harness, "payments").content
        assert first == second
        assert first.count(b"\n") == 3

    def test_the_free_text_on_a_row_stays_in_the_practice(self, harness: dict[str, Any]) -> None:
        harness["payments"].charges = [
            _charge(
                kind="write_off",
                write_off_reason="hardship",
                note="lost her job after the hospitalisation",
            )
        ]
        assert "hospitalisation" not in _export(harness, "payments").text


# ---------------------------------------------------------------------------
# The window, and the audit entry it produces
# ---------------------------------------------------------------------------


class TestWindowAndAudit:
    def test_a_window_that_ends_before_it_starts_is_422(self, harness: dict[str, Any]) -> None:
        resp = _export(harness, "claims", **{"from": "2026-09-30", "to": "2026-09-01"})
        assert resp.status_code == 422
        assert not _audit_rows(harness)

    def test_the_audit_row_names_the_window_and_the_row_count(
        self, harness: dict[str, Any]
    ) -> None:
        _seed_claim(harness)
        _seed_claim(
            harness,
            id="second",
            control_number="SECOND1",
            lines=[line(claim_id="second", service_date=date(2026, 9, 8))],
        )
        _export(harness, "claims")

        (entry,) = _audit_rows(harness)
        assert entry.action == "billing_period_exported"
        assert entry.resource_type == "billing_period_export"
        assert entry.resource_id == "2026-09-01..2026-09-30"
        assert entry.changes == {
            "format": "csv",
            "kind": "claims",
            "from": "2026-09-01",
            "to": "2026-09-30",
            "row_count": 2,
        }

    def test_an_empty_export_is_audited_too(self, harness: dict[str, Any]) -> None:
        _export(harness, "payments")
        (entry,) = _audit_rows(harness)
        assert entry.changes["kind"] == "payments"
        assert entry.changes["row_count"] == 0

    def test_the_audit_row_carries_nothing_off_the_card(self, harness: dict[str, Any]) -> None:
        _seed_claim(harness)
        _export(harness, "claims")
        payload = json.dumps([row.changes for row in _audit_rows(harness)])
        for secret in (_MEMBER_ID, _DOB, _DX, "Anon"):
            assert secret not in payload

    def test_the_claims_and_payments_files_are_audited_separately(
        self, harness: dict[str, Any]
    ) -> None:
        _seed_claim(harness)
        harness["payments"].charges = [_charge()]
        _export(harness, "claims")
        _export(harness, "payments")
        assert [row.changes["kind"] for row in _audit_rows(harness)] == [
            "claims",
            "payments",
        ]


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

_HUGE = 200_000


def _lazy_charges(count: int, produced: list[int]) -> Iterator[PatientCharge]:
    for index in range(count):
        produced[0] += 1
        yield _charge(id=f"charge-{index:08d}")


class TestStreaming:
    def test_the_renderer_pulls_only_the_rows_it_is_about_to_write(self) -> None:
        produced = [0]
        chunks = stream_payments_csv(
            _lazy_charges(_HUGE, produced), timezone=_TZ, tally=ExportTally()
        )
        header = next(chunks)
        assert header == ",".join(PAYMENT_COLUMNS) + "\n"
        assert produced[0] == 0, "the header went out before a single row was read"

        first_body = next(chunks)
        assert len(first_body) >= CHUNK_BYTES
        assert produced[0] < _HUGE / 100, (
            f"rendering one chunk pulled {produced[0]} of {_HUGE} rows — "
            "the ledger is being materialised"
        )

    def test_the_route_streams_a_ledger_far_larger_than_memory(self) -> None:
        payments = _EndlessPayments(_HUGE)
        response = billing_export.export_period(
            request=Request({"type": "http", "method": "GET", "path": "/", "headers": []}),
            user=_user(),
            claims=InMemoryClaimRepository(),
            payments=payments,  # type: ignore[arg-type]  — in-process fake, not the ABC
            users=_FakeUsers(),  # type: ignore[arg-type]  — in-process fake, not the ABC
            from_date=date(2026, 9, 1),
            to_date=date(2026, 9, 30),
            kind="payments",
            audit=AuditService(InMemoryAuditRepository()),
        )

        chunks = asyncio.run(_take(response.body_iterator, 2))
        assert chunks[0] == ",".join(PAYMENT_COLUMNS) + "\n"
        assert len(chunks[1]) >= CHUNK_BYTES
        assert payments.yielded < _HUGE / 100, (
            f"streaming one chunk pulled {payments.yielded} of {_HUGE} rows — "
            "the route is materialising the ledger"
        )

    def test_a_ledger_of_many_chunks_drains_completely(self, harness: dict[str, Any]) -> None:
        rows = 5_000
        harness["payments"].charges = [_charge(id=f"charge-{index:05d}") for index in range(rows)]
        resp = _export(harness, "payments")
        assert resp.status_code == 200
        exported = _rows(resp.text)
        assert len(exported) == rows
        assert exported[-1]["charge_id"] == "charge-04999"
        (entry,) = _audit_rows(harness)
        assert entry.changes["row_count"] == rows


async def _take(body_iterator: Any, count: int) -> list[str]:
    """The first ``count`` chunks off a streaming response, and no more.

    Leaving the loop early leaves the generator suspended, which is the whole
    point: what the source produced by then is what the route actually pulled.
    """
    chunks: list[str] = []
    async for chunk in body_iterator:
        chunks.append(chunk)
        if len(chunks) == count:
            break
    return chunks
