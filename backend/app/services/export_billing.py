# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The billing record, as the export carries it.

The ledger, every plan on the chart and every claim filed, each read through
the repository its own screen uses, on the exporting clinician's session, so
a row that clinician cannot open is not in the copy either. Beside the data,
the documents the practice already issues: the client's statement, and a
superbill for each visit the superbill route would render one for.

The card on file is not read here and is in no export. How a payment was
taken is a category (``card``); the card itself, its last four digits and
the processor's identifiers stay with the processor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from ..claims.superbill import (
    SuperbillRefusedError,
    build_superbill,
    render_superbill_pdf,
)
from ..models.export import (
    ClaimEvent,
    ClaimLineAdjustment,
    Coverage,
    CoverageSubscriber,
    ExportCharge,
    ExportClaim,
    ExportClaimLine,
    PaymentMethod,
)
from ..payments.balance import outcome_is_known
from ..payments.statement import build_statement, render_statement_pdf

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, tzinfo

    from ..models import Patient
    from ..models.claims import Claim, ClaimLine, ClaimReceipt
    from ..models.coverage import PatientCoverage, Payer
    from ..models.payments import PatientCharge
    from ..payments.statement import PracticeBlock
    from ..people_term import PeopleWords
    from ..repositories.claim_receipts import ClaimReceiptRepository
    from ..repositories.claims import ClaimRepository
    from ..repositories.clinician_profile import ClinicianProfile
    from ..repositories.coverage import PatientCoverageRepository, PayerRepository
    from ..repositories.patient_payment import PatientPaymentRepository
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.repositories.appointment import AppointmentRepository


@dataclass(frozen=True)
class BillingRecord:
    """The billing lists for ``patient.json`` and ``chart.pdf``, and the documents beside them.

    ``statement`` is the client's statement and ``superbill`` the receipt for
    every visit a claim was filed for, each as a PDF. Both are ``None`` when
    no source was wired, and the superbill is ``None`` when the route would
    have refused it.
    """

    charges: list[ExportCharge] = field(default_factory=list)
    coverage: list[Coverage] = field(default_factory=list)
    claims: list[ExportClaim] = field(default_factory=list)
    statement: bytes | None = None
    superbill: bytes | None = None
    balance_cents: int = 0


#: Given the patient, the exporting clinician's id and the export time, the billing record.
BillingRecordReader = Callable[["Patient", str, "datetime"], BillingRecord]

STATEMENT_PATH = "billing/statement.pdf"
SUPERBILL_PATH = "billing/superbill.pdf"


class BillingRecordSource:
    """Reads one patient's billing record through the billing screens' repositories.

    ``practice`` and ``tax_id`` are read once per export, at the moment the
    documents are rendered: the tax id is decrypted for the superbill alone
    and goes nowhere else. ``timezone`` is the exporting clinician's calendar
    zone, which is what puts a visit on its local date.
    """

    def __init__(
        self,
        *,
        payments: PatientPaymentRepository,
        coverage: PatientCoverageRepository,
        payers: PayerRepository,
        claims: ClaimRepository,
        receipts: ClaimReceiptRepository,
        appointments: AppointmentRepository,
        practice: Callable[[], PracticeBlock],
        tax_id: Callable[[], str | None],
        license_for: Callable[[str], ClinicianProfile | None],
        timezone: Callable[[str], tzinfo],
        people: Callable[[str], PeopleWords],
    ) -> None:
        self._payments = payments
        self._coverage = coverage
        self._payers = payers
        self._claims = claims
        self._receipts = receipts
        self._appointments = appointments
        self._practice = practice
        self._tax_id = tax_id
        self._license_for = license_for
        self._timezone = timezone
        self._people = people

    def read(self, patient: Patient, user_id: str, exported_at: datetime) -> BillingRecord:
        charges = sorted(
            self._payments.list_charges(patient.id), key=lambda c: (c.created_at, c.id)
        )
        plans = self._coverage.list_by_patient(patient.id)
        payers = {plan.payer_id: self._payers.get(plan.payer_id) for plan in plans}
        claims = sorted(
            self._claims.list_by_patient(patient.id), key=lambda c: (c.created_at, c.id)
        )
        appointments = self._appointments.list_by_patient(user_id, patient.id)
        timezone = self._timezone(user_id)
        active = next((plan for plan in plans if plan.active), None)

        statement = build_statement(
            patient_id=patient.id,
            client_name=patient.display_name,
            charges=charges,
            claims=claims,
            appointments=appointments,
            practice=self._practice(),
            timezone=timezone,
            generated_at=exported_at,
            outcome_known=outcome_is_known(payers[active.payer_id] if active else None),
        )
        return BillingRecord(
            charges=[_charge(row) for row in charges],
            coverage=[_coverage(plan, payers[plan.payer_id]) for plan in plans],
            claims=[_claim(c, self._receipts.list_for_claim(c.id)) for c in claims],
            statement=render_statement_pdf(statement),
            superbill=self._superbill(
                patient.id,
                claims,
                charges,
                appointments,
                timezone,
                exported_at,
                self._people(user_id),
            ),
            balance_cents=statement.balance_cents,
        )

    def _superbill(
        self,
        patient_id: str,
        claims: Sequence[Claim],
        charges: Sequence[PatientCharge],
        appointments: Sequence[Appointment],
        timezone: tzinfo,
        exported_at: datetime,
        people: PeopleWords,
    ) -> bytes | None:
        """The superbill over every date a claim covers, as the route would render it.

        The route takes a period; here it is the first to the last service
        date on the client's claims. Left out where the route would refuse,
        for the same reasons: a visit in that span with no claim, a provider
        field the receipt needs, a line with no code. The tax id is read only
        when there is a claim to print it on.
        """
        dates = [line.service_date for claim in claims for line in claim.lines]
        if not dates:
            return None
        try:
            superbill = build_superbill(
                patient_id=patient_id,
                period_start=min(dates),
                period_end=max(dates),
                claims=claims,
                charges=charges,
                appointments=appointments,
                timezone=timezone,
                tax_id=self._tax_id(),
                license_for=self._license_for,
                generated_at=exported_at,
                people=people,
            )
        except SuperbillRefusedError:
            return None
        return render_superbill_pdf(superbill, people)


