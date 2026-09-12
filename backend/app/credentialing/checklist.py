# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The checklist: what gets asked, in whose order, and when.

One definition, consumed by the API, the wizard and anything exporting the
record. The point of keeping it here rather than in the UI is that the ORDER
and the GROUPING are not ours to choose.

**The provider data portal's shape is the shape.** Its profile has eleven
sections, and a record collected in a different order needs translating before
it can be filed. So the sections are named and ordered exactly as the portal
names them — see ``docs/reference/caqh-provider-data-portal-rules.md``, which
quotes the provider user guide rather than paraphrasing it.

**Three tiers, and the middle one is the product.** Tier 1 is claims-ready
data: every field in it is one the billing pipeline needs regardless of whether
the clinician ever applies to a panel. So a clinician who fills in Tier 1 and
stops has not abandoned anything — she has finished. Nothing here treats that
as incomplete, and :func:`completion` reports the two tiers separately so no
caller can accidentally render a progress bar that shames her for stopping.

**The supervision fork branches early** because an associate-licensed clinician
is a different applicant, not the same applicant with extra fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..db.models import CREDENTIAL_CONFIRMATION_SOURCES, CREDENTIAL_SUPERVISION_STATUSES


class CaqhSection(StrEnum):
    """The eleven profile sections, in the portal's own order.

    Verbatim from the provider user guide p31:

        "Personal Information, Professional IDs, Education and Professional
         Training, Specialties, Practice Locations, Hospital Affiliations,
         Credential Contacts, Professional Liability Insurance, Employment
         Information, Professional References, and Disclosure."

    Declaration order IS export order; nothing re-sorts these.
    """

    PERSONAL_INFORMATION = "personal_information"
    PROFESSIONAL_IDS = "professional_ids"
    EDUCATION_AND_TRAINING = "education_and_professional_training"
    SPECIALTIES = "specialties"
    PRACTICE_LOCATIONS = "practice_locations"
    HOSPITAL_AFFILIATIONS = "hospital_affiliations"
    CREDENTIAL_CONTACTS = "credential_contacts"
    PROFESSIONAL_LIABILITY_INSURANCE = "professional_liability_insurance"
    EMPLOYMENT_INFORMATION = "employment_information"
    PROFESSIONAL_REFERENCES = "professional_references"
    DISCLOSURE = "disclosure"


class Tier(StrEnum):
    """Which sitting a field belongs to.

    ``CONFIRM`` is not a question. Those values are already known from a public
    source or from what the practice already stores, and the clinician's job is
    to say whether they are right — so a Tier-0 field renders pre-filled with a
    confirm control, never as an empty box awaiting typing.
    """

    CONFIRM = "tier_0_confirm"
    CLAIMS_READY = "tier_1_claims_ready"
    CREDENTIALING = "tier_2_credentialing"


class FieldKind(StrEnum):
    """What the surface has to render, and what the API will accept."""

    TEXT = "text"
    DATE = "date"
    BOOLEAN = "boolean"
    CHOICE = "choice"
    MONEY = "money"
    #: A repeating group — licences, locations, references, employment.
    COLLECTION = "collection"
    #: A document into the existing compliance vault, referenced by id.
    UPLOAD = "upload"


class Applicability(StrEnum):
    """Who a field applies to, which is what the supervision fork switches on.

    The fork is here rather than in the UI because the API has to agree with it:
    a field that does not apply must not be required, and must not be silently
    accepted either.
    """

    ALL = "all"
    #: Independently licensed — the ordinary case.
    INDEPENDENT_ONLY = "independent_only"
    #: Associate, provisional or otherwise practising under supervision. A
    #: different applicant: many payers will not panel her at all, and the ones
    #: that do credential the supervisor alongside her.
    SUPERVISED_ONLY = "supervised_only"
    #: Prescribers, who carry a DEA and answer three more questions.
    PRESCRIBER_ONLY = "prescriber_only"


