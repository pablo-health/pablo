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
    """One payer-search hit.

    ``transactionSupport`` maps each transaction the vendor knows
    (``professionalClaimSubmission``, ``eligibilityCheck``, ``claimPayment``,
    ...) to ``SUPPORTED``, ``ENROLLMENT_REQUIRED`` or ``NOT_SUPPORTED`` — what
    decides whether an enrollment request has to be filed before the
    practice can use that transaction with this payer.
    """

    model_config = _WIRE_MODEL_CONFIG

    stediId: str
    primaryPayerId: str
    displayName: str
    aliases: list[str] = []
    transactionSupport: dict[str, str] = {}


class EligibilityProvider(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    organizationName: str
    npi: str


class EligibilitySubscriber(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    memberId: str
    firstName: str | None = None
    lastName: str | None = None
    dateOfBirth: str | None = None


class EligibilityRequest(BaseModel):
    """A 270 eligibility inquiry, in the shape ``check_eligibility`` accepts."""

    model_config = _WIRE_MODEL_CONFIG

    tradingPartnerServiceId: str
    provider: EligibilityProvider
    subscriber: EligibilitySubscriber


class EligibilityPlanStatus(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    statusCode: str
    status: str | None = None


class EligibilityPayer(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    name: str | None = None
    payerId: str | None = None


class EligibilityError(BaseModel):
    """One entry of a 271's top-level ``errors`` array: the payer refused the inquiry.

    These arrive with HTTP 200 and no ``planStatus`` at all — the payer
    answered, but with an AAA rejection (72 "Invalid/Missing
    Subscriber/Insured ID" is the common one) rather than a coverage answer.
    ``code`` and ``followupAction`` are safe to log; ``description`` is the
    vendor's own explanation of the code, but treat it as PHI-adjacent
    rather than assuming that holds for every payer. The vendor's longer
    ``location`` / ``possibleResolutions`` prose is not modeled.
    """

    model_config = _WIRE_MODEL_CONFIG

    code: str
    description: str
    followupAction: str


class EligibilityResponse(BaseModel):
    """A 271 response.

    Only the fields a caller needs to record the check are modeled;
    benefit-line detail is intentionally out of scope. An empty
    ``planStatus`` alone does not mean "no coverage": when ``errors`` is
    non-empty the payer rejected the inquiry and never answered the
    coverage question.
    """

    model_config = _WIRE_MODEL_CONFIG

    planStatus: list[EligibilityPlanStatus] = []
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


class TransactionEnrollment(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    enroll: bool


class EnrollmentTransactions(BaseModel):
    """Which transactions one enrollment request covers.

    The vendor's object carries one key per transaction it knows; only the
    three a practice files for are modeled (835 remittance, 837P claims, 270
    eligibility). One request per transaction is the convention here, so at
    most one of these is set on any request.
    """

    model_config = _WIRE_MODEL_CONFIG

    claimPayment: TransactionEnrollment | None = None
    professionalClaimSubmission: TransactionEnrollment | None = None
    eligibilityCheck: TransactionEnrollment | None = None


class EnrollmentProviderRef(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    id: str


class EnrollmentPayerRef(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    idOrAlias: str


class EnrollmentRequest(BaseModel):
    """A transaction enrollment, in the shape ``create_enrollment`` accepts.

    ``userEmail`` is where the vendor sends updates about the request;
    ``status`` set to ``STEDI_ACTION_REQUIRED`` submits it in the same call
    rather than parking it as a draft.
    """

    model_config = _WIRE_MODEL_CONFIG

    provider: EnrollmentProviderRef
    payer: EnrollmentPayerRef
    primaryContact: ProviderContact
    transactions: EnrollmentTransactions
    userEmail: str | None = None
    status: Literal["DRAFT", "STEDI_ACTION_REQUIRED"] | None = None


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


class EnrollmentTaskLink(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    label: str
    url: str


class EnrollmentManualTask(BaseModel):
    """What the vendor asks the practice to do by hand: instructions and links."""

    model_config = _WIRE_MODEL_CONFIG

    instructions: str | None = None
    links: list[EnrollmentTaskLink] = []


class EnrollmentTaskDefinition(BaseModel):
    model_config = _WIRE_MODEL_CONFIG

    manualTask: EnrollmentManualTask | None = None


class EnrollmentTask(BaseModel):
    """One step the vendor attached to an enrollment.

    ``responsibleParty`` is ``PROVIDER`` when the practice has to act (sign a
    form, attest, upload a document) and ``STEDI`` when the vendor does.
    """

    model_config = _WIRE_MODEL_CONFIG

    id: str
    responsibleParty: str
    isComplete: bool = False
    definition: EnrollmentTaskDefinition | None = None


class Enrollment(BaseModel):
    """A transaction enrollment's current state, as the vendor reports it.

    ``reason`` is the vendor's note on why a request is still provisioning
    or was rejected; ``tasks`` is what it wants done. Both are payer-facing
    prose about the practice, never about a patient.
    """

    model_config = _WIRE_MODEL_CONFIG

    id: str
    status: str
    payer: EnrollmentPayer
    provider: EnrollmentProvider
    submittedAt: str | None = None
    statusLastUpdatedAt: str
    transactions: EnrollmentTransactions = EnrollmentTransactions()
    reason: str | None = None
    tasks: list[EnrollmentTask] = []


class EnrollmentFilters(BaseModel):
    """Query filters for ``list_enrollments``. All optional; an empty
    instance lists every enrollment the account has."""

    model_config = _WIRE_MODEL_CONFIG

    providerId: str | None = None
    payerId: str | None = None
    status: str | None = None