def _charge(row: PatientCharge) -> ExportCharge:
    return ExportCharge(
        id=row.id,
        kind=row.kind,
        appointment_id=row.appointment_id,
        claim_id=row.claim_id,
        description=row.note,
        amount_cents=row.amount_cents,
        currency=row.currency,
        status=row.status,
        # The table's CHECK constraint holds it to the four.
        method=cast("PaymentMethod | None", row.method),
        payment_reference=row.payment_reference,
        write_off_reason=row.write_off_reason,
        settled_by_charge_id=row.settled_by_charge_id,
        recorded_at=row.created_at,
        updated_at=row.updated_at,
    )


def _coverage(plan: PatientCoverage, payer: Payer | None) -> Coverage:
    subscriber = (
        None
        if plan.subscriber_relationship == "self"
        else CoverageSubscriber(
            first_name=plan.subscriber_first_name,
            last_name=plan.subscriber_last_name,
            date_of_birth=plan.subscriber_date_of_birth,
            sex=plan.subscriber_sex,
        )
    )
    return Coverage(
        id=plan.id,
        payer_name=payer.name if payer is not None else None,
        payer_id=payer.payer_id if payer is not None else None,
        member_id=plan.member_id,
        group_number=plan.group_number,
        plan_name=plan.plan_name,
        subscriber_relationship=plan.subscriber_relationship,
        subscriber=subscriber,
        active=plan.active,
        verified_at=plan.verified_at,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _line(line: ClaimLine, diagnosis_codes: Sequence[str]) -> ExportClaimLine:
    """The line with its pointers resolved to the codes they name."""
    pointed = [
        diagnosis_codes[pointer - 1]
        for pointer in line.dx_pointers
        if 1 <= pointer <= len(diagnosis_codes)
    ]
    return ExportClaimLine(
        id=line.id,
        line_number=line.line_number,
        service_date=line.service_date,
        cpt=line.cpt,
        modifiers=list(line.modifiers),
        units=line.units,
        charge_cents=line.charge_cents,
        diagnosis_codes=pointed,
        telehealth=line.telehealth,
        allowed_cents=line.allowed_cents,
        paid_cents=line.paid_cents,
        patient_responsibility_cents=line.patient_resp_cents,
        adjustments=[
            ClaimLineAdjustment.model_validate(adjustment) for adjustment in line.adjustments or []
        ],
    )


def _claim(claim: Claim, receipts: Sequence[ClaimReceipt]) -> ExportClaim:
    plan = claim.subscriber_snapshot
    return ExportClaim(
        id=claim.id,
        control_number=claim.control_number,
        state=claim.state,
        frequency_code=claim.frequency_code,
        parent_claim_id=claim.parent_claim_id,
        payer_name=plan.payer_name,
        payer_id=plan.payer_id,
        member_id=plan.member_id,
        place_of_service=claim.place_of_service,
        diagnosis_codes=list(claim.diagnosis_codes),
        total_charge_cents=claim.total_charge_cents,
        total_paid_cents=claim.total_paid_cents,
        submitted_at=claim.submitted_at,
        payer_accepted_at=claim.payer_accepted_at,
        adjudicated_at=claim.adjudicated_at,
        payer_claim_number=claim.payer_claim_number,
        lines=[_line(line, claim.diagnosis_codes) for line in claim.lines],
        events=[
            ClaimEvent(
                id=r.id,
                kind=r.kind,
                from_state=r.from_state,
                to_state=r.to_state,
                occurred_at=r.occurred_at,
            )
            for r in receipts
        ],
        created_at=claim.created_at,
        updated_at=claim.updated_at,
    )
