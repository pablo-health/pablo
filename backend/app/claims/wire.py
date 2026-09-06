# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stored claim in the shape the clearinghouse takes.

:func:`to_submission_request` maps a :class:`~app.models.claims.Claim` onto
the wire model in :mod:`app.models.claims_transport` — the professional
(837P) claim body ``submit_claim`` accepts. Pure: a claim and a few
per-account values in, a request out. Nothing is sent from here; that is
the submission worker's job, and it is the only place the practice's tax
id is decrypted, which is why the tax id is an argument rather than a
field the claim carries.

Money crosses from integer cents to the vendor's decimal string here, once.
Dates become ``YYYYMMDD``; diagnosis codes lose their dot (``F41.1`` is
``F411`` on the wire); the first diagnosis is the principal (``ABK``) and
the rest are ``ABF``.

The subscriber loop is filled from the claim's subscriber snapshot. When the
client is a spouse or child on somebody else's plan, the policy holder goes
in ``subscriber`` and the client in ``dependent``, with the relationship
code the vendor expects; when the client is the subscriber there is no
dependent loop at all.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from ..models.claims_transport import (
    Address,
    BillingProvider,
    ClaimInformation,
    ClaimSubmissionRequest,
    CompositeDiagnosisCodePointers,
    ContactInformation,
    Dependent,
    DependentRelationshipCode,
    DiagnosisCode,
    ProfessionalService,
    Receiver,
    RenderingProvider,
    ServiceLine,
    Submitter,
    Subscriber,
)
from ..money import cents_to_dollars
from .validation import missing_fields

if TYPE_CHECKING:
    from datetime import date

    from ..models.claims import (
        BillingProviderSnapshot,
        Claim,
        PersonSnapshot,
    )
    from ..models.coverage import SubscriberRelationship