@dataclass(frozen=True)
class ChecklistField:
    """One thing the checklist asks for, or asks her to confirm.

    ``target`` is ``table.column`` for a scalar or the bare table name for a
    collection, so the mapping doc and the route layer agree on where a value
    lands without either restating it. ``source`` is filled in only for
    :attr:`Tier.CONFIRM` fields and says where the pre-filled value came from,
    which is what lets the surface tell her *why* it already knows.

    Every Tier-0 field leaves a ``credential_confirmations`` row regardless of
    its target — that table is the provenance, not the value. Most Tier-0
    fields still target the column that owns the value, and confirming does not
    move it. The few that target ``credential_confirmations.presented_value``
    are the ones with no home column anywhere else: the portal asks them of
    everyone, so the answer has to live somewhere, and the confirmation row is
    where.
    """

    key: str
    label: str
    section: CaqhSection
    tier: Tier
    kind: FieldKind
    target: str
    required: bool = True
    applies_to: Applicability = Applicability.ALL
    source: str | None = None
    help_text: str | None = None
    #: Written through this module rather than set on a row. Every encrypted
    #: field is, because ``government_ids`` is what audits the access.
    audited_writer: str | None = None
    choices: tuple[str, ...] = field(default_factory=tuple)


#: Where a Tier-0 value comes from. Named so the surface can say "from the NPPES
#: registry" rather than presenting a value with no provenance, which reads as
#: the software having guessed. These are the schema's own vocabulary rather
#: than a parallel list: the same string is written to
#: ``credential_confirmations.source``, and a second copy of it would eventually
#: disagree with the CHECK constraint.
_NPPES, _PECOS, _EXCLUSIONS, _PROFILE, _PRACTICE = CREDENTIAL_CONFIRMATION_SOURCES


