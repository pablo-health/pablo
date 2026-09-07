# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's own copy of a period: what it billed, and what it collected.

Two CSVs over a ``[from, to]`` window, rendered a row at a time:

* **claims** — one row per claim with a service line dated in the window: the
  control number, the client, the payer, the span of service dates, and the
  money as the payer left it (billed, allowed, paid, adjusted, patient
  responsibility) with the claim's state and the dates it was filed and
  adjudicated.
* **payments** — one row per ledger entry recorded in the window, with what
  the row *is* (its kind), how it ended (its status), the amount, and the
  visit, claim and write-off reason behind it.

Different from the biller handoff in :mod:`app.claims.export` in three ways
that matter. It is the practice's own records rather than a package leaving
for someone else, so it carries client ids and no names, no member ids, no
diagnoses and no tax id — the practice already knows who its clients are, and
an accountant reconciling a bank statement does not need to be handed
identified health information to do it. It is not refused for a scrub
finding: refusing a practice its own ledger because a claim has a validation
problem would be withholding the very record that explains the problem.  And
it is rendered lazily, because a year of a busy practice is not a thing to
hold in memory.

**Two windows, one parameter.** A claim is selected by its *dates of
service* — the period a practice bills for is the period it saw people in,
and it is the window the biller export already uses. A ledger row is selected
by *when it was recorded*, which is the day the money moved and so the day it
shows up on the bank statement. Both are read in the practice's own timezone,
so a Friday-evening charge belongs to Friday.

**Byte-stable.** The same window over the same rows renders the same bytes
twice: fixed columns, a fixed ``\\n`` line terminator, ordering fixed by the
repository (oldest first, id as the tiebreaker), and nothing about the moment
of export in the body. A practice can diff a re-export against the copy it
filed away and see only what actually changed.

Money is stored as integer cents and leaves as dollars with two decimals,
through the same :func:`app.claims.export.dollars` the biller CSV uses so the
two files never disagree about how a figure is written. A column that is
blank is a value nobody has stated yet — an unadjudicated claim has no
allowed amount, and that is not zero.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING

from ..claims.export import dollars
from .balance import patient_balance

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from datetime import date, datetime, tzinfo

    from ..models.claims import Claim
    from ..models.payments import PatientCharge

#: The columns of the claims CSV, in this order.
CLAIM_COLUMNS: tuple[str, ...] = (
    "control_number",
    "claim_id",
    "client_id",
    "payer_name",
    "payer_id",
    "first_service_date",
    "last_service_date",
    "state",
    "billed",
    "allowed",
    "paid",
    "adjusted",
    "patient_responsibility",
    "filed_date",
    "adjudicated_date",
)

#: The columns of the payments CSV, in this order. The five bucket columns
#: after ``currency`` are the row's amount repeated under whichever heading
#: :mod:`app.payments.balance` puts it, and blank under the rest, so an
#: accountant can total a column instead of re-deriving the rules by kind.
PAYMENT_COLUMNS: tuple[str, ...] = (
    "charge_id",
    "recorded_date",
    "client_id",
    "kind",
    "status",
    "amount",
    "currency",
    "owed",
    "collected",
    "written_off",
    "adjusted",
    "credited",
    "appointment_id",
    "claim_id",
    "write_off_reason",
    "settled_by_charge_id",
)

#: Roughly how many bytes accumulate before a chunk is handed to the client.
#: Small enough that memory stays flat over a year of rows, large enough that
#: a long export is not one write per row.
CHUNK_BYTES = 64 * 1024


@dataclass
class ExportTally:
    """How many rows the render actually emitted.

    The count is only known once the last row has gone out, and the audit
    entry for a period export is *about* the count — so the caller hands one
    of these in and reads it after the stream has drained.
    """

    rows: int = 0


def stream_claims_csv(
    claims: Iterable[Claim], *, timezone: tzinfo, tally: ExportTally
) -> Iterator[str]:
    """Render claims to CSV text, a chunk at a time, in the order given."""
    return _stream(CLAIM_COLUMNS, (claim_row(claim, timezone=timezone) for claim in claims), tally)


