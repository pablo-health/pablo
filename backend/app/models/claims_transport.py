# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Wire-format models for the clearinghouse adapter (see ``app.claims``).

Field names are the vendor's own camelCase, unchanged, so a fixture recorded
from the vendor parses with ``model_validate_json`` unchanged and a value
read off a parsed response is the same name you'd find in the vendor's docs
— no second, snake_case vocabulary to keep in sync with theirs. mypy does
not understand pydantic's ``populate_by_name``/alias relaxation (it always
requires the alias at construction, defeating the point of a Pythonic call
site), so camelCase attributes are the simpler choice here, not merely a
style preference. ``ConfigDict(extra="ignore")`` means a field the vendor
adds later (or one we don't use, like the raw X12 the vendor echoes back)
doesn't break parsing.

Only the fields the claim-assembly and eligibility-check call sites actually
read are modeled; this is not an X12 837P/271 schema.

Money fields (``claimChargeAmount``, ``lineItemChargeAmount``) are the
vendor's own decimal-string wire format, not this codebase's integer-cents
convention (see ``app.money``) — the caller assembling a request converts
cents to that string at the boundary, once, rather than this module inventing
a second money representation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

_WIRE_MODEL_CONFIG = ConfigDict(extra="ignore")


class Address(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    address1: str
    city: str
    state: str
    postalCode: str


class ContactInformation(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    name: str
    phoneNumber: str


class BillingProvider(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    npi: str
    employerId: str
    organizationName: str
    address: Address
    contactInformation: ContactInformation
    taxonomyCode: str
    providerType: Literal["BillingProvider"] = "BillingProvider"


class Submitter(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    organizationName: str
    contactInformation: ContactInformation
    submitterIdentification: str


class Receiver(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    organizationName: str


class Subscriber(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    memberId: str
    paymentResponsibilityLevelCode: Literal["P"] = "P"
    firstName: str
    lastName: str
    gender: Literal["M", "F", "U"]
    dateOfBirth: str
    address: Address
    groupNumber: str | None = None


class DiagnosisCode(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    diagnosisTypeCode: Literal["ABK", "ABF"]
    diagnosisCode: str


class CompositeDiagnosisCodePointers(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    diagnosisCodePointers: list[str]


class ProfessionalService(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    procedureIdentifier: Literal["HC"] = "HC"
    procedureCode: str
    procedureModifiers: list[str] = []
    lineItemChargeAmount: str
    measurementUnit: Literal["UN"] = "UN"
    serviceUnitCount: str
    compositeDiagnosisCodePointers: CompositeDiagnosisCodePointers


class RenderingProvider(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    firstName: str
    lastName: str
    npi: str
    providerType: Literal["RenderingProvider"] = "RenderingProvider"
    taxonomyCode: str


class ServiceLine(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    serviceDate: str
    providerControlNumber: str
    renderingProvider: RenderingProvider
    professionalService: ProfessionalService


class ClaimInformation(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    claimFilingCode: Literal["CI"] = "CI"
    patientControlNumber: str
    claimChargeAmount: str
    placeOfServiceCode: str
    claimFrequencyCode: str
    signatureIndicator: Literal["Y"] = "Y"
    planParticipationCode: Literal["A"] = "A"
    benefitsAssignmentCertificationIndicator: Literal["Y"] = "Y"
    releaseInformationCode: Literal["Y"] = "Y"
    healthCareCodeInformation: list[DiagnosisCode]
    serviceLines: list[ServiceLine]


class ClaimSubmissionRequest(BaseModel):
    """A professional (837P) claim, in the shape ``submit_claim`` accepts."""

    model_config = _WIRE_MODEL_CONFIG

    tradingPartnerServiceId: str
    usageIndicator: Literal["T", "P"]
    billing: BillingProvider
    submitter: Submitter
    receiver: Receiver
    subscriber: Subscriber
    claimInformation: ClaimInformation


class ClaimReferenceServiceLine(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    lineItemControlNumber: str


class ClaimReference(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    correlationId: str
    patientControlNumber: str
    timeOfResponse: str
    payerId: str
    formatVersion: str
    rhclaimNumber: str
    serviceLines: list[ClaimReferenceServiceLine] = []


class ClaimSubmissionErrorDetail(BaseModel):
    """One entry of a 400 edit-rejection's ``errors`` array.

    ``code`` and ``description`` are safe to log (CARC/RARC-style edit
    codes); ``description`` in practice never carries member-identifying
    free text for these validation edits, but treat it as PHI-adjacent
    remittance text rather than assuming that holds for every payer.
    """

    model_config = _WIRE_MODEL_CONFIG

    code: str
    description: str
    followupAction: str


class SubmissionMeta(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    traceId: str


class SubmissionPayer(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    payerName: str
    payerId: str


class ClaimSubmissionResult(BaseModel):
    """The synchronous response to a claim submission: an accept or an edit rejection.

    ``status`` distinguishes the two: ``SUCCESS`` carries ``claimReference``,
    ``ERROR`` carries ``errors``. This is the initial 277CA-equivalent
    acknowledgement, not the payer's eventual adjudication — that arrives
    later, out of band, and is fetched with ``get_transaction``.
    """

    model_config = _WIRE_MODEL_CONFIG

    status: Literal["SUCCESS", "ERROR"]
    controlNumber: str
    tradingPartnerServiceId: str
    claimReference: ClaimReference | None = None
    errors: list[ClaimSubmissionErrorDetail] = []
    meta: SubmissionMeta
    payer: SubmissionPayer


class Payer(BaseModel):
    """One payer-search hit."""

    model_config = _WIRE_MODEL_CONFIG

    stediId: str
    primaryPayerId: str
    displayName: str
    aliases: list[str] = []


class EligibilityProvider(BaseModel):
    """The provider a 270 is asked on behalf of.

    Either an organization (``organizationName`` with the practice's type-2
    billing NPI) or a person (``firstName``/``lastName`` with the rendering
    clinician's own NPI). Which one a practice sends is decided by
    ``app.claims.eligibility``; the vendor accepts both shapes.
    """

    model_config = _WIRE_MODEL_CONFIG

    npi: str
    organizationName: str | None = None
    firstName: str | None = None
    lastName: str | None = None


class EligibilitySubscriber(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    memberId: str
    firstName: str | None = None
    lastName: str | None = None
    #: ``YYYYMMDD``, the vendor's date format on this API.
    dateOfBirth: str | None = None
    gender: Literal["M", "F", "U"] | None = None
    groupNumber: str | None = None


class EligibilityDependent(BaseModel):
    """The client, when the plan's subscriber is somebody else.

    Sent alongside the subscriber's own details; the payer answers about the
    dependent. Only the fields the vendor needs to find the person.
    """

    model_config = _WIRE_MODEL_CONFIG

    firstName: str
    lastName: str
    dateOfBirth: str | None = None
    gender: Literal["M", "F", "U"] | None = None


class EligibilityEncounter(BaseModel):
    """What the inquiry is about: X12 service type codes (``MH`` is mental health).

    Left out, the vendor asks for ``30`` (health benefit plan coverage) and
    the answer is about the plan as a whole rather than the benefit a
    behavioral-health visit is billed under.
    """

    model_config = _WIRE_MODEL_CONFIG

    serviceTypeCodes: list[str]


class EligibilityRequest(BaseModel):
    """A 270 eligibility inquiry, in the shape ``check_eligibility`` accepts."""

    model_config = _WIRE_MODEL_CONFIG

    tradingPartnerServiceId: str
    provider: EligibilityProvider
    subscriber: EligibilitySubscriber
    #: ``None`` rather than an empty list: the vendor rejects ``dependents: []``
    #: outright ("the length of the items must be >= 1"), and the adapter
    #: serialises with ``exclude_none`` so an absent list is simply not sent.
    dependents: list[EligibilityDependent] | None = None
    encounter: EligibilityEncounter | None = None


class EligibilityPlanStatus(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    statusCode: str
    status: str | None = None
    planDetails: str | None = None
    serviceTypeCodes: list[str] = []


class EligibilityPayer(BaseModel):
    """The payer that answered, as the 271's own ``payer`` loop names it.

    ``payorIdentification`` is the electronic payer id (X12 ``PI``); it is
    what a benefit line's related entity is compared against to tell "the
    same payer" from "somebody else administers this".
    """

    model_config = _WIRE_MODEL_CONFIG

    name: str | None = None
    payorIdentification: str | None = None


class EligibilityRelatedEntity(BaseModel):
    """An entity a benefit line points at (X12 2120 loop).

    ``entityIdentifier`` is the role the vendor spells out from the NM1
    qualifier ("Payer", "Third-Party Administrator", "Vendor", "Primary Care
    Provider", ...). ``entityIdentification`` is the code set of
    ``entityIdentificationValue``: ``PI`` is a payer id, ``XX`` an NPI.
    """

    model_config = _WIRE_MODEL_CONFIG

    entityIdentifier: str | None = None
    entityType: str | None = None
    entityName: str | None = None
    entityFirstname: str | None = None
    entityIdentification: str | None = None
    entityIdentificationValue: str | None = None


class EligibilityAdditionalInformation(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    description: str | None = None


class EligibilityBenefit(BaseModel):
    """One ``benefitsInformation`` line of a 271 (an X12 EB segment).

    ``code`` is the EB01 benefit type: ``1``-``5`` active, ``6``-``8``
    inactive, ``A`` co-insurance, ``B`` co-payment, ``C`` deductible, ``F``
    limitations, ``U`` "contact the following entity". ``timeQualifierCode``
    says what period an amount or quantity is for (``29`` remaining, ``23``
    calendar year, ``27`` per visit). Amounts are the vendor's decimal
    strings, not cents; ``app.claims.eligibility`` converts once.
    """

    model_config = _WIRE_MODEL_CONFIG

    code: str
    name: str | None = None
    serviceTypeCodes: list[str] = []
    insuranceTypeCode: str | None = None
    planCoverage: str | None = None
    coverageLevelCode: str | None = None
    timeQualifierCode: str | None = None
    timeQualifier: str | None = None
    benefitAmount: str | None = None
    benefitPercent: str | None = None
    benefitQuantity: str | None = None
    quantityQualifierCode: str | None = None
    quantityQualifier: str | None = None
    inPlanNetworkIndicatorCode: str | None = None
    authOrCertIndicator: str | None = None
    benefitsRelatedEntity: EligibilityRelatedEntity | None = None
    benefitsRelatedEntities: list[EligibilityRelatedEntity] = []
    additionalInformation: list[EligibilityAdditionalInformation] = []


class EligibilityPlanDates(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    planBegin: str | None = None
    planEnd: str | None = None
    eligibilityBegin: str | None = None
    eligibilityEnd: str | None = None


class EligibilityError(BaseModel):
    """One entry of a 271's top-level ``errors`` array: the payer refused the inquiry.

    These arrive with HTTP 200 and no ``planStatus`` at all — the payer
    answered, but with an AAA rejection (72 "Invalid/Missing
    Subscriber/Insured ID" is the common one) rather than a coverage answer.
    ``code`` and ``followupAction`` are safe to log; ``description`` is the
    vendor's own explanation of the code, but treat it as PHI-adjacent
    rather than assuming that holds for every payer. ``possibleResolutions``
    is the vendor's plain-language "what to do about it", rendered to the
    practice as-is.
    """

    model_config = _WIRE_MODEL_CONFIG

    code: str
    description: str
    followupAction: str
    possibleResolutions: str | None = None


class EligibilityResponse(BaseModel):
    """A 271 response.

    Modeled to the depth ``app.claims.eligibility`` reads: the plan-level
    status, every benefit line (that is where copays, deductibles, visit
    limits and "somebody else administers this" all live), and the payer's
    own name and id. An empty ``planStatus`` alone does not mean "no
    coverage": when ``errors`` is non-empty the payer rejected the inquiry
    and never answered the coverage question.
    """

    model_config = _WIRE_MODEL_CONFIG

    controlNumber: str | None = None
    tradingPartnerServiceId: str | None = None
    planStatus: list[EligibilityPlanStatus] = []
    benefitsInformation: list[EligibilityBenefit] = []
    planDateInformation: EligibilityPlanDates | None = None
    errors: list[EligibilityError] = []
    payer: EligibilityPayer | None = None
    meta: SubmissionMeta


class TransactionBusinessIdentifier(BaseModel):
    """One correlation value the vendor lifted out of the X12 envelope.

    Safe to log — these are control numbers (CLM-01, BHT-03, and similar),
    never subscriber or diagnosis data.
    """

    model_config = _WIRE_MODEL_CONFIG

    element: str
    name: str
    value: str


class TransactionArtifact(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    artifactType: str
    usage: str
    url: str


class TransactionDocument(BaseModel):
    """One entry from the transaction-polling endpoint: an inbound/outbound
    X12 exchange (a submitted 837, an inbound 277CA or 835, ...)."""

    model_config = _WIRE_MODEL_CONFIG

    transactionId: str
    direction: Literal["INBOUND", "OUTBOUND"]
    processedAt: str
    businessIdentifiers: list[TransactionBusinessIdentifier] = []
    artifacts: list[TransactionArtifact] = []


class ProviderContact(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    organizationName: str
    email: str
    phone: str
    streetAddress1: str
    city: str
    zipCode: str
    state: str


class ProviderRegistration(BaseModel):
    """A provider record, in the shape ``create_provider`` accepts."""

    model_config = _WIRE_MODEL_CONFIG

    name: str
    npi: str
    taxId: str
    taxIdType: Literal["EIN", "SSN"] = "EIN"
    contacts: list[ProviderContact]


class ProviderRecord(BaseModel):
    """A registered provider, as the vendor echoes it back."""

    model_config = _WIRE_MODEL_CONFIG

    id: str
    name: str
    npi: str
    taxId: str
    taxIdType: Literal["EIN", "SSN"]
    contacts: list[ProviderContact] = []


class ClaimPaymentEnrollment(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    enroll: bool


class EnrollmentTransactions(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    claimPayment: ClaimPaymentEnrollment | None = None


class EnrollmentProviderRef(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    id: str


class EnrollmentPayerRef(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    idOrAlias: str


class EnrollmentRequest(BaseModel):
    """A transaction enrollment, in the shape ``create_enrollment`` accepts."""

    model_config = _WIRE_MODEL_CONFIG

    provider: EnrollmentProviderRef
    payer: EnrollmentPayerRef
    primaryContact: ProviderContact
    transactions: EnrollmentTransactions


class EnrollmentPayer(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    name: str
    stediPayerId: str
    submittedPayerIdOrAlias: str


class EnrollmentProvider(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    id: str
    name: str
    npi: str
    taxId: str
    taxIdType: Literal["EIN", "SSN"]


class Enrollment(BaseModel):
    """A transaction enrollment's current state, as the vendor reports it."""

    model_config = _WIRE_MODEL_CONFIG

    id: str
    status: str
    payer: EnrollmentPayer
    provider: EnrollmentProvider
    submittedAt: str
    statusLastUpdatedAt: str


class EnrollmentFilters(BaseModel):
    """Query filters for ``list_enrollments``. All optional; an empty
    instance lists every enrollment the account has."""

    model_config = _WIRE_MODEL_CONFIG

    providerId: str | None = None
    payerId: str | None = None
    status: str | None = None