TIER_0_CONFIRM: tuple[ChecklistField, ...] = (
    ChecklistField(
        key="legal_name",
        label="Legal name",
        section=CaqhSection.PERSONAL_INFORMATION,
        tier=Tier.CONFIRM,
        kind=FieldKind.TEXT,
        target="credential_confirmations.presented_value",
        source=_NPPES,
        help_text="As it appears in the NPPES registry.",
    ),
    ChecklistField(
        key="npi_number",
        label="Individual NPI",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CONFIRM,
        kind=FieldKind.TEXT,
        target="clinician_profiles.npi_number",
        source=_NPPES,
    ),
    ChecklistField(
        key="taxonomy_code",
        label="Primary taxonomy",
        section=CaqhSection.SPECIALTIES,
        tier=Tier.CONFIRM,
        kind=FieldKind.TEXT,
        target="clinician_profiles.taxonomy_code",
        source=_NPPES,
        help_text="The specialty classification a payer expects on a claim.",
    ),
    ChecklistField(
        key="primary_license",
        label="Primary licence",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CONFIRM,
        kind=FieldKind.TEXT,
        target="clinician_profiles.license_number",
        source=_PROFILE,
    ),
    ChecklistField(
        key="primary_license_state",
        label="Primary licence state",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CONFIRM,
        kind=FieldKind.CHOICE,
        target="clinician_profiles.license_state",
        source=_PROFILE,
    ),
    ChecklistField(
        key="dea_number",
        label="DEA registration",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CONFIRM,
        kind=FieldKind.TEXT,
        # There is exactly one DEA column in this schema and it is this one.
        target="clinician_profiles.dea_number",
        source=_PROFILE,
        applies_to=Applicability.PRESCRIBER_ONLY,
    ),
    ChecklistField(
        key="credential_titles",
        label="Credentials",
        section=CaqhSection.PERSONAL_INFORMATION,
        tier=Tier.CONFIRM,
        kind=FieldKind.COLLECTION,
        target="clinician_profiles.credential_titles",
        source=_PROFILE,
    ),
    ChecklistField(
        key="practice_name",
        label="Practice legal name",
        section=CaqhSection.PRACTICE_LOCATIONS,
        tier=Tier.CONFIRM,
        kind=FieldKind.TEXT,
        target="practice_billing_profile.legal_name",
        source=_PRACTICE,
    ),
    ChecklistField(
        key="practice_address",
        label="Practice address",
        section=CaqhSection.PRACTICE_LOCATIONS,
        tier=Tier.CONFIRM,
        kind=FieldKind.TEXT,
        target="practice_billing_profile.address_line1",
        source=_PRACTICE,
    ),
    ChecklistField(
        key="billing_npi",
        label="Billing NPI",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CONFIRM,
        kind=FieldKind.TEXT,
        target="practice_billing_profile.billing_npi",
        source=_PRACTICE,
        required=False,
    ),
    ChecklistField(
        key="medicare_enrollment",
        label="Medicare enrolment on file",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CONFIRM,
        kind=FieldKind.BOOLEAN,
        target="credential_confirmations.presented_value",
        source=_PECOS,
        required=False,
        help_text="From the public PECOS file.",
    ),
    ChecklistField(
        key="exclusion_clearance",
        label="No federal exclusions found",
        section=CaqhSection.DISCLOSURE,
        tier=Tier.CONFIRM,
        kind=FieldKind.BOOLEAN,
        target="credential_confirmations.presented_value",
        source=_EXCLUSIONS,
        help_text="Checked against the LEIE and SAM exclusion lists.",
    ),
    ChecklistField(
        key="hospital_affiliations_none",
        label="No hospital affiliations",
        section=CaqhSection.HOSPITAL_AFFILIATIONS,
        tier=Tier.CONFIRM,
        kind=FieldKind.BOOLEAN,
        target="credential_confirmations.presented_value",
        source=_PROFILE,
        help_text=(
            "Pre-answered for an outpatient practice. The portal asks it of "
            "everyone, so confirming it here saves the question later."
        ),
    ),
    ChecklistField(
        key="credentialing_contact",
        label="Who the payer should contact",
        section=CaqhSection.CREDENTIAL_CONTACTS,
        tier=Tier.CONFIRM,
        kind=FieldKind.TEXT,
        target="practice_billing_profile.contact_email",
        source=_PRACTICE,
        help_text="The practice inbox, never an individual clinician's.",
    ),
)


