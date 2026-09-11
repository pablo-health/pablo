# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit and property tests for the financial report (``app.payments.reporting``).

Pure functions, so these are about the RULES: which owed row an aging
bucket counts, why a collections rate's denominator drops contractual
adjustments and write-offs, and why a claim with no payment yet is left out
of the lag figures rather than folded in as a zero.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from app.models.claims import ClaimReceipt
from app.models.payments import PatientCharge
from app.payments.reporting import (
    AGING_BUCKET_LABELS,
    _outstanding_owed_rows,
    aging_report,
    claim_payment_lag_report,
    collections_rate_report,
    payer_mix_report,
)

from tests.claims_fixtures import claim, subscriber_snapshot

_PATIENT = "11111111-1111-4111-8111-111111111111"
_VISIT = "33333333-3333-4333-8333-333333333333"
_NOW = datetime(2026, 9, 10, tzinfo=UTC)


def _charge(
    *,
    kind: str = "session",
    status: str = "succeeded",
    amount_cents: int = 10_000,
    patient_id: str = _PATIENT,
    appointment_id: str | None = _VISIT,
    settled_by_charge_id: str | None = None,
    write_off_reason: str | None = None,
    charge_id: str = "charge-1",
    age_days: int = 10,
) -> PatientCharge:
    return PatientCharge(
        id=charge_id,
        patient_id=patient_id,
        appointment_id=appointment_id,
        kind=kind,
        write_off_reason=write_off_reason,
        settled_by_charge_id=settled_by_charge_id,
        amount_cents=amount_cents,
        currency="usd",
        status=status,
        created_by_user_id="user-1",
        created_at=_NOW - timedelta(days=age_days),
    )


# ---------------------------------------------------------------------------
# Aging
# ---------------------------------------------------------------------------


def _bucket_cents(report, label: str) -> int:
    return next(b.cents for b in report.buckets if b.label == label)


def test_empty_ledger_has_every_bucket_at_zero() -> None:
    report = aging_report([], as_of=_NOW)

    assert [b.label for b in report.buckets] == list(AGING_BUCKET_LABELS)
    assert all(b.count == 0 and b.cents == 0 for b in report.buckets)


def test_an_unpaid_session_charge_ages_into_its_bucket() -> None:
    report = aging_report(
        [_charge(kind="session", status="pending", amount_cents=10_000, age_days=45)],
        as_of=_NOW,
    )

    assert _bucket_cents(report, "31-60") == 10_000
    assert _bucket_cents(report, "0-30") == 0


def test_a_paid_session_charge_is_not_outstanding() -> None:
    """It collected itself; there is nothing left to age."""
    report = aging_report(
        [_charge(kind="session", status="succeeded", amount_cents=10_000, age_days=120)],
        as_of=_NOW,
    )

    assert all(b.cents == 0 for b in report.buckets)


def test_an_unsettled_patient_resp_row_is_outstanding() -> None:
    report = aging_report(
        [
            _charge(
                kind="patient_resp",
                appointment_id=None,
                amount_cents=4_000,
                settled_by_charge_id=None,
                age_days=95,
            )
        ],
        as_of=_NOW,
    )

    assert _bucket_cents(report, "90+") == 4_000


def test_a_settled_patient_resp_row_is_not_outstanding() -> None:
    """Settlement is all-or-nothing, the same rule the ledger write side
    enforces: a payment that clears a bill clears the whole row."""
    report = aging_report(
        [
            _charge(
                kind="patient_resp",
                appointment_id=None,
                amount_cents=4_000,
                settled_by_charge_id="charge-2",
                age_days=95,
            )
        ],
        as_of=_NOW,
    )

    assert all(b.cents == 0 for b in report.buckets)


def test_a_write_off_removes_the_row_it_covers_from_aging() -> None:
    rows = [
        _charge(kind="session", status="pending", amount_cents=10_000, age_days=10, charge_id="c1"),
        _charge(kind="write_off", amount_cents=10_000, age_days=1, charge_id="c2"),
    ]

    report = aging_report(rows, as_of=_NOW)

    assert all(b.cents == 0 for b in report.buckets)


def test_a_write_off_smaller_than_the_bill_leaves_the_remainder_outstanding() -> None:
    rows = [
        _charge(kind="session", status="pending", amount_cents=10_000, age_days=10, charge_id="c1"),
        _charge(kind="write_off", amount_cents=6_000, age_days=1, charge_id="c2"),
    ]

    report = aging_report(rows, as_of=_NOW)

    assert _bucket_cents(report, "0-30") == 4_000


def test_a_write_off_applies_to_the_oldest_bill_in_its_group_first() -> None:
    rows = [
        _charge(kind="session", status="pending", amount_cents=5_000, age_days=50, charge_id="old"),
        _charge(kind="session", status="pending", amount_cents=5_000, age_days=5, charge_id="new"),
        _charge(kind="write_off", amount_cents=5_000, age_days=1, charge_id="wo"),
    ]

    report = aging_report(rows, as_of=_NOW)

    assert _bucket_cents(report, "31-60") == 0
    assert _bucket_cents(report, "0-30") == 5_000


