# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The superbill: an itemised receipt rendered from the client's claims.

A client seeing an out-of-network clinician files for reimbursement with
their own insurer, and what they file is this document — the practice's
identity, the client's, one line per service with its date, code, diagnosis,
fee and what was paid, and totals. The practice never sends it anywhere.

It is a *render*, not a second assembly. Every line comes from a claim that
:mod:`app.claims.assembly` already built from the session, so the receipt,
the claim and the unbilled queue can never disagree about a code or a fee.
Amounts paid come from the charge ledger, matched to the visit. Nothing here
is inferred, suggested or defaulted, and nothing here consults a model: a
value is copied from a record or it is arithmetic over values that were.

Which claims count
------------------

A client's claims include drafts, filed claims, corrections and voids, and
the same visit can appear on several of them. The document wants each
visit once, as it currently stands, so :func:`current_claims` keeps a claim
only if it is not a void and nothing names it as a parent (a correction or a
void has replaced it), and then keeps one claim per visit — the newest.

Refusing
--------

A superbill missing something the insurer needs is a denied claim the
client finds out about weeks later. So the build refuses, listing every
gap with the field it lives in, rather than rendering a blank: a missing
provider NPI, a line with no service code, no diagnosis or no rate, and a
visit in the period that has no claim built for it yet. The list uses the
same :func:`app.claims.validation.missing_fields` the scrub uses, so the two
surfaces agree about what "missing" means.

Determinism
-----------

The same inputs produce the same bytes: the lines are sorted, the money is
integer arithmetic, and the PDF is written with ReportLab's invariant flag
(fixed creation date, no random document id). The one clock in the path is
``generated_at``, which the caller supplies and the footer prints.
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

from ..money import cents_to_dollars
from .scrub import Finding
from .validation import missing_fields

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from datetime import date, datetime, tzinfo

    from ..models.claims import Claim, ClaimLine, PersonSnapshot
    from ..models.payments import PatientCharge
    from ..repositories.clinician_profile import ClinicianProfile
    from ..scheduling_engine.models.appointment import Appointment


class SuperbillRefusedError(Exception):
    """The document cannot be issued; ``findings`` says what is missing."""

    def __init__(self, findings: list[Finding]) -> None:
        super().__init__("The superbill is missing required information.")
        self.findings = findings


#: The ledger status that counts as money received. A refund is recorded
#: without its amount, a dispute is money on hold, and a failure or a
#: pending attempt is not a payment; none of those belong on a receipt.
PAID_STATUS = "succeeded"

#: Appointment statuses that are not a visit: never agreed to, or called off.
_NOT_A_VISIT: frozenset[str] = frozenset({"pending", "cancelled"})

_RENDERING_PROVIDER_REQUIRED = ["first_name", "last_name", "npi"]
_BILLING_PROVIDER_REQUIRED = [
    "legal_name",
    "address_line1",
    "city",
    "state",
    "postal_code",
    "phone",
    "tax_id_type",
    "tax_id",
]
_PATIENT_REQUIRED = ["first_name", "last_name", "date_of_birth"]


@dataclass(frozen=True)
class ProviderBlock:
    """Who rendered the service and who is paid for it, as the receipt shows them."""

    practice_name: str | None
    tax_id: str | None
    tax_id_type: str | None
    practice_npi: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    phone: str | None
    clinician_name: str | None
    clinician_npi: str | None
    taxonomy_code: str | None
    license_number: str | None
    license_state: str | None


@dataclass(frozen=True)
class SuperbillLine:
    """One service on the receipt: a code on a date, its diagnoses, fee and payment."""

    claim_id: str
    line_id: str
    appointment_id: str | None
    service_date: date
    cpt: str
    modifiers: tuple[str, ...]
    units: int
    diagnosis_codes: tuple[str, ...]
    charge_cents: int
    paid_cents: int


