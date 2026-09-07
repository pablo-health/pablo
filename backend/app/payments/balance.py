# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a client owes, computed from the charge ledger and stored nowhere.

A balance is arithmetic over rows, not a column. Storing it would create a
second source of truth that drifts the first time a webhook lands out of
order, a refund arrives, or a remittance is posted twice — and the drift is
invisible, because a stored number always looks like an answer. Computing it
means the ledger is the only thing that can be wrong, and the ledger is the
thing an auditor reads anyway.

The rules, once, here:

* **Owed** is the unpaid part of what the client was actually billed for:
  ``session`` rows (the full-rate visit charge) and ``patient_resp`` rows
  (what the payer said the client owes after adjudication). A row counts as
  owed while it has not succeeded and has not been settled by another charge.
* **Collected** is money that actually arrived: ``session`` and ``copay``
  rows in status ``succeeded``. A ``refunded``, ``failed`` or ``dispute_lost``
  row collected nothing; a ``disputed`` row is money the practice is holding
  but may lose, and it counts as collected until the dispute resolves, which
  is what ``dispute_lost`` is for.
* **Written off** is ``write_off`` rows. They reduce the balance without
  anyone paying.
* ``contractual_adjustment`` is owed by nobody — a participating practice
  agreed not to bill it — so it never touches the balance. It is summarised
  separately because the client's statement has to explain where the rest of
  the practice's rate went.
* ``credit`` is money held on the client's behalf. It reduces the balance and
  can take it negative, which is the point: a negative balance is a refund the
  practice owes, and rendering it as zero would hide that.

Every amount in the ledger is a positive magnitude (the table enforces it);
sign is applied here, by kind, so no reader anywhere else has to remember
which kinds are negative.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ..models.payments import PatientCharge

#: Kinds that put money on the client's tab.
_OWED_KINDS = frozenset({"session", "patient_resp"})

#: Kinds that represent money the client actually handed over.
_COLLECTED_KINDS = frozenset({"session", "copay"})

#: Statuses in which money is in the practice's hands. ``disputed`` is here
#: deliberately: the funds are held pending the cardholder's bank, and the
#: ledger has a distinct ``dispute_lost`` for the outcome where they are not.
_MONEY_ARRIVED = frozenset({"succeeded", "disputed"})


@dataclass(frozen=True)
class VisitBalance:
    """One visit's line on a statement.

    ``appointment_id`` is ``None`` for the rows that hang off no visit — a
    late-cancellation fee, a credit, a balance the practice raised by hand.
    They are collapsed into a single unattributed line rather than dropped,
    because a statement whose lines do not sum to its total is worse than one
    with an "other" row.
    """

    appointment_id: str | None
    owed_cents: int = 0
    collected_cents: int = 0
    written_off_cents: int = 0
    adjusted_cents: int = 0
    credited_cents: int = 0

    @property
    def balance_cents(self) -> int:
        return self.owed_cents - self.collected_cents - self.written_off_cents - self.credited_cents


@dataclass(frozen=True)
class BalanceSummary:
    """What the client owes the practice, and the arithmetic behind it."""

    owed_cents: int = 0
    collected_cents: int = 0
    written_off_cents: int = 0
    adjusted_cents: int = 0
    credited_cents: int = 0
    by_visit: Sequence[VisitBalance] = field(default_factory=tuple)

    @property
    def balance_cents(self) -> int:
        """Positive means the client owes; negative means the practice does."""
        return self.owed_cents - self.collected_cents - self.written_off_cents - self.credited_cents


@dataclass
class _Bucket:
    """Mutable accumulator; frozen into a :class:`VisitBalance` at the end."""

    owed: int = 0
    collected: int = 0
    written_off: int = 0
    adjusted: int = 0
    credited: int = 0


def _is_owed(charge: PatientCharge) -> bool:
    """An owed row is one that was billed and has not been paid or settled.

    ``settled_by_charge_id`` is what lets a ``patient_resp`` row stop being
    owed when a separate charge collects it: the money arrived on a different
    row, so without this the same dollar would be owed and collected at once.
    """
    if charge.kind not in _OWED_KINDS:
        return False
    if charge.settled_by_charge_id is not None:
        return False
    return charge.status not in _MONEY_ARRIVED


def _is_collected(charge: PatientCharge) -> bool:
    return charge.kind in _COLLECTED_KINDS and charge.status in _MONEY_ARRIVED


def patient_balance(charges: Iterable[PatientCharge]) -> BalanceSummary:
    """Summarise a client's ledger rows.

    Pure: it reads the rows it is handed and touches nothing else, so the
    chart header, the statement PDF and the practice-wide balances list can
    all render from the same arithmetic without a query apiece.
    """
    totals = _Bucket()
    per_visit: dict[str | None, _Bucket] = defaultdict(_Bucket)

    for charge in charges:
        bucket = per_visit[charge.appointment_id]
        amount = charge.amount_cents

        if _is_owed(charge):
            totals.owed += amount
            bucket.owed += amount
        if _is_collected(charge):
            totals.collected += amount
            bucket.collected += amount
        if charge.kind == "write_off":
            totals.written_off += amount
            bucket.written_off += amount
        elif charge.kind == "contractual_adjustment":
            totals.adjusted += amount
            bucket.adjusted += amount
        elif charge.kind == "credit":
            totals.credited += amount
            bucket.credited += amount

    # Visits first, in a stable order, then the unattributed line last — a
    # statement reads chronologically and ends with "everything else".
    ordered = sorted(
        (key for key in per_visit if key is not None),
    )
    by_visit = [
        VisitBalance(
            appointment_id=key,
            owed_cents=per_visit[key].owed,
            collected_cents=per_visit[key].collected,
            written_off_cents=per_visit[key].written_off,
            adjusted_cents=per_visit[key].adjusted,
            credited_cents=per_visit[key].credited,
        )
        for key in ordered
    ]
    if None in per_visit:
        loose = per_visit[None]
        by_visit.append(
            VisitBalance(
                appointment_id=None,
                owed_cents=loose.owed,
                collected_cents=loose.collected,
                written_off_cents=loose.written_off,
                adjusted_cents=loose.adjusted,
                credited_cents=loose.credited,
            )
        )

    return BalanceSummary(
        owed_cents=totals.owed,
        collected_cents=totals.collected,
        written_off_cents=totals.written_off,
        adjusted_cents=totals.adjusted,
        credited_cents=totals.credited,
        by_visit=tuple(by_visit),
    )
