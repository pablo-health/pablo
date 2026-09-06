# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One claim laid out the way a CMS-1500 reads, for a biller to transcribe.

The form a biller keys from has numbered boxes; this draws the boxes that
a professional claim from a session fills — 1a, 2, 3, 4, 5, 6, 7, 11, 11c,
21, 24A-J, 25, 26, 28, 29, 31, 33 and 33a — with the claim's stored values
in them. It is a layout, not the official red-ink form: the biller reads
it, they do not mail it.

Two layers: :func:`cms1500_fields` maps the claim onto box numbers (plain
strings, testable without a PDF), and :func:`render_cms1500` draws them
onto a letter page through :mod:`app.claims.cms1500_layout`. The drawing
is deterministic — the only clock is the ``now`` passed in, printed as the
box 31 date, and the PDF is written in reportlab's invariant mode (fixed
timestamp, fixed file id, no compression), so the same claim under the
same clock renders the same bytes. That is what lets a committed fixture stand as the visual test.

The form's diagnosis pointers are letters (A-L, box 21) rather than the
1-based numbers the stored line carries, so ``24E`` is translated here.
The practice's tax id is never on the claim — only its type and last four
are — so box 25 shows the masked form; the biller has the number.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from ..utcnow import utc_now
from .cms1500_layout import DX_LETTERS, draw_form
from .export import dollars

if TYPE_CHECKING:
    from datetime import date, datetime

    from ..models.claims import BillingProviderSnapshot, Claim, ClaimLine, PersonSnapshot


@dataclass(frozen=True)
class ServiceLineFields:
    """Box 24, one row: the lettered columns a biller keys."""

    date_from: str
    date_to: str
    place_of_service: str
    cpt: str
    modifiers: str
    dx_pointer: str
    charges: str
    units: str
    rendering_npi: str


@dataclass(frozen=True)
class Cms1500Fields:
    """Every box the layout prints, keyed the way the form numbers them."""

    box_1a_insured_id: str
    box_2_patient_name: str
    box_3_birth_date: str
    box_3_sex: str
    box_4_insured_name: str
    box_5_patient_address: list[str]
    box_6_relationship: str
    box_7_insured_address: list[str]
    box_11_group_number: str
    box_11c_plan_name: str
    box_21_diagnoses: list[str]
    box_24_lines: list[ServiceLineFields]
    box_25_tax_id: str
    box_25_tax_id_type: str
    box_26_account_number: str
    box_28_total_charge: str
    box_29_amount_paid: str
    box_31_signature: str
    box_31_date: str
    box_33_billing_provider: list[str]
    box_33a_billing_npi: str


def cms1500_fields(claim: Claim, *, now: datetime | None = None) -> Cms1500Fields:
    """The claim's values in the form's boxes, as strings ready to print."""
    signed_on = (now or utc_now()).date()
    plan = claim.subscriber_snapshot
    billing = claim.billing_snapshot.billing_provider
    rendering = claim.billing_snapshot.rendering_provider
    tax_type = (billing.tax_id_type or "").upper()
    return Cms1500Fields(
        box_1a_insured_id=plan.member_id,
        box_2_patient_name=_person_name(plan.patient),
        box_3_birth_date=_form_date(plan.patient.date_of_birth),
        box_3_sex=plan.patient.sex or "",
        box_4_insured_name=_person_name(plan.subscriber),
        box_5_patient_address=_address_lines(plan.patient),
        box_6_relationship=plan.relationship.replace("_", " ").title(),
        box_7_insured_address=_address_lines(plan.subscriber),
        box_11_group_number=plan.group_number or "",
        box_11c_plan_name=plan.plan_name or plan.payer_name,
        box_21_diagnoses=[code.upper() for code in claim.diagnosis_codes[: len(DX_LETTERS)]],
        box_24_lines=[
            _service_line(claim, line, rendering.npi or "")
            for line in sorted(claim.lines, key=lambda line: line.line_number)
        ],
        box_25_tax_id=_masked_tax_id(billing.tax_id_last4, tax_type),
        box_25_tax_id_type=tax_type,
        box_26_account_number=claim.control_number,
        box_28_total_charge=dollars(claim.total_charge_cents),
        box_29_amount_paid=dollars(claim.total_paid_cents),
        box_31_signature=" ".join(p for p in (rendering.first_name, rendering.last_name) if p),
        box_31_date=_form_date(signed_on),
        box_33_billing_provider=[billing.legal_name or "", *_address_lines(billing)],
        box_33a_billing_npi=billing.npi or "",
    )


def render_cms1500(claim: Claim, *, now: datetime | None = None) -> bytes:
    """The claim on a letter page in the CMS-1500 layout, as PDF bytes."""
    fields = cms1500_fields(claim, now=now)
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=letter, invariant=1, pageCompression=0)
    canvas.setTitle(f"Claim {claim.control_number}")
    canvas.setAuthor("")
    canvas.setSubject("CMS-1500 layout")
    canvas.setCreator("")
    draw_form(canvas, fields)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Field mapping helpers
# ---------------------------------------------------------------------------


def _service_line(claim: Claim, line: ClaimLine, rendering_npi: str) -> ServiceLineFields:
    pointers = "".join(
        DX_LETTERS[p - 1] for p in line.dx_pointers if 1 <= p <= len(claim.diagnosis_codes)
    )
    return ServiceLineFields(
        date_from=_form_date(line.service_date),
        date_to=_form_date(line.service_date),
        place_of_service=claim.place_of_service or "",
        cpt=line.cpt,
        modifiers=" ".join(line.modifiers),
        dx_pointer=pointers,
        charges=dollars(line.charge_cents),
        units=str(line.units),
        rendering_npi=rendering_npi,
    )


def _person_name(person: PersonSnapshot) -> str:
    if person.last_name and person.first_name:
        return f"{person.last_name}, {person.first_name}"
    return person.last_name or person.first_name or ""


def _address_lines(party: PersonSnapshot | BillingProviderSnapshot) -> list[str]:
    """Street, second line if any, ``City ST ZIP``, phone — blanks dropped."""
    locality = " ".join(p for p in (party.city, party.state, party.postal_code) if p)
    return [p for p in (party.address_line1, party.address_line2, locality, party.phone) if p]


def _form_date(value: date | None) -> str:
    """``MM DD YYYY``, the way the form's date boxes are segmented."""
    return value.strftime("%m %d %Y") if value is not None else ""


def _masked_tax_id(last4: str | None, tax_type: str) -> str:
    if not last4:
        return ""
    return f"XXX-XX-{last4}" if tax_type == "SSN" else f"XX-XXX{last4}"