@dataclass(frozen=True)
class Superbill:
    """Everything on the page, in the order it is printed."""

    patient_id: str
    period_start: date
    period_end: date
    provider: ProviderBlock
    patient: PersonSnapshot
    lines: tuple[SuperbillLine, ...]
    charge_ids: tuple[str, ...]
    generated_at: datetime

    @property
    def total_charge_cents(self) -> int:
        return sum(line.charge_cents for line in self.lines)

    @property
    def total_paid_cents(self) -> int:
        return sum(line.paid_cents for line in self.lines)

    @property
    def claim_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(line.claim_id for line in self.lines))

    @property
    def line_ids(self) -> tuple[str, ...]:
        return tuple(line.line_id for line in self.lines)


# ---------------------------------------------------------------------------
# Choosing the claims
# ---------------------------------------------------------------------------


def current_claims(claims: Iterable[Claim]) -> list[Claim]:
    """The claims that still stand, one per visit.

    Drops voids and anything a correction or a void has replaced, then keeps
    the newest remaining claim for each appointment. A claim whose visit
    line names no appointment is kept as it is.
    """
    every = list(claims)
    replaced = {claim.parent_claim_id for claim in every if claim.parent_claim_id}
    standing = [c for c in every if c.frequency_code != "8" and c.id not in replaced]
    standing.sort(key=lambda c: (c.created_at, c.id))
    newest_by_visit: dict[str, Claim] = {}
    unattached: list[Claim] = []
    for claim in standing:
        appointment_id = _visit_appointment_id(claim)
        if appointment_id is None:
            unattached.append(claim)
        else:
            newest_by_visit[appointment_id] = claim
    return sorted([*newest_by_visit.values(), *unattached], key=lambda c: (c.created_at, c.id))


def _visit_appointment_id(claim: Claim) -> str | None:
    return next((line.appointment_id for line in claim.lines if line.appointment_id), None)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def build_superbill(  # noqa: PLR0913 — every record the receipt is copied from, keyword-only
    *,
    patient_id: str,
    period_start: date,
    period_end: date,
    claims: Sequence[Claim],
    charges: Sequence[PatientCharge],
    appointments: Sequence[Appointment],
    timezone: tzinfo,
    tax_id: str | None,
    license_for: Callable[[str], ClinicianProfile | None],
    generated_at: datetime,
) -> Superbill:
    """The receipt for ``patient_id`` over ``period_start``..``period_end``, inclusive.

    ``claims`` is every claim on the client's chart, ``charges`` their whole
    ledger and ``appointments`` their whole diary; the period is applied
    here. ``tax_id`` is the practice's, decrypted by the caller, and
    ``license_for`` answers a clinician's licence from their profile — the
    one provider fact a claim does not snapshot.

    Raises :class:`SuperbillRefusedError` listing every gap, and never returns a
    document with one.
    """
    standing = current_claims(claims)
    lines = _lines_in_period(standing, period_start, period_end)
    findings = _visits_without_a_claim(
        appointments, lines, period_start, period_end, timezone, generated_at
    )
    if not lines:
        findings.append(
            Finding(
                "blocking",
                "no_visits",
                f"No claim covers a visit between {period_start.isoformat()} and "
                f"{period_end.isoformat()}. Build a claim from each session first.",
                "period",
            )
        )
        raise SuperbillRefusedError(findings)

    newest = max(
        (claim for claim in standing if any(line.claim_id == claim.id for line in lines)),
        key=lambda c: (c.created_at, c.id),
    )
    provider = _provider_block(newest, tax_id, license_for)
    patient = newest.subscriber_snapshot.patient
    findings.extend(_provider_findings(newest, tax_id))
    findings.extend(_patient_findings(patient))
    findings.extend(_line_findings(lines))
    if findings:
        raise SuperbillRefusedError(findings)

    paid, charge_ids = _paid_by_appointment(charges)
    return Superbill(
        patient_id=patient_id,
        period_start=period_start,
        period_end=period_end,
        provider=provider,
        patient=patient,
        lines=tuple(_with_payments(lines, paid)),
        charge_ids=charge_ids,
        generated_at=generated_at,
    )