TIER_1_CLAIMS_READY: tuple[ChecklistField, ...] = (
    ChecklistField(
        key="supervision_status",
        label="Are you independently licensed, or practising under supervision?",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.CHOICE,
        target="credential_government_ids.supervision_status",
        choices=CREDENTIAL_SUPERVISION_STATUSES,
        help_text=(
            "Asked first because it changes what follows. An associate-licensed "
            "clinician is a different applicant, not the same one with extra "
            "fields."
        ),
    ),
    ChecklistField(
        key="business_structure",
        label="Business structure",
        section=CaqhSection.PERSONAL_INFORMATION,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.CHOICE,
        target="credential_government_ids.business_structure",
        choices=(
            "sole_proprietor",
            "single_member_llc",
            "llc",
            "s_corp",
            "c_corp",
            "partnership",
            "professional_corporation",
        ),
    ),
    ChecklistField(
        key="date_of_birth",
        label="Date of birth",
        section=CaqhSection.PERSONAL_INFORMATION,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.DATE,
        target="credential_government_ids.dob_encrypted",
        audited_writer="app.credentialing.government_ids",
    ),
    ChecklistField(
        key="licenses",
        label="Every licence you hold",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.COLLECTION,
        target="credential_licenses",
        help_text=(
            "All states, with expiry dates. Multi-state is ordinary, and the "
            "expiry dates become your renewal reminders."
        ),
    ),
    ChecklistField(
        key="supervisor",
        label="Your supervisor",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.TEXT,
        target="supervision_relationships.supervisor_name",
        applies_to=Applicability.SUPERVISED_ONLY,
        help_text="Payers that panel supervised clinicians credential the supervisor too.",
    ),
    ChecklistField(
        key="liability_policy",
        label="Malpractice cover",
        section=CaqhSection.PROFESSIONAL_LIABILITY_INSURANCE,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.COLLECTION,
        target="credential_liability_policies",
        help_text="Carrier, policy number, and the per-occurrence and aggregate limits.",
    ),
    ChecklistField(
        key="liability_certificate",
        label="Certificate of insurance",
        section=CaqhSection.PROFESSIONAL_LIABILITY_INSURANCE,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.UPLOAD,
        target="credential_liability_policies.document_id",
        help_text=(
            "The certificate itself. A payer wants the document, not a transcription of it."
        ),
    ),
    ChecklistField(
        key="service_locations",
        label="Where you see clients",
        section=CaqhSection.PRACTICE_LOCATIONS,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.COLLECTION,
        target="credential_service_locations",
        help_text=(
            "These end up in payer directories, so accepting-new-clients and "
            "languages matter more than they look."
        ),
    ),
    ChecklistField(
        key="payer_participation",
        label="Which payers are you already in network with?",
        section=CaqhSection.PRACTICE_LOCATIONS,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.COLLECTION,
        target="payer_participations",
        audited_writer="app.credentialing.participation",
        help_text=(
            "Decides whether a session bills as a claim or a superbill. Marking "
            "one out-of-network is also how we know to offer it later."
        ),
    ),
    ChecklistField(
        key="bank_account",
        label="Where payments should land",
        section=CaqhSection.PRACTICE_LOCATIONS,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.COLLECTION,
        target="credential_bank_accounts",
        required=False,
        audited_writer="app.credentialing.government_ids",
        help_text=(
            "Offered now, needed later. EFT enrolment comes after a contract "
            "is signed, so a clinician who is here to get on panels does not "
            "have to have an account yet — and one forming an entity may not. "
            "A clinician already billing should fill it in now."
        ),
    ),
    ChecklistField(
        key="voided_cheque",
        label="Proof of your bank account",
        section=CaqhSection.PRACTICE_LOCATIONS,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.UPLOAD,
        target="credential_bank_accounts.document_id",
        required=False,
        help_text=(
            "A voided cheque, a bank letter or a statement header — payers "
            "differ, and some ask for nothing. Optional here on purpose: no "
            "payer has asked yet, and a clinician who banks online may have no "
            "chequebook at all. Whichever payer wants one will say so at "
            "enrolment, and we will have this ready."
        ),
    ),
    ChecklistField(
        key="caqh_id",
        label="Do you have a CAQH ID?",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CLAIMS_READY,
        kind=FieldKind.TEXT,
        target="credential_government_ids.caqh_id",
        required=False,
        help_text=(
            "Pulled forward from the credentialing questions on purpose: it is "
            "one field, and knowing it now shortens the later ask considerably."
        ),
    ),
)