class ClaimMappingError(Exception):
    """The claim lacks something this transport requires; nothing was built."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__("Cannot build the submission: missing " + ", ".join(missing))
        self.missing = missing


_BILLING_REQUIRED = ["legal_name", "npi", "address_line1", "city", "state", "postal_code", "phone"]
_RENDERING_REQUIRED = ["first_name", "last_name", "npi", "taxonomy_code"]
#: What the subscriber loop needs, and the dependent loop when there is one.
_PERSON_REQUIRED = [
    "first_name",
    "last_name",
    "sex",
    "date_of_birth",
    "address_line1",
    "city",
    "state",
    "postal_code",
]

#: The coverage's relationship, as the vendor codes it on the dependent loop.
#: ``self`` has no entry: a client who is the subscriber is not a dependent.
_RELATIONSHIP_CODES: dict[SubscriberRelationship, DependentRelationshipCode] = {
    "spouse": "01",
    "child": "19",
    "other": "G8",
}


def to_submission_request(  # noqa: PLR0913 — the per-account values, keyword-only
    claim: Claim,
    *,
    trading_partner_service_id: str,
    usage_indicator: Literal["T", "P"],
    tax_id: str,
    submitter_identification: str,
    receiver_name: str,
) -> ClaimSubmissionRequest:
    """The 837P request body for ``claim``.

    ``trading_partner_service_id`` is the clearinghouse's id for the payer;
    ``usage_indicator`` is ``T`` for the vendor's test mode, ``P`` for
    production; ``tax_id`` is the practice's EIN or SSN, decrypted by the
    caller; ``submitter_identification`` and ``receiver_name`` come from
    the practice's clearinghouse account.

    Raises :class:`ClaimMappingError` naming any value the transport needs
    that the claim does not carry.
    """
    snapshot = claim.subscriber_snapshot
    billing = claim.billing_snapshot.billing_provider
    rendering = claim.billing_snapshot.rendering_provider
    subscriber = snapshot.subscriber
    patient = snapshot.patient if snapshot.relationship != "self" else None
    missing = (
        [f"billing_provider.{f}" for f in missing_fields(billing, _BILLING_REQUIRED)]
        + [f"rendering_provider.{f}" for f in missing_fields(rendering, _RENDERING_REQUIRED)]
        + [f"subscriber.{f}" for f in missing_fields(subscriber, _PERSON_REQUIRED)]
    )
    if patient is not None:
        missing.extend(f"patient.{f}" for f in missing_fields(patient, _PERSON_REQUIRED))
    if not claim.place_of_service:
        missing.append("place_of_service")
    if not claim.lines:
        missing.append("lines")
    if missing:
        raise ClaimMappingError(missing)

    rendering_provider = RenderingProvider(
        firstName=_present(rendering.first_name),
        lastName=_present(rendering.last_name),
        npi=_present(rendering.npi),
        taxonomyCode=_present(rendering.taxonomy_code),
    )
    contact = ContactInformation(
        name=_present(billing.legal_name), phoneNumber=_digits(_present(billing.phone))
    )
    return ClaimSubmissionRequest(
        tradingPartnerServiceId=trading_partner_service_id,
        usageIndicator=usage_indicator,
        billing=BillingProvider(
            npi=_present(billing.npi),
            employerId=_digits(tax_id),
            organizationName=_present(billing.legal_name),
            address=_address(billing),
            contactInformation=contact,
            taxonomyCode=_present(rendering.taxonomy_code),
        ),
        submitter=Submitter(
            organizationName=_present(billing.legal_name),
            contactInformation=contact,
            submitterIdentification=submitter_identification,
        ),
        receiver=Receiver(organizationName=receiver_name),
        subscriber=Subscriber(
            memberId=snapshot.member_id,
            firstName=_present(subscriber.first_name),
            lastName=_present(subscriber.last_name),
            gender=subscriber.sex or "U",
            dateOfBirth=_wire_date(subscriber.date_of_birth),
            address=_address(subscriber),
            groupNumber=snapshot.group_number,
        ),
        dependent=None
        if patient is None
        else Dependent(
            relationshipToSubscriberCode=_RELATIONSHIP_CODES[snapshot.relationship],
            firstName=_present(patient.first_name),
            lastName=_present(patient.last_name),
            gender=patient.sex or "U",
            dateOfBirth=_wire_date(patient.date_of_birth),
            address=_address(patient),
        ),
        claimInformation=ClaimInformation(
            patientControlNumber=claim.control_number,
            claimChargeAmount=_amount(claim.total_charge_cents),
            placeOfServiceCode=_present(claim.place_of_service),
            claimFrequencyCode=claim.frequency_code,
            healthCareCodeInformation=[
                DiagnosisCode(
                    diagnosisTypeCode="ABK" if position == 0 else "ABF",
                    diagnosisCode=code.replace(".", "").upper(),
                )
                for position, code in enumerate(claim.diagnosis_codes)
            ],
            serviceLines=[
                ServiceLine(
                    serviceDate=_wire_date(line.service_date),
                    providerControlNumber=line.line_control_number,
                    renderingProvider=rendering_provider,
                    professionalService=ProfessionalService(
                        procedureCode=line.cpt,
                        procedureModifiers=list(line.modifiers),
                        lineItemChargeAmount=_amount(line.charge_cents),
                        serviceUnitCount=str(line.units),
                        compositeDiagnosisCodePointers=CompositeDiagnosisCodePointers(
                            diagnosisCodePointers=[str(p) for p in line.dx_pointers]
                        ),
                    ),
                )
                for line in sorted(claim.lines, key=lambda line: line.line_number)
            ],
        ),
    )


def _present(value: str | None) -> str:
    """A value the ``missing_fields`` pass above has already guaranteed.

    Exists so the optional snapshot fields narrow to ``str`` for the wire
    model without a second round of checks; the empty-string branch is
    unreachable once ``missing`` was empty.
    """
    return value or ""


def _address(party: PersonSnapshot | BillingProviderSnapshot) -> Address:
    return Address(
        address1=_present(party.address_line1),
        city=_present(party.city),
        state=_present(party.state),
        postalCode=_digits(_present(party.postal_code)),
    )


def _amount(cents: int) -> str:
    return f"{cents_to_dollars(cents) or 0:.2f}"


def _wire_date(value: date | None) -> str:
    return value.strftime("%Y%m%d") if value is not None else ""


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)