@dataclass(frozen=True)
class _Unpaid:
    """A line before its payment is allocated: the claim it sits on, and its diagnoses."""

    claim: Claim
    line: ClaimLine
    diagnosis_codes: tuple[str, ...]

    @property
    def claim_id(self) -> str:
        return self.claim.id


def _lines_in_period(claims: Sequence[Claim], start: date, end: date) -> list[_Unpaid]:
    found = [
        _Unpaid(claim, line, _pointed_diagnoses(claim, line))
        for claim in claims
        for line in claim.lines
        if start <= line.service_date <= end
    ]
    found.sort(
        key=lambda u: (
            u.line.service_date,
            u.line.appointment_id or "",
            u.claim.created_at,
            u.line.line_number,
        )
    )
    return found


def _pointed_diagnoses(claim: Claim, line: ClaimLine) -> tuple[str, ...]:
    """The diagnoses the line's pointers name, in pointer order, none invented."""
    codes = claim.diagnosis_codes
    return tuple(codes[p - 1] for p in line.dx_pointers if 1 <= p <= len(codes))


def _visits_without_a_claim(  # noqa: PLR0913 — the period, the clock and the two lists it filters
    appointments: Sequence[Appointment],
    lines: Sequence[_Unpaid],
    start: date,
    end: date,
    timezone: tzinfo,
    now: datetime,
) -> list[Finding]:
    """A visit in the period that no standing claim covers is reported, not skipped."""
    covered = {u.line.appointment_id for u in lines if u.line.appointment_id}
    findings: list[Finding] = []
    for appointment in sorted(appointments, key=lambda a: (a.start_at, a.id)):
        if appointment.status in _NOT_A_VISIT or appointment.start_at > now:
            continue
        service_date = appointment.start_at.astimezone(timezone).date()
        if not start <= service_date <= end or appointment.id in covered:
            continue
        findings.append(
            Finding(
                "blocking",
                "visit_without_claim",
                f"The visit on {service_date.isoformat()} has no claim built from it. "
                "Build one from the session, then generate the superbill.",
                f"appointments[{appointment.id}]",
            )
        )
    return findings


def _provider_block(
    claim: Claim, tax_id: str | None, license_for: Callable[[str], ClinicianProfile | None]
) -> ProviderBlock:
    billing = claim.billing_snapshot.billing_provider
    rendering = claim.billing_snapshot.rendering_provider
    profile = license_for(rendering.user_id)
    return ProviderBlock(
        practice_name=billing.legal_name,
        tax_id=tax_id,
        tax_id_type=billing.tax_id_type,
        practice_npi=billing.npi,
        address_line1=billing.address_line1,
        address_line2=billing.address_line2,
        city=billing.city,
        state=billing.state,
        postal_code=billing.postal_code,
        phone=billing.phone,
        clinician_name=" ".join(p for p in (rendering.first_name, rendering.last_name) if p)
        or None,
        clinician_npi=rendering.npi,
        taxonomy_code=rendering.taxonomy_code,
        license_number=profile.license_number if profile is not None else None,
        license_state=profile.license_state if profile is not None else None,
    )


def _provider_findings(claim: Claim, tax_id: str | None) -> list[Finding]:
    rendering = claim.billing_snapshot.rendering_provider
    billing = {**claim.billing_snapshot.billing_provider.model_dump(), "tax_id": tax_id}
    findings = [
        _missing("rendering_provider", name)
        for name in missing_fields(rendering, _RENDERING_PROVIDER_REQUIRED)
    ]
    findings.extend(
        _missing("billing_provider", name)
        for name in missing_fields(billing, _BILLING_PROVIDER_REQUIRED)
    )
    return findings


def _patient_findings(patient: PersonSnapshot) -> list[Finding]:
    return [_missing("patient", name) for name in missing_fields(patient, _PATIENT_REQUIRED)]


