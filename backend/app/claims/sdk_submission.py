# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Filing a claim on the vendor's own claim API rather than its X12 shim.

The request we build in :mod:`app.claims.wire` is shaped like an 837P: single
letters for sex, ``YYYYMMDD`` dates, and service lines that point at
diagnoses by position in a claim-level list. The vendor's native claim API
takes the same claim as ordinary data — named enums, ISO dates, and the
diagnosis codes themselves on each line. This translates between the two.

Two reasons it is worth doing, and only one of them is tidiness.

**A claim filed the old way has no timeline.** The claim-lifecycle API only
knows claims submitted through this endpoint; ask it about one filed through
the compatibility shim and it has never heard of it. Everything that reads a
claim's acknowledgements and payments therefore depends on filing this way
first.

**The claim id is stable.** The vendor keeps one id for a claim across every
resubmission of it, which is what its acknowledgements and remittances are
matched against — so that, not the per-attempt submission id, is what we
store as the claim's vendor id.

The translation loses nothing and drops one whole class of mistake: diagnosis
pointers are resolved here, so a line can no longer point past the end of the
diagnosis list.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stedi.models import (
        CreateProfessionalClaimSubmissionInput,
        CreateProfessionalClaimSubmissionOutput,
    )

    from ..models.claims_transport import (
        Address,
        ClaimSubmissionRequest,
        ClaimSubmissionResult,
        ContactInformation,
        Dependent,
        ServiceLine,
        Subscriber,
    )

logger = logging.getLogger(__name__)

#: The 837P's sex letters, as the vendor's words.
_GENDERS = {"M": "MALE", "F": "FEMALE", "U": "UNKNOWN"}

#: The 837P's PAT01 relationship codes, as the vendor's words. ``01`` is a
#: spouse and ``19`` a child; the rest of the code list has no counterpart
#: in the vendor's four-value enum and reads as "other", which is what the
#: claim means and all the payer needs.
_RELATIONSHIPS = {"01": "SPOUSE", "19": "CHILD", "20": "EMPLOYEE"}
_OTHER_RELATIONSHIP = "OTHER_RELATIONSHIP"

#: Claim frequency: ``1`` is an original claim and carries no resubmission
#: block at all, ``7`` replaces a prior claim, ``8`` cancels one.
_FREQUENCIES = {
    "7": "REPLACEMENT_OF_PRIOR_CLAIM",
    "8": "CANCELLATION_OF_PRIOR_CLAIM",
}


#: How the vendor words a refusal to reuse an idempotency key on a changed
#: body. Its own schema says the wording can change and to branch on the
#: exception type instead — which is right, and is exactly the problem here:
#: a reused key and a malformed claim arrive as the SAME exception type, so
#: the type cannot separate them and the text is the only signal there is.
#:
#: Matching it is safe in the direction that matters. A hit turns a generic
#: refusal into the specific one; a miss leaves the generic refusal alone.
#: If the vendor rewords this, a biller sees "the claim was refused" instead
#: of "the key was reused" — the answer we would give anyway without this,
#: and never a wrong one.
_IDEMPOTENCY_REUSE_MARKER = "idempotency-key was previously used"

#: The vendor exception that means "this claim is wrong". Matched by name so
#: this module does not import the SDK at module scope, in step with the
#: rest of the vendor imports here.
_INVALID_REQUEST = "InvalidRequestException"

#: Stands in for a vendor code the native rejection does not carry, when the
#: refusal names no field of its own either.
_INVALID_REQUEST_CODE = "invalid_request"


