# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a payer agreed to pay, and what it actually allowed.

The RECORD is a contracted rate per code per contract period, hanging off a
``payer_participations`` row, versioned by ``effective_date`` and never edited
in place — so a claim from last year reads against the rate in force when it
was filed. The VARIANCE compares each adjudicated line's allowed amount against
it; underpayment against a practice's own contract is common, recoverable, and
invisible without the comparison.

The arithmetic is pure (:func:`line_variance`); only :func:`variance_report`
touches a session. That split is what lets the rounding, the effective-date
boundary and both rate bases be tested without a database — which is where the
errors that matter live.

Single practice only. No cross-practice aggregation, deliberately.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import and_, select

from ..db.models import ClaimLineRow, ClaimRow, ContractedRateRow, PayerParticipationRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class VarianceStatus(StrEnum):
    """Why a line does or does not have a comparable variance.

    The three non-comparable states are separate on purpose. Collapsing them
    into a zero variance is the specific error this report exists to avoid: a
    practice reading "no difference" cannot tell whether it was paid correctly,
    has no contract on file, or has a percentage rate nobody has entered a
    Medicare amount for.
    """

    #: Allowed and contracted both known, and they agree.
    MATCH = "match"
    #: Allowed and contracted both known, and they differ.
    VARIANCE = "variance"
    #: No rate covers this code for this date. NOT a variance of zero.
    NO_RATE_ON_FILE = "no_rate_on_file"
    #: A percentage-of-Medicare rate with no Medicare amount entered.
    NOT_COMPUTABLE = "not_computable"
    #: No remittance has arrived, so there is no allowed amount to compare.
    NOT_ADJUDICATED = "not_adjudicated"


@dataclass(frozen=True)
class Rate:
    """A contracted rate as the arithmetic needs it, without the ORM row."""

    basis: str
    amount_cents: int | None
    percent: Decimal | None
    mpfs_amount_cents: int | None
    effective_date: date
    end_date: date | None = None

    def covers(self, service_date: date) -> bool:
        """Was this rate in force on the day of service?

        Inclusive at both ends. A rate whose ``effective_date`` is after the
        service date does not apply — which is the boundary a practice gets
        wrong by hand every time a schedule changes mid-year.
        """
        if service_date < self.effective_date:
            return False
        return self.end_date is None or service_date <= self.end_date

    def per_unit_cents(self) -> int | None:
        """What one unit of this code was contracted at, or ``None`` if unknowable.

        ``None`` means a percentage rate with no Medicare amount on file. It is
        not zero, and the caller must not treat it as one.
        """
        if self.basis == "fixed":
            return self.amount_cents
        if self.percent is None or self.mpfs_amount_cents is None:
            return None
        # Decimal, not float: a percentage of a cent amount is exactly the
        # place a float loses a penny, and this number is what a practice would
        # take to a payer.
        exact = (Decimal(self.mpfs_amount_cents) * self.percent) / Decimal(100)
        return int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class LineVariance:
    """One service line compared against its contract."""

    status: VarianceStatus
    cpt: str
    service_date: date
    units: int
    allowed_cents: int | None
    contracted_cents: int | None

    @property
    def variance_cents(self) -> int | None:
        """Allowed minus contracted. NEGATIVE is underpayment.

        ``None`` whenever the two are not both known — the whole point of
        :class:`VarianceStatus`.
        """
        if self.allowed_cents is None or self.contracted_cents is None:
            return None
        return self.allowed_cents - self.contracted_cents

    @property
    def underpaid(self) -> bool:
        variance = self.variance_cents
        return variance is not None and variance < 0


def line_variance(
    *,
    cpt: str,
    service_date: date,
    units: int,
    allowed_cents: int | None,
    rates: list[Rate],
) -> LineVariance:
    """Compare one line against whichever rate covered its service date.

    ``rates`` is every rate on file for this code; the one in force on the day
    of service is selected here rather than by the caller. Where more than one
    covers the date — overlapping periods somebody entered by hand — the LATEST
    ``effective_date`` wins, because that is the more recently agreed schedule.
    """
    applicable = [r for r in rates if r.covers(service_date)]
    if not applicable:
        return LineVariance(
            status=VarianceStatus.NO_RATE_ON_FILE,
            cpt=cpt,
            service_date=service_date,
            units=units,
            allowed_cents=allowed_cents,
            contracted_cents=None,
        )

    rate = max(applicable, key=lambda r: r.effective_date)
    per_unit = rate.per_unit_cents()
    if per_unit is None:
        return LineVariance(
            status=VarianceStatus.NOT_COMPUTABLE,
            cpt=cpt,
            service_date=service_date,
            units=units,
            allowed_cents=allowed_cents,
            contracted_cents=None,
        )

    contracted = per_unit * units
    if allowed_cents is None:
        return LineVariance(
            status=VarianceStatus.NOT_ADJUDICATED,
            cpt=cpt,
            service_date=service_date,
            units=units,
            allowed_cents=None,
            contracted_cents=contracted,
        )

    status = VarianceStatus.MATCH if allowed_cents == contracted else VarianceStatus.VARIANCE
    return LineVariance(
        status=status,
        cpt=cpt,
        service_date=service_date,
        units=units,
        allowed_cents=allowed_cents,
        contracted_cents=contracted,
    )


