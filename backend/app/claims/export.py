# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The claim-ready package a practice hands its biller.

A practice that files through a biller rather than electronically hands
over the same assembled claims in the two shapes billers take: a CSV with
one row per service line, in the column order below, and a CMS-1500-layout
PDF per claim (:mod:`app.claims.cms1500`). Both read the stored claim and
its lines exactly as they were built — nothing here reaches back to the
appointment or the coverage, so the package and the claim never disagree.

The one value the claim does not carry is the practice's tax id: the
snapshot keeps its type and last four only, and a biller cannot file from
the last four. The caller decrypts the full id from the billing profile at
the moment of export and passes it in; it is rendered, with its EIN/SSN
qualifier, and goes nowhere else — never onto the claim, never into a log
or an audit payload. With no id on file the mask the claim carries is
printed instead, so the row still says what the practice knows.

Only a claim that has passed the scrub leaves here. :func:`check_export`
runs the scrub again over what is about to go out and refuses the whole
package with the findings when any claim has a blocking one, so a biller
never gets a row with a hole in it; the caller lists the findings and the
person fixes the claim. Nothing here changes a claim's state.

Money leaves as dollars with two decimals, converted once through
:mod:`app.money`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING

from ..money import cents_to_dollars
from .scrub import Finding, blocking, scrub

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import date

    from ..models.claims import BillingProviderSnapshot, Claim, ClaimLine

#: The columns of the biller CSV, in this order. Fixed: a biller maps an
#: import template to these once.
CSV_COLUMNS: tuple[str, ...] = (
    "control_number",
    "patient_last",
    "patient_first",
    "patient_dob",
    "patient_sex",
    "member_id",
    "group_number",
    "payer_name",
    "payer_id",
    "subscriber_relationship",
    "rendering_npi",
    "billing_npi",
    "tax_id",
    "tax_id_type",
    "taxonomy",
    "service_date",
    "place_of_service",
    "cpt",
    "modifiers",
    "units",
    "charge",
    "dx1",
    "dx2",
    "dx3",
    "dx4",
    "dx_pointers",
)

#: How many diagnosis columns the CSV carries — the four a service line can
#: point at.
_DX_COLUMNS = 4

#: Lists inside one CSV cell (modifiers, diagnosis pointers) are pipe-joined.
LIST_SEPARATOR = "|"


@dataclass(frozen=True)
class BlockedClaim:
    """A claim the export refused, with the scrub findings that stopped it."""

    claim_id: str
    control_number: str
    findings: list[Finding]


class ExportBlockedError(Exception):
    """At least one claim in the package has a blocking finding; nothing was built."""

    def __init__(self, blocked: list[BlockedClaim]) -> None:
        numbers = ", ".join(b.control_number for b in blocked)
        super().__init__(f"Cannot export: blocking findings on {numbers}")
        self.blocked = blocked


def exportable(claim: Claim) -> bool:
    """Has the claim passed validation? A draft never leaves the practice."""
    return claim.state != "draft"


def check_export(claims: Iterable[Claim], *, today: date | None = None) -> None:
    """Refuse the package unless every claim is clean and past ``draft``.

    Runs the scrub over each claim and raises :class:`ExportBlockedError`
    listing every claim with a blocking finding. A draft is reported as a
    blocked claim too — with a single finding saying so — rather than
    silently dropped, so a caller that was handed a draft by mistake hears
    about it. Callers that select by range exclude drafts before they get
    here.
    """
    blocked: list[BlockedClaim] = []
    for claim in claims:
        findings = blocking(scrub(claim, today=today))
        if not exportable(claim):
            findings = [_draft_finding(), *findings]
        if findings:
            blocked.append(BlockedClaim(claim.id, claim.control_number, findings))
    if blocked:
        raise ExportBlockedError(blocked)


def _draft_finding() -> Finding:
    return Finding(
        severity="blocking",
        code="claim_is_draft",
        message="The claim is a draft; validate it before exporting.",
        field="state",
    )


def claims_to_csv(claims: Sequence[Claim], *, tax_id: str | None) -> str:
    """The biller CSV: a header row, then one row per service line.

    Claims come out in the order given, lines in line-number order. Pure —
    :func:`check_export` is where refusal happens; this writes whatever it
    is given. ``tax_id`` is the practice's full id, decrypted by the caller.
    """
    out = StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for claim in claims:
        for line in sorted(claim.lines, key=lambda line: line.line_number):
            writer.writerow(csv_row(claim, line, tax_id=tax_id))
    return out.getvalue()


def csv_row(claim: Claim, line: ClaimLine, *, tax_id: str | None) -> list[str]:
    """One CSV row, in :data:`CSV_COLUMNS` order."""
    billing = claim.billing_snapshot.billing_provider
    rendering = claim.billing_snapshot.rendering_provider
    plan = claim.subscriber_snapshot
    patient = plan.patient
    diagnoses = list(claim.diagnosis_codes[:_DX_COLUMNS])
    diagnoses += [""] * (_DX_COLUMNS - len(diagnoses))
    return [
        claim.control_number,
        patient.last_name or "",
        patient.first_name or "",
        patient.date_of_birth.isoformat() if patient.date_of_birth else "",
        patient.sex or "",
        plan.member_id,
        plan.group_number or "",
        plan.payer_name,
        plan.payer_id,
        plan.relationship,
        rendering.npi or "",
        billing.npi or "",
        tax_id_for_export(tax_id, billing),
        (billing.tax_id_type or "").upper(),
        rendering.taxonomy_code or "",
        line.service_date.isoformat(),
        claim.place_of_service or "",
        line.cpt,
        LIST_SEPARATOR.join(line.modifiers),
        str(line.units),
        dollars(line.charge_cents),
        *diagnoses,
        LIST_SEPARATOR.join(str(p) for p in line.dx_pointers),
    ]


def dollars(cents: int) -> str:
    """Stored cents as the dollars-and-cents string a biller reads: ``150.00``."""
    return f"{cents_to_dollars(cents) or 0:.2f}"


def tax_id_for_export(tax_id: str | None, billing: BillingProviderSnapshot) -> str:
    """The full tax id when the practice has one on file, else the claim's mask.

    The mask takes the shape of the id's type — ``XXX-XX-1234`` for an SSN,
    ``XX-XXX1234`` for an EIN — so a biller reading it knows which number
    to ask the practice for.
    """
    if tax_id:
        return tax_id
    last4 = billing.tax_id_last4
    if not last4:
        return ""
    return f"XXX-XX-{last4}" if (billing.tax_id_type or "").upper() == "SSN" else f"XX-XXX{last4}"