def rejection_from_validation(
    exc: Exception, *, req: ClaimSubmissionRequest
) -> ClaimSubmissionResult | None:
    """A refused claim as a rejection, when the vendor said which fields.

    ``None`` when the exception is not a field-level refusal, and the caller
    should raise instead.

    The vendor separates two things the old endpoint ran together. A claim
    that is malformed is refused with a list of fields and JSON paths; a
    claim that is well-formed but fails the clearinghouse's payer edits comes
    back as a stored claim carrying rejections. Both mean the same thing to a
    practice — this claim will not be paid until somebody fixes it — so both
    reject the claim here rather than one of them stalling it.

    Treating the first as a transport failure would be worse than merely
    untidy: the claim would sit waiting for a retry that must fail
    identically forever, and the field names the vendor just handed us —
    by far the most useful thing anyone gets out of a rejection — would be
    thrown away in favour of "the clearinghouse refused the request body".

    The vendor refuses a claim two ways under one exception type, and both
    are handled here. A JSON-schema failure carries a ``errors`` list of
    fields and paths. A rule about the claim as a whole — "these two fields
    are required when the insured is the patient" — carries no list at all,
    only a sentence, so the sentence becomes the rejection's description.
    The one refusal that is NOT about the claim is a reused idempotency key,
    which is the caller's bookkeeping rather than a defect in the claim; it
    is left to ``submission_error`` to raise.
    """
    from ..models.claims_transport import (  # noqa: PLC0415 — avoids an import cycle
        ClaimSubmissionErrorDetail,
        ClaimSubmissionResult,
        SubmissionMeta,
        SubmissionPayer,
    )

    message = str(getattr(exc, "message", "") or "")
    if type(exc).__name__ != _INVALID_REQUEST or _IDEMPOTENCY_REUSE_MARKER in message.lower():
        return None

    failures = list(getattr(exc, "errors", None) or ())
    details = [
        ClaimSubmissionErrorDetail(
            # The path into the claim is the most actionable thing here, and
            # there is no vendor code to put in this slot, so it carries the
            # field instead of a number nobody can look up.
            code=str(getattr(failure, "path", "") or _INVALID_REQUEST_CODE),
            description=str(getattr(failure, "message", "") or ""),
            followupAction="",
        )
        for failure in failures
    ] or [
        ClaimSubmissionErrorDetail(
            code=_INVALID_REQUEST_CODE, description=message, followupAction=""
        )
    ]
    return ClaimSubmissionResult(
        status="ERROR",
        controlNumber=req.claimInformation.patientControlNumber,
        tradingPartnerServiceId=req.tradingPartnerServiceId,
        claimReference=None,
        errors=details,
        # Refused before it was stored, so there is no submission to name.
        meta=SubmissionMeta(traceId=""),
        payer=SubmissionPayer(payerName="", payerId=req.tradingPartnerServiceId),
    )


def submission_error(exc: Exception) -> Exception:
    """The typed error for a failed claim submission.

    Separates the two refusals that share a wire shape: a claim the vendor
    could not read, and a claim it read fine but whose key it has already
    seen against a different claim. They send a biller to different places —
    fix the claim, or file it under a new key — so they must not read alike.
    """
    from .clearinghouse import ClearinghouseRequestChangedError  # noqa: PLC0415 — cycle
    from .sdk_runtime import translate_sdk_error  # noqa: PLC0415 — cycle

    message = str(getattr(exc, "message", "") or "")
    if _IDEMPOTENCY_REUSE_MARKER in message.lower():
        return ClearinghouseRequestChangedError(message)
    return translate_sdk_error(exc)


def _iso_date(compact: str | None) -> str | None:
    """``YYYYMMDD`` as ``YYYY-MM-DD``.

    Anything that is not eight digits is passed through untouched: this is a
    format translation, not a validation pass, and the vendor rejects a
    malformed date with a message naming the field far better than a guess
    made here would.
    """
    if compact is None:
        return None
    if len(compact) != len("YYYYMMDD") or not compact.isdigit():
        return compact
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


def _address(models: Any, address: Address | None) -> Any:
    """The party's address, or nothing when the claim does not carry one.

    A claim can reach here missing a field the wire model calls required —
    the scrub's own tests build exactly that, deliberately. Sending it and
    letting the vendor name the missing field beats raising here, where the
    only thing we could say is that an attribute was absent.
    """
    if address is None:
        return None
    return models.ProfessionalClaimSubmissionAddress(
        address_line1=address.address1,
        city=address.city,
        state=address.state,
        postal_code=address.postalCode,
    )


def _gender(models: Any, letter: str | None) -> Any:
    if letter is None:
        return None
    return models.ProfessionalClaimSubmissionGenderCode(_GENDERS.get(letter, "UNKNOWN"))


def _contact(models: Any, contact: ContactInformation) -> Any:
    return models.ProfessionalClaimSubmissionContact(
        name=contact.name, phone_number=contact.phoneNumber
    )


def _person(models: Any, first_name: str, last_name: str) -> Any:
    return models.ProfessionalClaimSubmissionQualifiedNamePerson(
        models.ProfessionalClaimSubmissionPersonName(last_name=last_name, first_name=first_name)
    )


def _organization(models: Any, name: str) -> Any:
    return models.ProfessionalClaimSubmissionQualifiedNameOrganization(name)


def _insured(models: Any, subscriber: Subscriber) -> Any:
    return models.ProfessionalClaimSubmissionInsured(
        # The 837P's claim filing code is always ``CI`` here — commercial
        # insurance — which the vendor's enum spells as the absence of a
        # government program rather than a name of its own.
        insurance_type=models.ProfessionalClaimSubmissionInsuranceType.OTHER,
        name=_person(models, subscriber.firstName, subscriber.lastName),
        payment_responsibility_level_code=(
            models.ProfessionalClaimSubmissionPaymentResponsibilityLevelCode.PRIMARY
        ),
        member_id=subscriber.memberId,
        address=_address(models, getattr(subscriber, "address", None)),
        policy_or_group_number=subscriber.groupNumber,
        date_of_birth=_iso_date(getattr(subscriber, "dateOfBirth", None)),
        gender=_gender(models, getattr(subscriber, "gender", None)),
    )