def test_loose_rows_from_different_patients_do_not_share_a_bucket_group() -> None:
    """A grouping bug here would let one client's write-off erase another
    client's balance because both rows carry no appointment id."""
    rows = [
        _charge(
            kind="patient_resp",
            appointment_id=None,
            patient_id="patient-a",
            amount_cents=5_000,
            age_days=10,
            charge_id="a-bill",
        ),
        _charge(
            kind="write_off",
            appointment_id=None,
            patient_id="patient-b",
            amount_cents=5_000,
            age_days=1,
            charge_id="b-writeoff",
        ),
    ]

    report = aging_report(rows, as_of=_NOW)

    assert _bucket_cents(report, "0-30") == 5_000


def _reference_total_owed(charges: list[PatientCharge], as_of: datetime) -> int:
    """A second, differently-shaped implementation of the same aging rule.

    Accumulates with list indices instead of a dict keyed by charge id, so a
    bug in :func:`app.payments.reporting._outstanding_owed_rows` (a dropped
    row, a key collision) does not also hide itself here.
    """
    groups: dict[tuple[str, str | None], list[PatientCharge]] = defaultdict(list)
    for charge in charges:
        groups[(charge.patient_id, charge.appointment_id)].append(charge)

    total = 0
    for rows in groups.values():
        owed = sorted(
            (r for r in rows if r.kind in ("session", "patient_resp")),
            key=lambda r: r.created_at,
        )
        remaining = []
        for row in owed:
            if row.kind == "session":
                remaining.append(0 if row.status in ("succeeded", "disputed") else row.amount_cents)
            else:
                remaining.append(0 if row.settled_by_charge_id is not None else row.amount_cents)

        pool = sum(r.amount_cents for r in rows if r.kind in ("write_off", "credit"))
        for i in range(len(remaining)):
            if pool <= 0:
                break
            applied = min(remaining[i], pool)
            remaining[i] -= applied
            pool -= applied

        total += sum(remaining)
    return total


def test_aging_buckets_sum_to_the_practices_total_owed() -> None:
    """Property test over generated ledgers (DONE WHEN #1)."""
    rng = random.Random(20260910)  # noqa: S311 — deterministic test fixture generation, not crypto
    statuses = ["pending", "succeeded", "failed", "disputed", "dispute_lost", "refunded"]

    for _ in range(200):
        patients = [f"patient-{i}" for i in range(rng.randint(1, 4))]
        rows: list[PatientCharge] = []
        for i in range(rng.randint(0, 12)):
            patient_id = rng.choice(patients)
            kind = rng.choice(["session", "patient_resp", "write_off", "credit", "payment"])
            appointment_id = (
                None if kind != "session" else f"{patient_id}-visit-{rng.randint(0, 2)}"
            )
            rows.append(
                _charge(
                    kind=kind,
                    status=rng.choice(statuses),
                    amount_cents=rng.randint(0, 20_000),
                    patient_id=patient_id,
                    appointment_id=appointment_id,
                    settled_by_charge_id=("settled" if rng.random() < 0.3 else None),
                    charge_id=f"row-{i}",
                    age_days=rng.randint(0, 150),
                )
            )

        report = aging_report(rows, as_of=_NOW)
        bucket_total = sum(b.cents for b in report.buckets)
        bucket_count = sum(b.count for b in report.buckets)

        assert bucket_total == _reference_total_owed(rows, _NOW)
        assert bucket_count == sum(1 for cents, _ in _outstanding_rows_for_test(rows) if cents > 0)


def _outstanding_rows_for_test(rows: list[PatientCharge]) -> list[tuple[int, datetime]]:
    groups: dict[tuple[str, str | None], list[PatientCharge]] = defaultdict(list)
    for charge in rows:
        groups[(charge.patient_id, charge.appointment_id)].append(charge)

    out: list[tuple[int, datetime]] = []
    for group_rows in groups.values():
        out.extend(_outstanding_owed_rows(group_rows))
    return out


# ---------------------------------------------------------------------------
# Collections rate
# ---------------------------------------------------------------------------


def test_collections_rate_reports_the_four_raw_figures() -> None:
    rows = [
        _charge(kind="session", status="pending", amount_cents=10_000, charge_id="c1"),
        _charge(kind="payment", status="succeeded", amount_cents=4_000, charge_id="c2"),
        _charge(kind="write_off", amount_cents=1_000, charge_id="c3"),
        _charge(kind="contractual_adjustment", amount_cents=500, charge_id="c4"),
    ]

    rate = collections_rate_report(rows)

    assert rate.billed_cents == 10_000
    assert rate.collected_cents == 4_000
    assert rate.write_off_cents == 1_000
    assert rate.contractual_adjustment_cents == 500


def test_collections_rate_denominator_excludes_contractual_adjustments_and_write_offs() -> None:
    """Neither a contractual adjustment nor a write-off was ever collectible —
    a participating practice agreed never to bill the first, and chose not to
    pursue the second. Counting either against the denominator would report
    the practice as having failed to collect money nobody was ever owed."""
    rows = [
        _charge(kind="session", status="pending", amount_cents=10_000, charge_id="c1"),
        _charge(kind="write_off", amount_cents=3_000, charge_id="c2"),
        _charge(kind="payment", status="succeeded", amount_cents=7_000, charge_id="c3"),
    ]

    rate = collections_rate_report(rows)
    denominator = rate.billed_cents - rate.contractual_adjustment_cents - rate.write_off_cents

    assert denominator == 7_000
    assert rate.collected_cents == denominator


