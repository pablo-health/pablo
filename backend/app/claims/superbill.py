# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The superbill: an itemised receipt for a client's visits over a period.

A client seeing a clinician outside their plan pays the practice directly,
and many want a receipt that says what they paid for: to file with their own
insurer for reimbursement, to claim from an HSA or FSA, or to keep. This is
that document — the practice's identity, the client's, one line per service
with its date, code, fee and what was paid, and totals. The practice never
sends it anywhere.

It is a *render*, not a second assembly. A visit a claim was built for is
printed from that claim, so the receipt and the claim can never disagree
about a code or a fee. A visit with no claim — the ordinary case for a client
who pays out of pocket — is printed from the appointment: its service code,
modifiers and units, and the fee the statement shows for it (the session
charge on the ledger, else the client's rate or the appointment type's
default). Amounts paid come from the charge ledger, matched to the visit.
Nothing here is inferred, suggested or defaulted, and nothing here consults
a model: a value is copied from a record or it is arithmetic over values that
were.

The diagnosis is the client's choice
------------------------------------

A diagnosis is what makes a superbill reimbursable, and filing one puts a
mental-health diagnosis on the client's insurance record. Plenty of clients
pay out of pocket precisely to keep it off. So the diagnosis is printed only
when the clinician has recorded that this client wants it
(``patients.superbill_includes_diagnosis``); otherwise the column is not on
the page at all, and a visit without a diagnosis is not a gap.

Which claims count
------------------

A client's claims include drafts, filed claims, corrections and voids, and
the same visit can appear on several of them. The document wants each
visit once, as it currently stands, so :func:`current_claims` keeps a claim
only if it is not a void and nothing names it as a parent (a correction or a
void has replaced it), and then keeps one claim per visit — the newest.

Refusing
--------

A receipt missing something it needs is one the client finds out about
weeks later. So the build refuses, listing every gap with the field it lives
in, rather than rendering a blank: a missing provider NPI or practice
detail, a service with no code or no fee, and — only when the diagnosis is
being printed — a service with no diagnosis. The list uses the same
:func:`app.claims.validation.missing_fields` the scrub uses, so the two
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
from .service_codes import describe_service_code
from .validation import missing_fields

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from datetime import date, datetime, tzinfo

    from ..models.claims import BillingSnapshot, Claim, ClaimLine, PersonSnapshot
    from ..models.payments import PatientCharge
    from ..people_term import PeopleWords
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

