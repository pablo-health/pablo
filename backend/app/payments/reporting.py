# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's own financial report: aging, payer mix, collections rate,
and claim-to-payment lag.

Pure, like :mod:`app.payments.balance` — every function here reads the rows
it is handed and nothing else, and none of the four figures is stored. A
report is arithmetic over the ledger and the claims table as they stand right
now (or as they stood at an explicit ``as_of``/window), not a number that can
drift from the rows that produced it.

**A/R aging** buckets what is still owed by how long it has been owed.
Grouping is by ``(patient_id, appointment_id)`` — the same grouping
:func:`app.payments.balance.patient_balance` uses per client, applied across
the whole practice at once, since an appointment id is unique regardless of
whose it is and a bare ``appointment_id`` groups only within one patient's own
loose rows. A write-off or credit reduces the OLDEST unpaid owed row in its
group first, because that is the row the statement would show it against; a
row it cannot fully cover leaves the remainder on the next-oldest. What is
left after that reduction is the row's own outstanding amount, aged from its
own ``created_at`` — which is why the buckets sum to exactly the practice's
outstanding balance and never to its gross billed total.

**Payer mix** and **claim-to-payment lag** read claims, not the ledger: a
claim already carries what was billed to the payer
(``total_charge_cents``) and what came back (``total_paid_cents``), and a
receipt already carries the moment the claim was submitted and the moment its
835 arrived. Restating either from ledger rows would be a second, and
possibly drifting, copy of numbers the claim itself is the source for.

**Collections rate** is read from the ledger through
:func:`app.payments.balance.patient_balance` directly: ``billed`` is its
``owed_cents``, ``collected`` its ``collected_cents``, and the two figures a
caller subtracts out of the denominator are its ``adjusted_cents`` (what a
participating practice agreed never to bill) and ``written_off_cents`` (what
the practice chose not to collect). Neither was ever collectible, so leaving
them in the denominator would report a practice as failing to collect money
nobody was ever going to pay. The rate itself is not computed here — cents
divide exactly and percentages do not, so the division happens in the caller
that is going to round it for a screen.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import TYPE_CHECKING

from .balance import patient_balance

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import datetime

    from ..models.claims import Claim, ClaimReceipt
    from ..models.payments import PatientCharge

#: Mirrors ``app.payments.balance._OWED_KINDS``. Kept local rather than
#: imported because that name is private to balance.py — the rule it encodes
#: ("what is owed") is stable and documented once, on ``CHARGE_KINDS`` in
#: ``app.db.models``.
_OWED_KINDS = frozenset({"session", "patient_resp"})

#: Mirrors ``app.payments.balance._MONEY_ARRIVED``: the statuses in which a
#: session charge — which is both the bill and its own payment attempt — has
#: collected itself.
_MONEY_ARRIVED = frozenset({"succeeded", "disputed"})

#: Kinds that reduce a balance without anyone paying it.
_REDUCING_KINDS = frozenset({"write_off", "credit"})

AGING_BUCKET_LABELS: tuple[str, ...] = ("0-30", "31-60", "61-90", "90+")

#: Upper bound (inclusive) of age in days for every bucket but the last,
#: which catches everything older.
_AGING_BUCKET_CEILINGS: tuple[int, ...] = (30, 60, 90)

#: A remittance that paid something. ``deny`` moved no money, so a claim
#: adjudicated only as a denial has no payment to measure lag against.
_PAYING_DISPOSITIONS = frozenset({"pay", "pay_partial"})


@dataclass(frozen=True)
class AgingBucket:
    label: str
    count: int = 0
    cents: int = 0


@dataclass(frozen=True)
class AgingReport:
    buckets: tuple[AgingBucket, ...] = field(
        default_factory=lambda: tuple(AgingBucket(label=label) for label in AGING_BUCKET_LABELS)
    )


@dataclass(frozen=True)
class PayerMixEntry:
    payer_id: str
    payer_name: str
    billed_cents: int
    collected_cents: int


@dataclass(frozen=True)
class PayerMixReport:
    entries: tuple[PayerMixEntry, ...] = ()


@dataclass(frozen=True)
class CollectionsRate:
    """The four cents figures a caller needs to compute a rate.

    ``collected_cents / (billed_cents - contractual_adjustment_cents -
    write_off_cents)`` is the rate; nothing here divides them, so the API
    stays exact.
    """

    billed_cents: int = 0
    collected_cents: int = 0
    contractual_adjustment_cents: int = 0
    write_off_cents: int = 0


@dataclass(frozen=True)
class PayerLag:
    payer_id: str
    payer_name: str
    claim_count: int
    median_days: float
    p90_days: float


@dataclass(frozen=True)
class ClaimPaymentLagReport:
    by_payer: tuple[PayerLag, ...] = ()


def aging_report(charges: Iterable[PatientCharge], *, as_of: datetime) -> AgingReport:
    """Bucket what is still owed by how long it has been owed, as of ``as_of``."""
    groups: dict[tuple[str, str | None], list[PatientCharge]] = defaultdict(list)
    for charge in charges:
        groups[(charge.patient_id, charge.appointment_id)].append(charge)

    totals = {label: AgingBucket(label=label) for label in AGING_BUCKET_LABELS}
    for rows in groups.values():
        for cents, created_at in _outstanding_owed_rows(rows):
            if cents <= 0:
                continue
            label = _bucket_label((as_of - created_at).days)
            bucket = totals[label]
            totals[label] = AgingBucket(
                label=label, count=bucket.count + 1, cents=bucket.cents + cents
            )

    return AgingReport(buckets=tuple(totals[label] for label in AGING_BUCKET_LABELS))