def _patient(models: Any, dependent: Dependent) -> Any:
    """The client, when the policy is somebody else's.

    Only present for a dependent claim: when the client holds the policy the
    vendor takes the insured alone, exactly as the 837P leaves out the
    dependent loop.
    """
    relationship = _RELATIONSHIPS.get(dependent.relationshipToSubscriberCode, _OTHER_RELATIONSHIP)
    return models.ProfessionalClaimSubmissionPatient(
        name=models.ProfessionalClaimSubmissionQualifiedPersonNamePerson(
            models.ProfessionalClaimSubmissionPersonName(
                last_name=dependent.lastName, first_name=dependent.firstName
            )
        ),
        date_of_birth=_iso_date(dependent.dateOfBirth),
        gender=models.ProfessionalClaimSubmissionGenderCode(_GENDERS[dependent.gender]),
        address=_address(models, dependent.address),
        relationship_to_insured=models.ProfessionalClaimSubmissionPatientRelationshipToInsured(
            relationship
        ),
    )


def _service_line(models: Any, line: ServiceLine, *, diagnoses: list[str]) -> Any:
    """One service line, with its diagnosis pointers resolved to codes.

    A pointer outside the claim's diagnosis list is dropped rather than
    raising. It cannot be honoured — there is no code to send — and the
    vendor's own edits report a line with no diagnosis far more usefully
    than an exception raised three layers below the claim would.
    """
    service = line.professionalService
    codes = [
        diagnoses[index - 1]
        for pointer in service.compositeDiagnosisCodePointers.diagnosisCodePointers
        if (index := int(pointer)) and 1 <= index <= len(diagnoses)
    ]
    # A session happens on a day, so the line carries a start and no end.
    # The vendor takes a range here, and a range whose ends are equal is not
    # the same statement in X12 — it becomes a range segment rather than a
    # single service date, and what the payer's remittance echoes back
    # changes with it.
    date = _iso_date(line.serviceDate)
    return models.ProfessionalClaimSubmissionServiceLine(
        dates_of_service=models.ProfessionalClaimSubmissionDateRange(start=date),
        procedure_code=models.ProfessionalClaimSubmissionProcedureCode(
            code=service.procedureCode, modifiers=list(service.procedureModifiers) or None
        ),
        diagnosis_codes=codes,
        line_item_charge_amount=service.lineItemChargeAmount,
        units=service.serviceUnitCount,
        line_item_control_number=line.providerControlNumber,
        rendering_provider=models.ProfessionalClaimSubmissionRenderingProvider(
            name=_person(models, line.renderingProvider.firstName, line.renderingProvider.lastName),
            identifiers=models.ProfessionalClaimSubmissionRenderingProviderIdentifiers(
                npi=line.renderingProvider.npi,
                taxonomy_code=line.renderingProvider.taxonomyCode,
            ),
        ),
    )


def _resubmission(models: Any, req: ClaimSubmissionRequest) -> Any | None:
    """The block that says which claim this one replaces or cancels.

    ``None`` for an original claim, which is the ordinary case.
    """
    information = req.claimInformation
    code = _FREQUENCIES.get(information.claimFrequencyCode)
    if code is None:
        return None
    supplemental = information.claimSupplementalInformation
    return models.ProfessionalClaimSubmissionResubmission(
        code=models.ProfessionalClaimSubmissionClaimFrequencyCode(code),
        original_reference_number=(
            supplemental.claimControlNumber if supplemental is not None else None
        ),
    )