#: The ledger kind that records what a visit was billed at. The statement
#: reads the same kind for a visit with no claim, so the two documents agree.
SESSION_KIND = "session"

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
    """One service on the receipt: a code on a date, its fee and payment.

    ``claim_id`` and ``line_id`` name the claim line it was printed from, and
    are ``None`` for a visit no claim was built for. ``diagnosis_codes`` is
    empty when the client's superbill leaves the diagnosis off.
    """

    claim_id: str | None
    line_id: str | None
    appointment_id: str | None
    service_date: date
    cpt: str
    modifiers: tuple[str, ...]
    units: int
    diagnosis_codes: tuple[str, ...]
    charge_cents: int
    paid_cents: int

    @property
    def description(self) -> str | None:
        return describe_service_code(self.cpt)


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
    includes_diagnosis: bool
    generated_at: datetime

    @property
    def total_charge_cents(self) -> int:
        return sum(line.charge_cents for line in self.lines)

    @property
    def total_paid_cents(self) -> int:
        return sum(line.paid_cents for line in self.lines)

    @property
    def claim_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(line.claim_id for line in self.lines if line.claim_id))

    @property
    def line_ids(self) -> tuple[str, ...]:
        return tuple(line.line_id for line in self.lines if line.line_id)

    @property
    def appointment_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(line.appointment_id for line in self.lines if line.appointment_id)
        )


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
    patient: PersonSnapshot,
    billing: BillingSnapshot,
    claims: Sequence[Claim],
    charges: Sequence[PatientCharge],
    appointments: Sequence[Appointment],
    rate_for: Callable[[Appointment], int | None],
    includes_diagnosis: bool,
    timezone: tzinfo,
    tax_id: str | None,
    license_for: Callable[[str], ClinicianProfile | None],
    generated_at: datetime,
    people: PeopleWords,
) -> Superbill:
    """The receipt for ``patient_id`` over ``period_start``..``period_end``, inclusive.

    ``patient`` and ``billing`` are the client and the two providers as they
    stand now (:func:`app.claims.assembly.client_snapshot` and
    :func:`~app.claims.assembly.billing_snapshot`), so a gap the clinician
    fixes on a profile is fixed on the next superbill. ``claims`` is every
    claim on the client's chart, ``charges`` their whole ledger and
    ``appointments`` their whole diary; the period is applied here.
    ``rate_for`` answers a visit's fee when no claim and no session charge
    does. ``tax_id`` is the practice's, decrypted by the caller, and
    ``license_for`` answers a clinician's licence from their profile.
    ``people`` is the clinician's word for the people they see, for the
    refusal messages.

    Raises :class:`SuperbillRefusedError` listing every gap, and never returns a
    document with one.
    """
    standing = current_claims(claims)
    services = _claim_services(standing, period_start, period_end, includes_diagnosis)
    covered = {s.appointment_id for s in services if s.appointment_id}
    services.extend(
        _visit_services(
            appointments,
            covered,
            charges,
            rate_for,
            period_start,
            period_end,
            timezone,
            generated_at,
            includes_diagnosis,
        )
    )
    services.sort(key=_service_order)
    if not services:
        raise SuperbillRefusedError(
            [
                Finding(
                    "blocking",
                    "no_visits",
                    f"There is no visit between {period_start.isoformat()} and "
                    f"{period_end.isoformat()}.",
                    "period",
                )
            ]
        )

    findings = _provider_findings(billing, tax_id)
    findings.extend(_patient_findings(patient))
    findings.extend(_service_findings(services, includes_diagnosis, people))
    if findings:
        raise SuperbillRefusedError(findings)

    paid, charge_ids = _paid_by_appointment(charges)
    return Superbill(
        patient_id=patient_id,
        period_start=period_start,
        period_end=period_end,
        provider=_provider_block(billing, tax_id, license_for),
        patient=patient,
        lines=tuple(_with_payments(services, paid)),
        charge_ids=charge_ids,
        includes_diagnosis=includes_diagnosis,
        generated_at=generated_at,
    )


@dataclass(frozen=True)
class _Service:
    """A line before its payment is allocated, from a claim line or from a visit."""

    claim_id: str | None
    line_id: str | None
    appointment_id: str | None
    service_date: date
    cpt: str
    modifiers: tuple[str, ...]
    units: int
    diagnosis_codes: tuple[str, ...]
    charge_cents: int
    #: Orders a claim's lines within one visit; a visit's own line is 1.
    line_number: int
    #: Orders two claims for the same date; ``None`` for a visit with no claim.
    built_at: datetime | None


def _service_order(s: _Service) -> tuple[date, str, str, int]:
    built = s.built_at.isoformat() if s.built_at is not None else ""
    return (s.service_date, s.appointment_id or "", built, s.line_number)


def _claim_services(
    claims: Sequence[Claim], start: date, end: date, includes_diagnosis: bool
) -> list[_Service]:
    return [
        _Service(
            claim_id=claim.id,
            line_id=line.id,
            appointment_id=line.appointment_id,
            service_date=line.service_date,
            cpt=line.cpt.strip(),
            modifiers=tuple(line.modifiers),
            units=line.units,
            diagnosis_codes=_pointed_diagnoses(claim, line) if includes_diagnosis else (),
            charge_cents=line.charge_cents,
            line_number=line.line_number,
            built_at=claim.created_at,
        )
        for claim in claims
        for line in claim.lines
        if start <= line.service_date <= end
    ]


def _pointed_diagnoses(claim: Claim, line: ClaimLine) -> tuple[str, ...]:
    """The diagnoses the line's pointers name, in pointer order, none invented."""
    codes = claim.diagnosis_codes
    return tuple(codes[p - 1] for p in line.dx_pointers if 1 <= p <= len(codes))