def _line_findings(lines: Sequence[_Unpaid]) -> list[Finding]:
    findings: list[Finding] = []
    for position, unpaid in enumerate(lines):
        line = unpaid.line
        when = line.service_date.isoformat()
        if not line.cpt.strip():
            findings.append(
                Finding(
                    "blocking",
                    "missing_field",
                    f"The service on {when} has no service code.",
                    f"lines[{position}].cpt",
                )
            )
        if not unpaid.diagnosis_codes:
            findings.append(
                Finding(
                    "blocking",
                    "missing_field",
                    f"The service on {when} has no diagnosis code.",
                    f"lines[{position}].diagnosis_codes",
                )
            )
        if line.charge_cents <= 0:
            findings.append(
                Finding(
                    "blocking",
                    "charge_zero",
                    f"The service on {when} has no fee. Set a rate on the client or the "
                    "appointment type and rebuild the claim.",
                    f"lines[{position}].charge_cents",
                )
            )
    return findings


def _missing(prefix: str, name: str) -> Finding:
    return Finding("blocking", "missing_field", f"{prefix}.{name} is required.", f"{prefix}.{name}")


def _paid_by_appointment(
    charges: Sequence[PatientCharge],
) -> tuple[dict[str, int], tuple[str, ...]]:
    """Money received per visit, and the ledger rows it came from.

    A charge that names no appointment cannot be put against a service line,
    so it is not on the document: the receipt totals only what it itemises.
    """
    paid: dict[str, int] = {}
    used: list[str] = []
    for charge in sorted(charges, key=lambda c: (c.created_at, c.id)):
        if charge.status != PAID_STATUS or charge.appointment_id is None:
            continue
        paid[charge.appointment_id] = paid.get(charge.appointment_id, 0) + charge.amount_cents
        used.append(charge.id)
    return paid, tuple(used)


