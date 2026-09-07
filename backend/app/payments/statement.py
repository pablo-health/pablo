# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The statement: what a client owes their practice, and how it got there.

A client who asks "why do I owe $62?" is not asking for a total. They are
asking which visit, what the practice charged, what their plan paid, what the
practice agreed to write off, and what they have already handed over. So the
document is one line per visit with those five figures beside each other, and
the balance is what is left when the arithmetic is done in front of them.

It is a *render*, not a second opinion. Every figure is copied from a record:
the visit and its service name from the appointment, the amount billed and
the payer's payment from the claim that was filed for it, and the client's own
side — adjusted, paid, owed — from :func:`app.payments.balance.patient_balance`
over the charge ledger. The balance rules live there and are not restated
here, so the chart header, this document and the practice-wide balances list
can never disagree about what a client owes.

A visit with no claim is a self-pay visit: what was billed is the session
charge on the ledger, and the payer paid nothing because there was no payer.

What is deliberately absent
---------------------------

No diagnosis codes and no member id. This is a document about money that gets
left on a desk, emailed by a practice through its own channels, or handed
across a waiting room, and a client's diagnoses have no bearing on what they
owe. The superbill — which a client sends to their own insurer, and which
needs those codes to be reimbursable — is a different document for a different
reader.

Determinism
-----------

The same inputs produce the same bytes: lines are sorted, money is integer
arithmetic throughout, and ReportLab is given its invariant flag so the PDF
carries no creation timestamp and no random document id. The one clock in the
path is ``generated_at``, which the caller supplies and the footer prints.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ..claims.superbill import current_claims
from ..money import cents_to_dollars
from .balance import patient_balance

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime, tzinfo

    from ..models.claims import Claim
    from ..models.payments import PatientCharge
    from ..scheduling_engine.models.appointment import Appointment

#: The ledger kind that is the practice's own charge for a visit. It is what a
#: self-pay visit was billed at, and it is the only kind that stands in for a
#: claim's total when no claim was ever filed.
_SESSION_KIND = "session"


@dataclass(frozen=True)
class PracticeBlock:
    """Who the client pays, as the statement prints it.

    No tax id and no NPI. Those exist on a document so an insurer can pay a
    provider; nobody needs either to write a cheque.
    """

    name: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    phone: str | None


@dataclass(frozen=True)
class StatementLine:
    """One visit's line: what it cost and where the money came from.

    ``appointment_id`` is ``None`` on the single trailing line that collects
    the ledger rows hanging off no visit — a late-cancellation fee, a credit,
    a balance the practice raised by hand. They are shown rather than dropped,
    because a statement whose lines do not sum to its total invites exactly
    the phone call it was meant to prevent.
    """

    appointment_id: str | None
    #: ``None`` when the visit itself is gone from the diary and no claim
    #: names a service date for it. The line still carries its money.
    service_date: date | None
    service: str
    charged_cents: int
    insurance_paid_cents: int
    adjusted_cents: int
    paid_cents: int
    owed_cents: int


@dataclass(frozen=True)
class Statement:
    """Everything on the page, in the order it is printed."""

    patient_id: str
    client_name: str
    practice: PracticeBlock
    lines: tuple[StatementLine, ...]
    #: The ledger rows the document was rendered from, for the audit entry.
    charge_ids: tuple[str, ...]
    generated_at: datetime

    @property
    def total_charged_cents(self) -> int:
        return sum(line.charged_cents for line in self.lines)

    @property
    def total_insurance_paid_cents(self) -> int:
        return sum(line.insurance_paid_cents for line in self.lines)

    @property
    def total_adjusted_cents(self) -> int:
        return sum(line.adjusted_cents for line in self.lines)

    @property
    def total_paid_cents(self) -> int:
        return sum(line.paid_cents for line in self.lines)

    @property
    def balance_cents(self) -> int:
        """Positive means the client owes; negative means the practice does."""
        return sum(line.owed_cents for line in self.lines)


@dataclass(frozen=True)
class _Billed:
    """What a visit was billed at, and what the payer paid on it."""

    charged_cents: int = 0
    insurance_paid_cents: int = 0
    service_date: date | None = None


def build_statement(  # noqa: PLR0913 — every record the document is copied from, keyword-only
    *,
    patient_id: str,
    client_name: str,
    charges: Sequence[PatientCharge],
    claims: Sequence[Claim],
    appointments: Sequence[Appointment],
    practice: PracticeBlock,
    timezone: tzinfo,
    generated_at: datetime,
) -> Statement:
    """The client's statement over their whole ledger.

    No period: a statement answers "what do I owe", and a balance carried from
    a visit outside an arbitrary window is exactly the figure a client would
    then ring up about. ``claims`` is every claim on the chart and
    ``appointments`` their whole diary; both are indexed by visit here.

    Never refuses. Unlike the superbill, which an insurer will reject if a
    field is missing, a statement with an unnamed service still tells the
    client what they owe — and a practice that cannot produce one has no way
    to ask for the money.
    """
    balance = patient_balance(charges)
    billed = _billed_by_visit(charges, claims)
    visits = {appointment.id: appointment for appointment in appointments}

    lines = [
        StatementLine(
            appointment_id=visit.appointment_id,
            service_date=_service_date(visit.appointment_id, visits, billed, timezone),
            service=_service(visit.appointment_id, visits),
            charged_cents=billed.get(visit.appointment_id, _Billed()).charged_cents,
            insurance_paid_cents=billed.get(visit.appointment_id, _Billed()).insurance_paid_cents,
            adjusted_cents=visit.adjusted_cents,
            paid_cents=visit.collected_cents,
            owed_cents=visit.balance_cents,
        )
        for visit in balance.by_visit
    ]
    return Statement(
        patient_id=patient_id,
        client_name=client_name,
        practice=practice,
        lines=tuple(sorted(lines, key=_line_order)),
        charge_ids=tuple(sorted(charge.id for charge in charges)),
        generated_at=generated_at,
    )


def _line_order(line: StatementLine) -> tuple[int, str, str]:
    """Chronological, with the visit-less line last and ties broken by id.

    The date sorts as a string rather than a ``date`` so the undated line can
    share the key; its leading flag already puts it at the end.
    """
    if line.appointment_id is None:
        return (1, "", "")
    return (0, line.service_date.isoformat() if line.service_date else "", line.appointment_id)


def _billed_by_visit(
    charges: Sequence[PatientCharge], claims: Sequence[Claim]
) -> dict[str | None, _Billed]:
    """What each visit was billed at, from its claim or from its session charge.

    The claim is the better source and wins where there is one: it carries
    what the practice actually billed the payer and what the payer paid back,
    neither of which the ledger records. Only the visits no standing claim
    covers fall back to the session charge on the ledger, which is what a
    self-pay visit was billed at.
    """
    billed: dict[str | None, _Billed] = {}
    for claim in current_claims(claims):
        for line in claim.lines:
            previous = billed.get(line.appointment_id, _Billed())
            billed[line.appointment_id] = _Billed(
                charged_cents=previous.charged_cents + line.charge_cents,
                insurance_paid_cents=previous.insurance_paid_cents + line.paid_cents,
                # The earliest service date on the visit, so a claim carrying
                # an add-on line dated a day later does not move the visit.
                service_date=min(
                    [d for d in (previous.service_date, line.service_date) if d is not None],
                    default=None,
                ),
            )

    # Snapshotted before the fallback runs: a visit is claim-covered or it is
    # not, and reading the dictionary as it grows would let the first session
    # charge on a self-pay visit shut out the second.
    claim_covered = set(billed)
    for charge in charges:
        if charge.kind != _SESSION_KIND or charge.appointment_id in claim_covered:
            continue
        previous = billed.get(charge.appointment_id, _Billed())
        billed[charge.appointment_id] = _Billed(
            charged_cents=previous.charged_cents + charge.amount_cents,
            insurance_paid_cents=previous.insurance_paid_cents,
            service_date=previous.service_date,
        )
    return billed


def _service_date(
    appointment_id: str | None,
    visits: dict[str, Appointment],
    billed: dict[str | None, _Billed],
    timezone: tzinfo,
) -> date | None:
    """When the visit happened, from the diary, else from its claim.

    The diary is authoritative and read in the practice's own timezone — a
    seven-in-the-evening session is on the day the client remembers it, not
    the UTC day after it.
    """
    appointment = visits.get(appointment_id) if appointment_id is not None else None
    if appointment is not None:
        return appointment.start_at.astimezone(timezone).date()
    return billed.get(appointment_id, _Billed()).service_date


def _service(appointment_id: str | None, visits: dict[str, Appointment]) -> str:
    """What the visit was, in the practice's own words.

    ``session_type`` is the appointment type's name as it was booked, so a
    renamed type does not rewrite what a client was told they attended. Never
    a CPT code: a statement is read by the person who owes the money.
    """
    if appointment_id is None:
        return "Other charges"
    appointment = visits.get(appointment_id)
    return appointment.session_type if appointment is not None else ""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_INK = colors.HexColor("#1f1a14")
_RULE = colors.HexColor("#d9d2c5")
_MUTED = colors.HexColor("#6b635a")


def render_statement_pdf(statement: Statement) -> bytes:
    """The document as PDF bytes. Same statement in, same bytes out."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.8 * inch,
        title="Statement",
        author=statement.practice.name or "",
        subject="Account statement",
        invariant=1,
        pageCompression=0,
    )
    doc.build(_story(statement))
    return buffer.getvalue()


def _story(statement: Statement) -> list:
    styles = getSampleStyleSheet()
    title = ParagraphStyle("st-title", parent=styles["Title"], alignment=0, textColor=_INK)
    heading = ParagraphStyle(
        "st-heading", parent=styles["Heading4"], textColor=_MUTED, spaceBefore=10, spaceAfter=4
    )
    body = ParagraphStyle("st-body", parent=styles["BodyText"], textColor=_INK)
    small = ParagraphStyle("st-small", parent=body, fontSize=8, leading=10, textColor=_MUTED)
    right = ParagraphStyle("st-right", parent=body, alignment=TA_RIGHT)

    return [
        Paragraph("Statement", title),
        Paragraph(f"Account summary for {statement.client_name}", body),
        Spacer(1, 8),
        Paragraph("Practice", heading),
        _pairs(_practice_rows(statement.practice)),
        Paragraph("Visits", heading),
        _visits_table(statement),
        Spacer(1, 6),
        Paragraph(_balance_line(statement.balance_cents), right),
        Spacer(1, 14),
        Paragraph(
            "Insurance paid is what the plan has paid so far, and adjusted is the amount the "
            "practice agreed not to bill. A visit still with the plan may change once it is "
            "settled.",
            small,
        ),
        Paragraph(f"Generated {_stamp(statement.generated_at)}", small),
    ]


def _balance_line(balance_cents: int) -> str:
    """The bottom line, in words a client can act on.

    A negative balance is stated as a credit rather than as a minus sign in
    front of money: the practice owes it back, and "-$30.00 due" is the kind
    of line somebody pays anyway.
    """
    if balance_cents < 0:
        return f"Credit balance: {_money(-balance_cents)}"
    return f"Balance due: {_money(balance_cents)}"


def _practice_rows(practice: PracticeBlock) -> list[tuple[str, str]]:
    address = ", ".join(
        part
        for part in (
            practice.address_line1,
            practice.address_line2,
            " ".join(p for p in (practice.city, practice.state, practice.postal_code) if p),
        )
        if part
    )
    rows = [("Practice", practice.name or "")]
    if address:
        rows.append(("Address", address))
    if practice.phone:
        rows.append(("Phone", practice.phone))
    return rows


def _pairs(rows: list[tuple[str, str]]) -> Table:
    table = Table(rows, colWidths=[1.4 * inch, 5.0 * inch], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
                ("FONT", (1, 0), (1, -1), "Helvetica", 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), _INK),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _visits_table(statement: Statement) -> Table:
    header = ["Date", "Service", "Charge", "Insurance paid", "Adjusted", "You paid", "You owe"]
    rows: list[list[str]] = [header]
    rows.extend(
        [
            _date(line.service_date) if line.service_date else "—",
            line.service,
            _money(line.charged_cents),
            _money(line.insurance_paid_cents),
            _money(line.adjusted_cents),
            _money(line.paid_cents),
            _money(line.owed_cents),
        ]
        for line in statement.lines
    )
    rows.append(
        [
            "Total",
            "",
            _money(statement.total_charged_cents),
            _money(statement.total_insurance_paid_cents),
            _money(statement.total_adjusted_cents),
            _money(statement.total_paid_cents),
            _money(statement.balance_cents),
        ]
    )
    widths = [
        0.85 * inch,
        1.35 * inch,
        0.8 * inch,
        1.0 * inch,
        0.85 * inch,
        0.85 * inch,
        0.85 * inch,
    ]
    table = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                ("FONT", (0, 1), (-1, -2), "Helvetica", 8),
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8),
                ("TEXTCOLOR", (0, 0), (-1, -1), _INK),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, _RULE),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, _RULE),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _date(value: date) -> str:
    return value.strftime("%m/%d/%Y")


def _stamp(value: datetime) -> str:
    return value.strftime("%m/%d/%Y %H:%M %Z").strip()


def _money(cents: int) -> str:
    """``$62.00``, and ``-$10.00`` rather than ``$-10.00`` below zero.

    The sign belongs in front of the whole amount, where a reader scanning a
    column of figures sees it before the digits.
    """
    sign = "-" if cents < 0 else ""
    return f"{sign}${cents_to_dollars(abs(cents)):,.2f}"