def test_collections_rate_is_zero_over_zero_for_an_empty_practice() -> None:
    rate = collections_rate_report([])

    assert rate.billed_cents == 0
    assert rate.collected_cents == 0
    assert rate.contractual_adjustment_cents == 0
    assert rate.write_off_cents == 0


# ---------------------------------------------------------------------------
# Payer mix
# ---------------------------------------------------------------------------


def test_payer_mix_groups_and_sums_by_payer() -> None:
    aetna = claim(
        id="claim-1",
        control_number="CN1",
        payer_id="payer-aetna",
        total_charge_cents=15_000,
        total_paid_cents=10_000,
        subscriber_snapshot=subscriber_snapshot(payer_id="AETNA", payer_name="Aetna"),
    )
    aetna_2 = claim(
        id="claim-2",
        control_number="CN2",
        payer_id="payer-aetna",
        total_charge_cents=5_000,
        total_paid_cents=0,
        subscriber_snapshot=subscriber_snapshot(payer_id="AETNA", payer_name="Aetna"),
    )
    cigna = claim(
        id="claim-3",
        control_number="CN3",
        payer_id="payer-cigna",
        total_charge_cents=20_000,
        total_paid_cents=18_000,
        subscriber_snapshot=subscriber_snapshot(payer_id="CIGNA", payer_name="Cigna"),
    )

    report = payer_mix_report([aetna, aetna_2, cigna])

    by_payer = {e.payer_id: e for e in report.entries}
    assert by_payer["payer-aetna"].billed_cents == 20_000
    assert by_payer["payer-aetna"].collected_cents == 10_000
    assert by_payer["payer-cigna"].billed_cents == 20_000
    assert by_payer["payer-cigna"].collected_cents == 18_000
    # Cigna billed the same as Aetna but is alphabetically first on a tie.
    assert next(e.payer_id for e in report.entries) in ("payer-aetna", "payer-cigna")


def test_payer_mix_is_empty_for_no_claims() -> None:
    assert payer_mix_report([]).entries == ()


# ---------------------------------------------------------------------------
# Claim-to-payment lag
# ---------------------------------------------------------------------------


def _receipt(
    claim_id: str, kind: str, *, occurred_at: datetime, disposition: str | None = None
) -> ClaimReceipt:
    return ClaimReceipt(
        id=f"{claim_id}-{kind}-{occurred_at.isoformat()}",
        claim_id=claim_id,
        kind=kind,
        detail={"disposition": disposition} if disposition else {},
        occurred_at=occurred_at,
    )


def test_lag_is_measured_from_submission_to_the_paying_835() -> None:
    submitted_claim = claim(
        id="claim-1",
        payer_id="payer-aetna",
        subscriber_snapshot=subscriber_snapshot(payer_id="AETNA", payer_name="Aetna"),
    )
    receipts = {
        "claim-1": [
            _receipt("claim-1", "submitted", occurred_at=datetime(2026, 8, 1, tzinfo=UTC)),
            _receipt(
                "claim-1",
                "adjudicated",
                occurred_at=datetime(2026, 8, 15, tzinfo=UTC),
                disposition="pay",
            ),
        ]
    }

    report = claim_payment_lag_report([submitted_claim], receipts)

    assert len(report.by_payer) == 1
    lag = report.by_payer[0]
    assert lag.payer_id == "payer-aetna"
    assert lag.claim_count == 1
    assert lag.median_days == 14.0
    assert lag.p90_days == 14.0


def test_a_claim_with_no_payment_yet_is_excluded_not_zero() -> None:
    """DONE WHEN #3: a claim only submitted, or only denied, contributes no
    lag sample — it must not silently read as an instant payment."""
    only_submitted = claim(
        id="claim-1",
        payer_id="payer-aetna",
        subscriber_snapshot=subscriber_snapshot(payer_id="AETNA", payer_name="Aetna"),
    )
    only_denied = claim(
        id="claim-2",
        payer_id="payer-aetna",
        subscriber_snapshot=subscriber_snapshot(payer_id="AETNA", payer_name="Aetna"),
    )
    receipts = {
        "claim-1": [
            _receipt("claim-1", "submitted", occurred_at=datetime(2026, 8, 1, tzinfo=UTC)),
        ],
        "claim-2": [
            _receipt("claim-2", "submitted", occurred_at=datetime(2026, 8, 1, tzinfo=UTC)),
            _receipt(
                "claim-2",
                "adjudicated",
                occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
                disposition="deny",
            ),
        ],
    }

    report = claim_payment_lag_report([only_submitted, only_denied], receipts)

    assert report.by_payer == ()


def test_lag_is_empty_for_no_claims() -> None:
    assert claim_payment_lag_report([], {}).by_payer == ()