def to_sdk_submission(
    req: ClaimSubmissionRequest, *, idempotency_key: str
) -> CreateProfessionalClaimSubmissionInput:
    """``req`` as the vendor's native claim submission.

    ``idempotency_key`` is a field on the body rather than a header here, and
    means the same thing: the vendor answers a repeat of the same key with
    the original claim instead of filing a second one.
    """
    from stedi import models  # noqa: PLC0415 — vendor import at call time

    information = req.claimInformation
    diagnoses = [entry.diagnosisCode for entry in information.healthCareCodeInformation]
    billing = req.billing

    return models.CreateProfessionalClaimSubmissionInput(
        purpose=models.ProfessionalClaimSubmissionPurpose.CHARGEABLE,
        # Only the payer's id. The 837P request carries the clearinghouse as
        # its receiver and never the payer's own name, and the vendor
        # resolves the name from the id anyway — sending the receiver's name
        # here would file every claim against a payer called "Stedi".
        payer=models.ProfessionalClaimSubmissionPayer(id=req.tradingPartnerServiceId),
        submitter=models.ProfessionalClaimSubmissionSubmitter(
            name=_organization(models, req.submitter.organizationName),
            contact=_contact(models, req.submitter.contactInformation),
            etin=req.submitter.submitterIdentification,
        ),
        insured=_insured(models, req.subscriber),
        patient=None if req.dependent is None else _patient(models, req.dependent),
        # The four assertions the 837P spells as single letters, and which
        # `wire.py` has always sent as constants: the client consented to
        # release their information, benefits are assigned to the practice,
        # the practice accepts assignment, and the provider's signature is
        # on file.
        authorization=models.ProfessionalClaimSubmissionAuthorization(
            patient_releases_medical_info=(
                models.ProfessionalClaimSubmissionReleaseOfInformationCode.YES
            ),
            insured_authorizes_assignment=(
                models.ProfessionalClaimSubmissionBenefitsAssignmentCertificationIndicator.YES
            ),
            provider_accepts_assignment=(
                models.ProfessionalClaimSubmissionProviderAcceptsAssignment.ASSIGNED
            ),
            provider_signature=models.ProfessionalClaimSubmissionProviderSignature.ON_FILE,
        ),
        encounter=models.ProfessionalClaimSubmissionEncounter(
            primary_diagnosis_code=diagnoses[0] if diagnoses else "",
            additional_diagnosis_codes=diagnoses[1:] or None,
            primary_place_of_service=information.placeOfServiceCode,
            resubmission=_resubmission(models, req),
        ),
        billing=models.ProfessionalClaimSubmissionBilling(
            tax_id=models.ProfessionalClaimSubmissionTaxIdEin(billing.employerId),
            patient_control_number=information.patientControlNumber,
            total_charge=information.claimChargeAmount,
            billing_provider=models.ProfessionalClaimSubmissionBillingProvider(
                name=_organization(models, billing.organizationName),
                address=_address(models, billing.address),
                contact=_contact(models, billing.contactInformation),
                identifiers=models.ProfessionalClaimSubmissionBillingProviderIdentifiers(
                    npi=billing.npi, taxonomy_code=billing.taxonomyCode
                ),
            ),
        ),
        service_lines=[
            _service_line(models, line, diagnoses=diagnoses) for line in information.serviceLines
        ],
        idempotency_key=idempotency_key,
    )


def result_from_sdk(
    output: CreateProfessionalClaimSubmissionOutput, *, req: ClaimSubmissionRequest
) -> ClaimSubmissionResult:
    """What the vendor said, in the shape the submission worker already reads.

    The vendor reports an edit rejection as a successful call carrying
    ``errors``: it stored the claim and wrote a rejection acknowledgement
    against it, but did not send it to the payer. That is the same fact the
    old endpoint reported as an HTTP 400, so it reads as ``ERROR`` here and
    the claim is rejected exactly as before.

    The claim id, not the submission id, becomes the claim's vendor id: the
    vendor keeps the claim id across resubmissions and matches its
    acknowledgements and remittances against it, while a new submission id
    is minted for every attempt. Storing the submission id would leave the
    claim unable to find its own payments after the first correction.
    """
    from ..models.claims_transport import (  # noqa: PLC0415 — avoids an import cycle
        ClaimReference,
        ClaimSubmissionErrorDetail,
        ClaimSubmissionResult,
        SubmissionMeta,
        SubmissionPayer,
    )

    rejections = list(output.errors or ())
    control_number = req.claimInformation.patientControlNumber
    return ClaimSubmissionResult(
        status="ERROR" if rejections else "SUCCESS",
        controlNumber=control_number,
        tradingPartnerServiceId=req.tradingPartnerServiceId,
        claimReference=None
        if rejections
        else ClaimReference(
            correlationId=output.claim_id,
            patientControlNumber=control_number,
            timeOfResponse="",
            payerId=req.tradingPartnerServiceId,
            formatVersion="",
            rhclaimNumber="",
            serviceLines=[],
        ),
        errors=[
            # The vendor's native rejection carries a description and no
            # code of its own. The code is what a practice sees next to the
            # message, so name the source rather than leaving it blank and
            # implying the payer said nothing.
            ClaimSubmissionErrorDetail(
                code="clearinghouse_edit",
                description=rejection.description,
                # The old endpoint's follow-up action was a code telling the
                # biller who to chase. The native rejection has no such
                # field, and inventing one would put words in the vendor's
                # mouth on the screen a biller works the rejection from.
                followupAction="",
            )
            for rejection in rejections
        ],
        meta=SubmissionMeta(traceId=output.submission_id),
        # The native response does not echo the payer's name and nothing
        # reads it; the id is what identifies the payer everywhere it counts.
        payer=SubmissionPayer(payerName="", payerId=req.tradingPartnerServiceId),
    )