def stream_payments_csv(
    charges: Iterable[PatientCharge], *, timezone: tzinfo, tally: ExportTally
) -> Iterator[str]:
    """Render ledger rows to CSV text, a chunk at a time, in the order given."""
    return _stream(
        PAYMENT_COLUMNS, (payment_row(charge, timezone=timezone) for charge in charges), tally
    )


def claim_row(claim: Claim, *, timezone: tzinfo) -> list[str]:
    """One claims row, in :data:`CLAIM_COLUMNS` order.

    The money columns are the claim's own totals plus what the payer said per
    line. ``adjusted`` is the contractual write-down the allowed amount
    implies — charge minus allowed, over the lines that have been adjudicated
    — which is the figure that makes the row add up: billed less adjusted is
    what anybody is ever going to pay. Lines nobody has adjudicated leave
    ``allowed``, ``adjusted`` and ``patient_responsibility`` blank rather than
    claiming a zero the payer never said.
    """
    plan = claim.subscriber_snapshot
    service_dates = sorted(line.service_date for line in claim.lines)
    allowed = _sum_known(line.allowed_cents for line in claim.lines)
    adjusted = _sum_known(
        None if line.allowed_cents is None else line.charge_cents - line.allowed_cents
        for line in claim.lines
    )
    patient_resp = _sum_known(line.patient_resp_cents for line in claim.lines)
    return [
        claim.control_number,
        claim.id,
        claim.patient_id,
        plan.payer_name,
        plan.payer_id,
        _date(service_dates[0] if service_dates else None),
        _date(service_dates[-1] if service_dates else None),
        claim.state,
        dollars(claim.total_charge_cents),
        _money(allowed),
        dollars(claim.total_paid_cents),
        _money(adjusted),
        _money(patient_resp),
        _local_date(claim.submitted_at, timezone),
        _local_date(claim.adjudicated_at, timezone),
    ]


def payment_row(charge: PatientCharge, *, timezone: tzinfo) -> list[str]:
    """One payments row, in :data:`PAYMENT_COLUMNS` order.

    Which bucket the amount lands in is not decided here: the row is put
    through :func:`app.payments.balance.patient_balance` on its own, so the
    export says exactly what a client's balance says, by construction. A row
    can land in none of them — a client-responsibility charge that another
    charge already settled is neither still owed nor money that arrived — and
    that row's buckets are all blank, which is the honest answer.
    """
    summary = patient_balance([charge])
    return [
        charge.id,
        _local_date(charge.created_at, timezone),
        charge.patient_id,
        charge.kind,
        charge.status,
        dollars(charge.amount_cents),
        charge.currency,
        _bucket(summary.owed_cents),
        _bucket(summary.collected_cents),
        _bucket(summary.written_off_cents),
        _bucket(summary.adjusted_cents),
        _bucket(summary.credited_cents),
        charge.appointment_id or "",
        charge.claim_id or "",
        charge.write_off_reason or "",
        charge.settled_by_charge_id or "",
    ]


def _stream(
    columns: tuple[str, ...], rows: Iterable[list[str]], tally: ExportTally
) -> Iterator[str]:
    """The header, then the rows, buffered up to :data:`CHUNK_BYTES` a time.

    The header is yielded before the first row is pulled, so a window with no
    rows in it is still a well-formed CSV rather than an empty body.
    """
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    for row in rows:
        writer.writerow(row)
        tally.rows += 1
        if buffer.tell() >= CHUNK_BYTES:
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
    if buffer.tell():
        yield buffer.getvalue()


def _sum_known(values: Iterable[int | None]) -> int | None:
    """Total the values anybody has actually stated; ``None`` if nobody has."""
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _money(cents: int | None) -> str:
    """Dollars and cents, or blank for a figure nobody has stated."""
    return "" if cents is None else dollars(cents)


def _bucket(cents: int) -> str:
    """A bucket column: the amount when the row belongs to it, else blank."""
    return dollars(cents) if cents else ""


def _date(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def _local_date(value: datetime | None, timezone: tzinfo) -> str:
    """A stored moment as the calendar date the practice had that day."""
    return value.astimezone(timezone).date().isoformat() if value is not None else ""