TIER_2_CREDENTIALING: tuple[ChecklistField, ...] = (
    ChecklistField(
        key="ssn",
        label="Social Security number",
        section=CaqhSection.PERSONAL_INFORMATION,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.TEXT,
        target="credential_government_ids.ssn_encrypted",
        audited_writer="app.credentialing.government_ids",
        help_text=(
            "Required by every credentialing application. Encrypted, and every "
            "read of it is logged."
        ),
    ),
    ChecklistField(
        key="tax_id",
        label="Tax ID",
        section=CaqhSection.PERSONAL_INFORMATION,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.TEXT,
        target="credential_government_ids.tax_id_encrypted",
        audited_writer="app.credentialing.government_ids",
    ),
    ChecklistField(
        key="type2_npi",
        label="Organisation NPI",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.TEXT,
        target="credential_government_ids.type2_npi",
        required=False,
        help_text="If you bill as an entity rather than as yourself.",
    ),
    ChecklistField(
        key="license_certificate",
        label="Licence certificates",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.UPLOAD,
        target="credential_licenses.document_id",
        help_text=(
            "One per licence. Tier 1 collected the numbers and dates, which is "
            "all a claim needs; an application wants the certificate itself."
        ),
    ),
    ChecklistField(
        key="education",
        label="Degrees",
        section=CaqhSection.EDUCATION_AND_TRAINING,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.COLLECTION,
        target="credential_education",
    ),
    ChecklistField(
        key="degree_certificate",
        label="Highest degree",
        section=CaqhSection.EDUCATION_AND_TRAINING,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.UPLOAD,
        target="compliance_documents.id",
        help_text="The diploma or a transcript. Primary-source verification starts here.",
    ),
    ChecklistField(
        key="training",
        label="Internships, practica, residencies and fellowships",
        section=CaqhSection.EDUCATION_AND_TRAINING,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.COLLECTION,
        target="credential_training",
        help_text=(
            "These also account for the years you were not in paid employment, "
            "so entering them saves explaining the same period twice."
        ),
    ),
    ChecklistField(
        key="employment",
        label="Work history",
        section=CaqhSection.EMPLOYMENT_INFORMATION,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.COLLECTION,
        target="credential_employment",
        help_text=(
            "Month and year for each position. A break of three months or more "
            "needs a line explaining it, which is the portal's own rule."
        ),
    ),
    ChecklistField(
        key="cv",
        label="Curriculum vitae",
        section=CaqhSection.EMPLOYMENT_INFORMATION,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.UPLOAD,
        target="compliance_documents.id",
        required=False,
        help_text=(
            "Optional here. Some payers ask for the document; the history above "
            "is what the portal actually files."
        ),
    ),
    ChecklistField(
        key="references",
        label="Three professional references",
        section=CaqhSection.PROFESSIONAL_REFERENCES,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.COLLECTION,
        target="credential_references",
        help_text=(
            "Payers contact these. A reference who has known you under a year "
            "is usually rejected, so pick accordingly."
        ),
    ),
    ChecklistField(
        key="disclosure_license_action",
        label="Has any licence of yours ever been limited, suspended or revoked?",
        section=CaqhSection.DISCLOSURE,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.BOOLEAN,
        target="credential_disclosures",
        audited_writer="app.credentialing.disclosures",
    ),
    ChecklistField(
        key="disclosure_malpractice_claim",
        label="Has a malpractice claim ever been filed against you?",
        section=CaqhSection.DISCLOSURE,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.BOOLEAN,
        target="credential_disclosures",
        audited_writer="app.credentialing.disclosures",
    ),
    ChecklistField(
        key="disclosure_criminal_history",
        label="Have you ever been convicted of a criminal offence?",
        section=CaqhSection.DISCLOSURE,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.BOOLEAN,
        target="credential_disclosures",
        audited_writer="app.credentialing.disclosures",
    ),
    ChecklistField(
        key="disclosure_coverage_lapse",
        label="Has your malpractice cover ever lapsed?",
        section=CaqhSection.DISCLOSURE,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.BOOLEAN,
        target="credential_disclosures",
        audited_writer="app.credentialing.disclosures",
    ),
    ChecklistField(
        key="disclosure_medicare_sanction",
        label="Have you ever been sanctioned by Medicare or Medicaid?",
        section=CaqhSection.DISCLOSURE,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.BOOLEAN,
        target="credential_disclosures",
        audited_writer="app.credentialing.disclosures",
    ),
    ChecklistField(
        key="medicare_intent",
        label="Do you want to enrol with Medicare?",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.BOOLEAN,
        target="credential_government_ids.medicare_intent",
        required=False,
    ),
    ChecklistField(
        key="medicaid_intent",
        label="Do you want to enrol with your state Medicaid programme?",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.BOOLEAN,
        target="credential_government_ids.medicaid_intent",
        required=False,
    ),
    ChecklistField(
        key="dea_certificate",
        label="DEA registration certificate",
        section=CaqhSection.PROFESSIONAL_IDS,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.UPLOAD,
        target="compliance_documents.id",
        applies_to=Applicability.PRESCRIBER_ONLY,
        required=False,
    ),
    ChecklistField(
        key="w9",
        label="Signed W-9",
        section=CaqhSection.PERSONAL_INFORMATION,
        tier=Tier.CREDENTIALING,
        kind=FieldKind.UPLOAD,
        target="compliance_documents.id",
        required=False,
        help_text=(
            "Whether this is required depends on your state and provider type; "
            "the portal tells you which documents it wants."
        ),
    ),
)