@dataclass(frozen=True)
class VarianceGroup:
    """Aggregated variance for one payer, or one code within a payer."""

    key: str
    line_count: int
    compared_count: int
    allowed_cents: int
    contracted_cents: int
    underpaid_count: int
    underpaid_cents: int
    no_rate_on_file_count: int
    not_computable_count: int
    not_adjudicated_count: int

    @property
    def variance_cents(self) -> int:
        """Across compared lines only. Negative is underpayment."""
        return self.allowed_cents - self.contracted_cents


def record_rate(  # noqa: PLR0913 — service deps + keyword-only rate fields
    session: Session,
    *,
    participation: PayerParticipationRow,
    cpt: str,
    basis: str,
    effective_date: date,
    modifier: str = "",
    amount_cents: int | None = None,
    percent: Decimal | None = None,
    mpfs_amount_cents: int | None = None,
    end_date: date | None = None,
    source_document_id: str | None = None,
) -> ContractedRateRow:
    """Record what a payer agreed to pay for one code. Does not commit.

    Takes the participation row rather than ids so ``user_id`` comes from the
    contract it belongs to and cannot be passed inconsistently with it.

    Raises ``ValueError`` before the database does when the basis and its
    numbers disagree — the check constraint also refuses it, but a caller
    deserves to be told which field is wrong rather than which constraint
    fired.
    """
    if basis == "fixed":
        if amount_cents is None or percent is not None:
            msg = "A fixed rate needs amount_cents and must not carry percent"
            raise ValueError(msg)
    elif basis == "percent_of_mpfs":
        if percent is None or amount_cents is not None:
            msg = "A percent_of_mpfs rate needs percent and must not carry amount_cents"
            raise ValueError(msg)
    else:
        msg = f"Unknown rate basis {basis!r}; expected 'fixed' or 'percent_of_mpfs'"
        raise ValueError(msg)

    now = datetime.now(UTC)
    row = ContractedRateRow(
        id=str(uuid.uuid4()),
        participation_id=participation.id,
        user_id=participation.user_id,
        cpt=cpt,
        modifier=modifier,
        basis=basis,
        amount_cents=amount_cents,
        percent=percent,
        mpfs_amount_cents=mpfs_amount_cents,
        effective_date=effective_date,
        end_date=end_date,
        source_document_id=source_document_id,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def rates_for(
    session: Session, user_id: str, payer_id: str, cpt: str, modifier: str = ""
) -> list[Rate]:
    """Every rate on file for one clinician, payer and code."""
    rows = (
        session.execute(
            select(ContractedRateRow)
            .join(
                PayerParticipationRow,
                PayerParticipationRow.id == ContractedRateRow.participation_id,
            )
            .where(
                and_(
                    ContractedRateRow.user_id == user_id,
                    PayerParticipationRow.payer_id == payer_id,
                    ContractedRateRow.cpt == cpt,
                    ContractedRateRow.modifier == modifier,
                )
            )
            .order_by(ContractedRateRow.effective_date)
        )
        .scalars()
        .all()
    )
    return [_to_rate(row) for row in rows]


def variance_report(
    session: Session,
    user_id: str,
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, list[LineVariance]]:
    """Every adjudicated line of this clinician's claims, against its contract.

    Keyed by payer row id, because "which payer underpays me" is the first
    question the report is asked and a ``LineVariance`` deliberately does not
    carry a payer — it is the arithmetic's value type, and the arithmetic does
    not depend on who paid. Flatten with :func:`all_lines` when the payer does
    not matter.

    Scoped to one clinician by the rendering provider snapshotted on the claim,
    and to one practice by the tenant schema the session is bound to. Nothing
    here reads across practices.

    Only adjudicated claims are considered. An unadjudicated claim has no
    allowed amount, and reporting "not adjudicated" for every line of every
    draft would bury the findings under rows nobody can act on.
    """
    rate_cache: dict[tuple[str, str, str], list[Rate]] = {}
    out: dict[str, list[LineVariance]] = {}

    rows = session.execute(
        select(ClaimLineRow, ClaimRow.payer_id)
        .join(ClaimRow, ClaimRow.id == ClaimLineRow.claim_id)
        .where(
            and_(
                # The rendering clinician is snapshotted into the claim's
                # billing identity, so that is where a line's rate ownership
                # comes from — not from who happens to be reading.
                ClaimRow.billing_snapshot["rendering_provider"]["user_id"].astext == user_id,
                ClaimRow.adjudicated_at.is_not(None),
                *(() if start is None else (ClaimLineRow.service_date >= start,)),
                *(() if end is None else (ClaimLineRow.service_date <= end,)),
            )
        )
        .order_by(ClaimLineRow.service_date, ClaimLineRow.cpt, ClaimLineRow.id)
    ).all()

    for line, payer_id in rows:
        modifier = _primary_modifier(line.modifiers)
        key = (payer_id, line.cpt, modifier)
        if key not in rate_cache:
            rate_cache[key] = rates_for(session, user_id, payer_id, line.cpt, modifier)
        out.setdefault(payer_id, []).append(
            line_variance(
                cpt=line.cpt,
                service_date=line.service_date,
                units=line.units,
                allowed_cents=line.allowed_cents,
                rates=rate_cache[key],
            )
        )
    return out


def all_lines(by_payer: dict[str, list[LineVariance]]) -> list[LineVariance]:
    """Flatten a report when the payer does not matter, payers in id order."""
    return [line for _, lines in sorted(by_payer.items()) for line in lines]


def by_payer(report: dict[str, list[LineVariance]]) -> list[VarianceGroup]:
    """Roll each payer's lines into one row, payers in id order."""
    return [_group(payer_id, lines) for payer_id, lines in sorted(report.items())]


def by_cpt(variances: list[LineVariance]) -> list[VarianceGroup]:
    """Roll lines up by procedure code, codes in code order."""
    buckets: dict[str, list[LineVariance]] = {}
    for variance in variances:
        buckets.setdefault(variance.cpt, []).append(variance)
    return [_group(name, rows) for name, rows in sorted(buckets.items())]


def _group(key: str, rows: list[LineVariance]) -> VarianceGroup:
    compared = [r for r in rows if r.variance_cents is not None]
    return VarianceGroup(
        key=key,
        line_count=len(rows),
        compared_count=len(compared),
        # Sums cover COMPARED lines only. Including a line whose contracted
        # amount is unknown would make the totals disagree with the variance.
        allowed_cents=sum(r.allowed_cents or 0 for r in compared),
        contracted_cents=sum(r.contracted_cents or 0 for r in compared),
        underpaid_count=sum(1 for r in compared if r.underpaid),
        underpaid_cents=sum(-(r.variance_cents or 0) for r in compared if r.underpaid),
        no_rate_on_file_count=sum(1 for r in rows if r.status is VarianceStatus.NO_RATE_ON_FILE),
        not_computable_count=sum(1 for r in rows if r.status is VarianceStatus.NOT_COMPUTABLE),
        not_adjudicated_count=sum(1 for r in rows if r.status is VarianceStatus.NOT_ADJUDICATED),
    )


def _to_rate(row: ContractedRateRow) -> Rate:
    return Rate(
        basis=row.basis,
        amount_cents=row.amount_cents,
        percent=row.percent,
        mpfs_amount_cents=row.mpfs_amount_cents,
        effective_date=row.effective_date,
        end_date=row.end_date,
    )


def _primary_modifier(modifiers: list | None) -> str:
    """The modifier a rate is keyed on: the first one, or none.

    A fee schedule prices a code plus at most one pricing modifier; the rest of
    a line's modifiers are informational (95 for telehealth, for instance, does
    not usually carry its own rate). Keying on the first keeps the lookup
    deterministic. A line whose pricing modifier is not first will report
    ``no_rate_on_file`` rather than silently matching the unmodified rate —
    visible, and the honest answer until somebody needs more.
    """
    if not modifiers:
        return ""
    first = modifiers[0]
    return str(first) if first else ""