def _with_payments(lines: Sequence[_Unpaid], paid: dict[str, int]) -> list[SuperbillLine]:
    """Spread each visit's payment over its lines, in line order.

    A visit with an add-on has two lines and one payment. The payment fills
    the first line up to its fee, then the next; whatever is left over after
    the last line's fee stays on the last line, so the paid column always
    sums to what the ledger says was received.
    """
    remaining = dict(paid)
    last_for_visit = {
        u.line.appointment_id: u.line.id for u in lines if u.line.appointment_id is not None
    }
    out: list[SuperbillLine] = []
    for unpaid in lines:
        line = unpaid.line
        allocated = 0
        if line.appointment_id is not None:
            available = remaining.get(line.appointment_id, 0)
            is_last = last_for_visit[line.appointment_id] == line.id
            allocated = available if is_last else min(available, line.charge_cents)
            remaining[line.appointment_id] = available - allocated
        out.append(
            SuperbillLine(
                claim_id=unpaid.claim_id,
                line_id=line.id,
                appointment_id=line.appointment_id,
                service_date=line.service_date,
                cpt=line.cpt,
                modifiers=tuple(line.modifiers),
                units=line.units,
                diagnosis_codes=unpaid.diagnosis_codes,
                charge_cents=line.charge_cents,
                paid_cents=allocated,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_INK = colors.HexColor("#1f1a14")
_RULE = colors.HexColor("#d9d2c5")
_MUTED = colors.HexColor("#6b635a")


def render_superbill_pdf(superbill: Superbill) -> bytes:
    """The document as PDF bytes. Same superbill in, same bytes out."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.8 * inch,
        title="Superbill",
        author=superbill.provider.practice_name or "",
        subject="Itemised receipt for out-of-network reimbursement",
        invariant=1,
        pageCompression=0,
    )
    doc.build(_story(superbill))
    return buffer.getvalue()


def _story(superbill: Superbill) -> list:
    styles = getSampleStyleSheet()
    title = ParagraphStyle("sb-title", parent=styles["Title"], alignment=0, textColor=_INK)
    heading = ParagraphStyle(
        "sb-heading", parent=styles["Heading4"], textColor=_MUTED, spaceBefore=10, spaceAfter=4
    )
    body = ParagraphStyle("sb-body", parent=styles["BodyText"], textColor=_INK)
    small = ParagraphStyle("sb-small", parent=body, fontSize=8, leading=10, textColor=_MUTED)
    right = ParagraphStyle("sb-right", parent=body, alignment=TA_RIGHT)

    period = f"{_date(superbill.period_start)} to {_date(superbill.period_end)}"
    story: list = [
        Paragraph("Superbill", title),
        Paragraph(f"Itemised receipt for services from {period}", body),
        Spacer(1, 8),
        Paragraph("Provider", heading),
        _pairs(_provider_rows(superbill.provider)),
        Paragraph("Client", heading),
        _pairs(_patient_rows(superbill.patient)),
        Paragraph("Services", heading),
        _services_table(superbill),
        Spacer(1, 6),
        Paragraph(
            f"Balance: {_money(superbill.total_charge_cents - superbill.total_paid_cents)}",
            right,
        ),
        Spacer(1, 14),
        Paragraph(
            "This is a receipt for services already rendered and paid for as shown. It is "
            "not a claim; the client submits it to their own insurer.",
            small,
        ),
        Paragraph(f"Generated {_stamp(superbill.generated_at)}", small),
    ]
    return story


def _provider_rows(provider: ProviderBlock) -> list[tuple[str, str]]:
    address = ", ".join(
        p
        for p in (
            provider.address_line1,
            provider.address_line2,
            " ".join(q for q in (provider.city, provider.state, provider.postal_code) if q),
        )
        if p
    )
    tax_label = (provider.tax_id_type or "tax id").upper()
    licence = " ".join(p for p in (provider.license_number, provider.license_state) if p)
    rows = [
        ("Practice", provider.practice_name or ""),
        ("Address", address),
        ("Phone", provider.phone or ""),
        (tax_label, provider.tax_id or ""),
        ("Clinician", provider.clinician_name or ""),
        ("Clinician NPI", provider.clinician_npi or ""),
    ]
    if provider.practice_npi and provider.practice_npi != provider.clinician_npi:
        rows.insert(3, ("Practice NPI", provider.practice_npi))
    if provider.taxonomy_code:
        rows.append(("Taxonomy", provider.taxonomy_code))
    if licence:
        rows.append(("License", licence))
    return rows


def _patient_rows(patient: PersonSnapshot) -> list[tuple[str, str]]:
    address = ", ".join(
        p
        for p in (
            patient.address_line1,
            patient.address_line2,
            " ".join(q for q in (patient.city, patient.state, patient.postal_code) if q),
        )
        if p
    )
    rows = [
        ("Name", " ".join(p for p in (patient.first_name, patient.last_name) if p)),
        ("Date of birth", _date(patient.date_of_birth) if patient.date_of_birth else ""),
    ]
    if address:
        rows.append(("Address", address))
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


def _services_table(superbill: Superbill) -> Table:
    header = ["Date of service", "Service", "Diagnosis", "Units", "Fee", "Paid"]
    rows: list[list[str]] = [header]
    for line in superbill.lines:
        service = " ".join((line.cpt, *line.modifiers))
        rows.append(
            [
                _date(line.service_date),
                service,
                ", ".join(line.diagnosis_codes),
                str(line.units),
                _money(line.charge_cents),
                _money(line.paid_cents),
            ]
        )
    rows.append(
        [
            "Total",
            "",
            "",
            "",
            _money(superbill.total_charge_cents),
            _money(superbill.total_paid_cents),
        ]
    )
    widths = [1.1 * inch, 1.3 * inch, 1.6 * inch, 0.5 * inch, 0.95 * inch, 0.95 * inch]
    table = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("FONT", (0, 1), (-1, -2), "Helvetica", 9),
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), _INK),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
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
    return f"${cents_to_dollars(cents):,.2f}"