#: Every field, in CAQH section order within tier order. The whole question set.
CHECKLIST_FIELDS: tuple[ChecklistField, ...] = (
    *TIER_0_CONFIRM,
    *TIER_1_CLAIMS_READY,
    *TIER_2_CREDENTIALING,
)


def fields_for_tier(tier: Tier) -> tuple[ChecklistField, ...]:
    """The question set for one sitting, in declaration order."""
    return tuple(f for f in CHECKLIST_FIELDS if f.tier is tier)


def fields_for_section(section: CaqhSection) -> tuple[ChecklistField, ...]:
    """Everything that exports into one portal section, across all tiers.

    What an export builder reads: the portal wants a section at a time, and it
    does not care which sitting each answer came from.
    """
    return tuple(f for f in CHECKLIST_FIELDS if f.section is section)


def applicable(
    fields: tuple[ChecklistField, ...],
    *,
    supervised: bool = False,
    prescriber: bool = False,
) -> tuple[ChecklistField, ...]:
    """Narrow a question set to this clinician.

    The supervision fork lives here rather than in the UI so the API agrees with
    the surface: a field that does not apply is neither required nor quietly
    accepted. Both flags default false, so the ordinary independently-licensed
    non-prescriber gets the ordinary set without a caller having to say so.
    """
    allowed = {Applicability.ALL}
    allowed.add(Applicability.SUPERVISED_ONLY if supervised else Applicability.INDEPENDENT_ONLY)
    if prescriber:
        allowed.add(Applicability.PRESCRIBER_ONLY)
    return tuple(f for f in fields if f.applies_to in allowed)


@dataclass(frozen=True)
class TierCompletion:
    """How far one tier has got. Deliberately NOT a single overall percentage.

    Tier 1 and Tier 2 are reported apart because finishing Tier 1 and stopping is
    a complete outcome, and one combined number would render as roughly half
    done — which is exactly the nag the design rules out. A caller wanting a
    progress bar gets one per tier, and Tier 2's is absent until she opts in.
    """

    tier: Tier
    answered: int
    required: int
    complete: bool


def completion(
    answered_keys: set[str],
    *,
    supervised: bool = False,
    prescriber: bool = False,
) -> tuple[TierCompletion, ...]:
    """Per-tier progress, given the keys that have an answer on file."""
    out: list[TierCompletion] = []
    for tier in Tier:
        fields = applicable(fields_for_tier(tier), supervised=supervised, prescriber=prescriber)
        required = [f for f in fields if f.required]
        answered = [f for f in required if f.key in answered_keys]
        out.append(
            TierCompletion(
                tier=tier,
                answered=len(answered),
                required=len(required),
                complete=len(answered) == len(required),
            )
        )
    return tuple(out)


def claims_ready(
    answered_keys: set[str], *, supervised: bool = False, prescriber: bool = False
) -> bool:
    """Has she given us everything the billing pipeline needs?

    The question that matters for a clinician who never credentials, and the one
    that makes stopping after Tier 1 a finish rather than an abandonment.
    """
    by_tier = {
        c.tier: c for c in completion(answered_keys, supervised=supervised, prescriber=prescriber)
    }
    return by_tier[Tier.CLAIMS_READY].complete