def _outstanding_owed_rows(rows: list[PatientCharge]) -> list[tuple[int, datetime]]:
    """One group's owed rows, each with what is still outstanding on it.

    A session row collects itself, off its own status. A ``patient_resp``
    row is collected in full, or not at all, the moment a payment settles
    it (``settled_by_charge_id``) — never partially, which is the rule
    ``app.routes.patient_payments._bills_this_payment_clears`` already
    enforces on the write side. What is left after that is reduced by this
    group's write-offs and credits, oldest owed row first, because that is
    the row a write-off against an old bill is against.
    """
    owed = sorted(
        (row for row in rows if row.kind in _OWED_KINDS),
        key=lambda row: row.created_at,
    )
    remaining: dict[str, int] = {}
    for row in owed:
        if row.kind == "session":
            remaining[row.id] = 0 if row.status in _MONEY_ARRIVED else row.amount_cents
        else:  # patient_resp
            remaining[row.id] = 0 if row.settled_by_charge_id is not None else row.amount_cents

    reduction = sum(row.amount_cents for row in rows if row.kind in _REDUCING_KINDS)
    for row in owed:
        if reduction <= 0:
            break
        take = min(remaining[row.id], reduction)
        remaining[row.id] -= take
        reduction -= take

    return [(remaining[row.id], row.created_at) for row in owed]


def _bucket_label(age_days: int) -> str:
    for ceiling, label in zip(_AGING_BUCKET_CEILINGS, AGING_BUCKET_LABELS, strict=False):
        if age_days <= ceiling:
            return label
    return AGING_BUCKET_LABELS[-1]


@dataclass
class _PayerTotals:
    payer_name: str
    billed_cents: int = 0
    collected_cents: int = 0


def payer_mix_report(claims: Iterable[Claim]) -> PayerMixReport:
    """What was billed and collected per payer, over whatever window ``claims`` is."""
    totals: dict[str, _PayerTotals] = {}
    for claim in claims:
        entry = totals.setdefault(
            claim.payer_id, _PayerTotals(payer_name=claim.subscriber_snapshot.payer_name)
        )
        entry.billed_cents += claim.total_charge_cents
        entry.collected_cents += claim.total_paid_cents

    entries = tuple(
        PayerMixEntry(
            payer_id=payer_id,
            payer_name=totals[payer_id].payer_name,
            billed_cents=totals[payer_id].billed_cents,
            collected_cents=totals[payer_id].collected_cents,
        )
        for payer_id in totals
    )
    ordered = sorted(entries, key=lambda e: (-e.billed_cents, e.payer_name))
    return PayerMixReport(entries=tuple(ordered))


def collections_rate_report(charges: Iterable[PatientCharge]) -> CollectionsRate:
    """Billed, collected, adjusted and written-off cents over whatever window
    ``charges`` is — read straight off :func:`patient_balance`'s totals."""
    summary = patient_balance(charges)
    return CollectionsRate(
        billed_cents=summary.owed_cents,
        collected_cents=summary.collected_cents,
        contractual_adjustment_cents=summary.adjusted_cents,
        write_off_cents=summary.written_off_cents,
    )


def claim_payment_lag_report(
    claims: Iterable[Claim],
    receipts_by_claim: Mapping[str, Sequence[ClaimReceipt]],
) -> ClaimPaymentLagReport:
    """Median and p90 days from a claim's first submission to the 835 that
    first paid it, per payer.

    A claim never adjudicated, or adjudicated only as a denial, has no
    payment to measure a lag against and is excluded rather than folded in
    as a zero — a claim nobody has paid yet is not a claim paid instantly.
    """
    days_by_payer: dict[str, list[float]] = defaultdict(list)
    names: dict[str, str] = {}
    for claim in claims:
        receipts = receipts_by_claim.get(claim.id, ())
        submitted_at = min((r.occurred_at for r in receipts if r.kind == "submitted"), default=None)
        paid_at = min(
            (
                r.occurred_at
                for r in receipts
                if r.kind == "adjudicated" and r.detail.get("disposition") in _PAYING_DISPOSITIONS
            ),
            default=None,
        )
        if submitted_at is None or paid_at is None:
            continue
        lag_days = (paid_at - submitted_at).total_seconds() / 86400
        days_by_payer[claim.payer_id].append(lag_days)
        names[claim.payer_id] = claim.subscriber_snapshot.payer_name

    by_payer = tuple(
        PayerLag(
            payer_id=payer_id,
            payer_name=names[payer_id],
            claim_count=len(days),
            median_days=median(days),
            p90_days=_percentile(days, 0.9),
        )
        for payer_id, days in days_by_payer.items()
    )
    return ClaimPaymentLagReport(by_payer=tuple(sorted(by_payer, key=lambda p: p.payer_name)))


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolation percentile (numpy's default), over a non-empty sequence."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight
