# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The chart PDF's billing section: the ledger, coverage and claims as tables.

A summary, not the documents: the statement and the superbill are their own
PDFs beside the chart in an archive. Each list has a table even when it is
empty, so a reader can tell "none on the chart" from "not in this copy".
Every value that came from a person is escaped, because ``Paragraph`` parses
its text as markup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Flowable, Paragraph, Spacer, Table, TableStyle

if TYPE_CHECKING:
    from collections.abc import Sequence

    from reportlab.lib.styles import ParagraphStyle, StyleSheet1

    from ..models.export import ExportClaim
    from .export_billing import BillingRecord


def _dollars(cents: int | None) -> str:
    return "" if cents is None else f"${cents / 100:,.2f}"


def _label(value: str | None) -> str:
    return (value or "").replace("_", " ").capitalize()


def _table(header: Sequence[str], rows: Sequence[Sequence[str]], widths: Sequence[float]) -> Table:
    body = [[escape(cell) for cell in row] for row in rows]
    table = Table([list(header), *body], colWidths=[w * inch for w in widths], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey),
            ]
        )
    )
    return table


def _charges(record: BillingRecord) -> Table:
    return _table(
        ("Recorded", "Kind", "Amount", "Status", "Method", "Reference", "Visit"),
        [
            (
                charge.recorded_at.strftime("%Y-%m-%d"),
                _label(charge.kind),
                _dollars(charge.amount_cents),
                _label(charge.status),
                _label(charge.method),
                charge.payment_reference or "",
                charge.appointment_id or "",
            )
            for charge in record.charges
        ],
        (0.8, 1.2, 0.8, 0.8, 0.7, 0.9, 1.3),
    )


def _coverage(record: BillingRecord) -> Table:
    return _table(
        ("Payer", "Payer ID", "Member ID", "Group", "Plan", "Subscriber", "Active"),
        [
            (
                plan.payer_name or "",
                plan.payer_id or "",
                plan.member_id,
                plan.group_number or "",
                plan.plan_name or "",
                _label(plan.subscriber_relationship),
                "Yes" if plan.active else "No",
            )
            for plan in record.coverage
        ],
        (1.4, 0.8, 1.0, 0.8, 1.2, 0.8, 0.5),
    )


def _claims(record: BillingRecord) -> Table:
    return _table(
        ("Control number", "Service dates", "Payer", "Codes", "Charged", "Paid", "Status"),
        [
            (
                claim.control_number,
                _dates(claim),
                claim.payer_name,
                ", ".join(f"{line.cpt} x{line.units}" for line in claim.lines),
                _dollars(claim.total_charge_cents),
                _dollars(claim.total_paid_cents),
                _label(claim.state),
            )
            for claim in record.claims
        ],
        (1.1, 1.2, 1.3, 1.0, 0.7, 0.7, 0.8),
    )


def _dates(claim: ExportClaim) -> str:
    dates = sorted({line.service_date.isoformat() for line in claim.lines})
    if not dates:
        return ""
    return dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}"


def billing_flowables(
    record: BillingRecord, heading: ParagraphStyle, styles: StyleSheet1
) -> list[Flowable]:
    """One headed section, a table per list, and the balance the statement carries."""
    sections = [
        ("Charges", len(record.charges), _charges(record)),
        ("Coverage", len(record.coverage), _coverage(record)),
        ("Claims", len(record.claims), _claims(record)),
    ]
    out: list[Flowable] = [
        Paragraph("Billing", heading),
        Paragraph(escape(f"Balance: {_dollars(record.balance_cents)}"), styles["Normal"]),
        Spacer(1, 0.15 * inch),
    ]
    for title, count, table in sections:
        out.append(Paragraph(f"{title} ({count})", styles["Heading3"]))
        out.append(table if count else Paragraph("None recorded.", styles["Normal"]))
        out.append(Spacer(1, 0.2 * inch))
    return out