def _visit_services(  # noqa: PLR0913 — the period, the clock and the records a visit is priced from
    appointments: Sequence[Appointment],
    covered: set[str],
    charges: Sequence[PatientCharge],
    rate_for: Callable[[Appointment], int | None],
    start: date,
    end: date,
    timezone: tzinfo,
    now: datetime,
    includes_diagnosis: bool,
) -> list[_Service]:
    """A line for each visit in the period that no standing claim covers.

    The fee is what the statement shows for the visit: its session charges on
    the ledger, or — before the visit has been charged — the rate it would be
    charged at.
    """
    billed: dict[str, int] = {}
    for charge in charges:
        if charge.kind == SESSION_KIND and charge.appointment_id is not None:
            billed[charge.appointment_id] = billed.get(charge.appointment_id, 0) + (
                charge.amount_cents
            )
    services: list[_Service] = []
    for appointment in appointments:
        if appointment.status in _NOT_A_VISIT or appointment.start_at > now:
            continue
        service_date = appointment.start_at.astimezone(timezone).date()
        if not start <= service_date <= end or appointment.id in covered:
            continue
        fee = billed.get(appointment.id)
        if fee is None:
            fee = rate_for(appointment) or 0
        services.append(
            _Service(
                claim_id=None,
                line_id=None,
                appointment_id=appointment.id,
                service_date=service_date,
                cpt=(appointment.service_code or "").strip(),
                modifiers=tuple(appointment.modifiers or ()),
                units=appointment.unit_count or 1,
                diagnosis_codes=(
                    tuple(appointment.diagnosis_codes or ()) if includes_diagnosis else ()
                ),
                charge_cents=fee,
                line_number=1,
                built_at=None,
            )
        )
    return services


def _provider_block(
    billing: BillingSnapshot,
    tax_id: str | None,
    license_for: Callable[[str], ClinicianProfile | None],
) -> ProviderBlock:
    practice = billing.billing_provider
    rendering = billing.rendering_provider
    profile = license_for(rendering.user_id)
    return ProviderBlock(
        practice_name=practice.legal_name,
        tax_id=tax_id,
        tax_id_type=practice.tax_id_type,
        practice_npi=practice.npi,
        address_line1=practice.address_line1,
        address_line2=practice.address_line2,
        city=practice.city,
        state=practice.state,
        postal_code=practice.postal_code,
        phone=practice.phone,
        clinician_name=" ".join(p for p in (rendering.first_name, rendering.last_name) if p)
        or None,
        clinician_npi=rendering.npi,
        taxonomy_code=rendering.taxonomy_code,
        license_number=profile.license_number if profile is not None else None,
        license_state=profile.license_state if profile is not None else None,
    )


def _provider_findings(billing: BillingSnapshot, tax_id: str | None) -> list[Finding]:
    practice = {**billing.billing_provider.model_dump(), "tax_id": tax_id}
    findings = [
        _missing("rendering_provider", name)
        for name in missing_fields(billing.rendering_provider, _RENDERING_PROVIDER_REQUIRED)
    ]
    findings.extend(
        _missing("billing_provider", name)
        for name in missing_fields(practice, _BILLING_PROVIDER_REQUIRED)
    )
    return findings


def _patient_findings(patient: PersonSnapshot) -> list[Finding]:
    return [_missing("patient", name) for name in missing_fields(patient, _PATIENT_REQUIRED)]


def _service_findings(
    services: Sequence[_Service], includes_diagnosis: bool, people: PeopleWords
) -> list[Finding]:
    findings: list[Finding] = []
    for position, service in enumerate(services):
        when = service.service_date.isoformat()
        # A line printed from a claim keeps the claim's values until it is rebuilt.
        rebuild = ", then rebuild the claim" if service.claim_id else ""
        if not service.cpt:
            findings.append(
                Finding(
                    "blocking",
                    "missing_field",
                    f"The service on {when} has no service code. Add it to the "
                    f"appointment{rebuild}.",
                    f"lines[{position}].cpt",
                )
            )
        if includes_diagnosis and not service.diagnosis_codes:
            findings.append(
                Finding(
                    "blocking",
                    "missing_field",
                    f"The service on {when} has no diagnosis. Add one to the "
                    f'appointment{rebuild}, or turn off "Include the diagnosis" for this '
                    f"{people.one}.",
                    f"lines[{position}].diagnosis_codes",
                )
            )
        if service.charge_cents <= 0:
            findings.append(
                Finding(
                    "blocking",
                    "charge_zero",
                    f"The service on {when} has no fee. Set a rate on the {people.one} or the "
                    f"appointment type{rebuild}.",
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


def _with_payments(services: Sequence[_Service], paid: dict[str, int]) -> list[SuperbillLine]:
    """Spread each visit's payment over its lines, in line order.

    A visit with an add-on has two lines and one payment. The payment fills
    the first line up to its fee, then the next; whatever is left over after
    the last line's fee stays on the last line, so the paid column always
    sums to what the ledger says was received.
    """
    remaining = dict(paid)
    last_for_visit = {s.appointment_id: i for i, s in enumerate(services) if s.appointment_id}
    out: list[SuperbillLine] = []
    for index, service in enumerate(services):
        allocated = 0
        if service.appointment_id is not None:
            available = remaining.get(service.appointment_id, 0)
            is_last = last_for_visit[service.appointment_id] == index
            allocated = available if is_last else min(available, service.charge_cents)
            remaining[service.appointment_id] = available - allocated
        out.append(
            SuperbillLine(
                claim_id=service.claim_id,
                line_id=service.line_id,
                appointment_id=service.appointment_id,
                service_date=service.service_date,
                cpt=service.cpt,
                modifiers=service.modifiers,
                units=service.units,
                diagnosis_codes=service.diagnosis_codes,
                charge_cents=service.charge_cents,
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


def render_superbill_pdf(superbill: Superbill, people: PeopleWords) -> bytes:
    """The document as PDF bytes. Same superbill in, same bytes out.

    ``people`` names the person the receipt is for in the clinician's own word.
    """
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
        subject="Itemized receipt",
        invariant=1,
        pageCompression=0,
    )
    doc.build(_story(superbill, people))
    return buffer.getvalue()


def _story(superbill: Superbill, people: PeopleWords) -> list:
    styles = getSampleStyleSheet()
    title = ParagraphStyle("sb-title", parent=styles["Title"], alignment=0, textColor=_INK)
    heading = ParagraphStyle(
        "sb-heading", parent=styles["Heading4"], textColor=_MUTED, spaceBefore=10, spaceAfter=4
    )
    body = ParagraphStyle("sb-body", parent=styles["BodyText"], textColor=_INK)
    small = ParagraphStyle("sb-small", parent=body, fontSize=8, leading=10, textColor=_MUTED)
    right = ParagraphStyle("sb-right", parent=body, alignment=TA_RIGHT)
    cell = ParagraphStyle("sb-cell", parent=body, fontSize=9, leading=11)

    period = f"{_date(superbill.period_start)} to {_date(superbill.period_end)}"
    story: list = [
        Paragraph("Superbill", title),
        Paragraph(f"Itemized receipt for services from {period}", body),
        Spacer(1, 8),
        Paragraph("Provider", heading),
        _pairs(_provider_rows(superbill.provider)),
        Paragraph(people.One, heading),
        _pairs(_patient_rows(superbill.patient)),
        Paragraph("Services", heading),
        _services_table(superbill, cell),
        Spacer(1, 6),
        Paragraph(
            f"Balance: {_money(superbill.total_charge_cents - superbill.total_paid_cents)}",
            right,
        ),
        Spacer(1, 14),
        Paragraph(
            "A receipt for services rendered and payments received as shown. It is not a claim.",
            small,
        ),
        Paragraph(f"Issued {_stamp(superbill.generated_at)}", small),
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


def _services_table(superbill: Superbill, cell: ParagraphStyle) -> Table:
    """The services, with a diagnosis column only when the client wants one.

    Leaving the column off, rather than printing it empty, is the point: a
    blank diagnosis reads as something missing, and nothing is.
    """
    dx = superbill.includes_diagnosis
    header: list[object] = ["Date", "Code", "Description", *(["Diagnosis"] if dx else [])]
    header += ["Units", "Fee", "Paid"]
    rows: list[list[object]] = [header]
    for line in superbill.lines:
        rows.append(
            [
                _date(line.service_date),
                " ".join((line.cpt, *line.modifiers)),
                Paragraph(line.description or "", cell),
                *([", ".join(line.diagnosis_codes)] if dx else []),
                str(line.units),
                _money(line.charge_cents),
                _money(line.paid_cents),
            ]
        )
    blanks = ["", "", *([""] if dx else []), ""]
    rows.append(
        [
            "Total",
            *blanks,
            _money(superbill.total_charge_cents),
            _money(superbill.total_paid_cents),
        ]
    )
    description_width = 2.0 if dx else 3.0
    widths = [0.85, 0.85, description_width, *([1.0] if dx else []), 0.45, 0.85, 0.85]
    first_number = 4 if dx else 3
    table = Table(rows, colWidths=[w * inch for w in widths], hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("FONT", (0, 1), (-1, -2), "Helvetica", 9),
                ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), _INK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (first_number, 0), (-1, -1), "RIGHT"),
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
