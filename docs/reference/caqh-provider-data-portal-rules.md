# Provider data portal rules the credential record implements

Reference notes for `backend/app/credentialing/`. Every rule below is quoted
from the publisher's own documentation with a page number, so a reader can
check it without taking this file's word for anything.

**Source.** *CAQH Provider Data Portal Provider User Guide*, Version #43, 172
pages. <https://www.caqh.org/hubfs/43908627/drupal/solutions/proview/guide/provider-user-guide.pdf>
Retrieved 2026-09-12. CAQH rebranded to DataSpring in June 2026; the portal
keeps its name and the guide is still published under caqh.org.

**Why quotes and not the PDF.** The guide is 8.7 MB of somebody else's
copyrighted material and this repository is public. Short quotations with a
citation are ordinary practice; redistributing the file is not, and a binary
that size is effectively permanent in git history. If you need the whole
document, fetch it from the URL above.

**Why this file exists at all.** The first version of the gap derivation was
written from a paraphrase of these rules and got three of them wrong — the
threshold, whether training periods count, and the lookback window. Eighteen
unit tests passed, because they were written to agree with the same paraphrase.
An authored summary of a spec is an authored fixture; this is the captured one.

---

## Employment gaps

> In general, a gap is any break in continuous, full-time employment for 3
> months or longer.
>
> — p138

Implemented as `CAQH_GAP_THRESHOLD_DAYS = 90`. Named for whose rule it is,
because it is not universal:

> Some organizations may require a full work history beginning with your
> professional degree and the reporting of all gaps in work history. Check with
> your credentialing organization.
>
> — p138

So every entry point takes `threshold_days`. Pass `0` for an organization that
wants every break. Over-strict is not the safe direction: it asks a clinician to
account for breaks nobody enquired about, on the one workflow we exist to
shorten.

## Education and training periods become explained gaps

> In order to create a seamless timeline of a provider's work history reducing
> provider outreach and documentation redundancies, the following Education and
> Professional Training types will create an associated Gap record in the
> Employment History if the record includes both Start Date and End Date and is
> within the last ten years from the current year.
>
> Internship / Residency / Fellowship / Preceptorship / Other Trainings /
> Undergraduate / Fifth Pathway / Professional School
>
> — p136

> An employment gap record will be created for each individual education and
> professional records in the last 10 years. The Start and End date for gap
> records will match the dates entered in the Professional Training and
> Education record. The Gap Explanation field value will be pre-populated as
> "Academic/Training leave". The card will provide a link to the Education and
> Professional training record that the gap is sourced from.
>
> — p137

Two consequences, and missing either produces a wrong answer:

1. These periods **cover** the timeline, so a fellowship is not an unexplained
   hole. Without this the record asks a clinician to explain her own residency.
2. They also **emit** a gap of their own, pre-explained. The obvious
   simplification — treat the period as coverage and report no gap — is wrong,
   because the portal is the export target and expects that gap record to
   exist.

The enumerated types span both `credential_education` and `credential_training`
in their entirety, so the implementation filters on neither. Matching on
`credential_training.program_type` would couple the rule to free text for no
gain.

## Unexplained gaps block attestation

> If there are any employment gap records, the CAQH Provider Data Portal will
> display a message "Add an explanation for this gap" and a red marker "Please
> Respond". The start and end date of the gap will also be indicated. **You are
> required to fill in all Employment Gaps before attestation.**
>
> — p139

This is why a false gap is not cosmetic. Attestation is the 120-day clock, so a
gap we invent stalls the thing the clinician is trying to do.

> You are required to enter at least one Employment Information record on the
> profile.
>
> — p138

## Required documents are computed per provider, not from a fixed list

> The CAQH Provider Data Portal application will display the required documents
> based on your practice state, your provider type, and any other details that
> you have entered on your profile. Other document types that don't appear as
> required in the Documents section of your profile, or in the drop down list,
> don't need to be uploaded or submitted to CAQH.
>
> — p157

There is no universal required-document list, and the checklists published by
credentialing services are *typical* sets rather than the rule. Anything built
against a hard-coded list will be wrong for some state or provider type.

Note also what the guide does **not** contain: the phrase "curriculum vitae"
appears nowhere in its 172 pages. The work history is *typed* into the
Employment Information section (§4.10). A CV upload is therefore a requirement
of an individual payer or panel, not of the portal — which makes parsing a CV a
**convenience for the clinician**, never a compliance gate. It must not be a
precondition for anything.

State-specific handling is real and varies, e.g.:

> Providers practicing in Oklahoma are now required to upload the CAQH
> Authorization, Attestation, and Release Form (AAR Form) in addition to your
> Oklahoma Application Release.
>
> — p156

> Signed AAR form must be submitted within 120 days from the signature date. If
> the AAR form's signature date is greater than 120 days, it will NOT be
> accepted by CAQH.
>
> — p157

## The profile is data entry first, documents second

> 1. Register with the system. 2. Complete all application questions. 3. Review
> your data profile for accuracy. 4. Authorize participating organizations
> access to your data profile. 5. Attest to your data profile. 6. Upload your
> supporting documentation.
>
> — p4

> Completing the initial CAQH Provider Data Portal profile may take up to two
> hours.
>
> — p4

Eleven profile sections, which is the shape anything exporting to the portal
should follow:

> Personal Information, Professional IDs, Education and Professional Training,
> Specialties, Practice Locations, Hospital Affiliations, Credential Contacts,
> Professional Liability Insurance, Employment Information, Professional
> References, and Disclosure.
>
> — p31

> Questions presented to you may vary based on your primary practice state.
>
> — p31

## Not verified

Recorded so nobody assumes these were checked:

- **Date precision on employment entries.** Credentialing guidance commonly says
  month-and-year, but neither "month and year" nor "MM/YYYY" appears in the
  guide. The one format statement found is `MM/DD/YYYY`, for a practice-location
  end date (p81) — a different field. Treat month-and-year as unconfirmed; it
  does not change the rule that a year-only date must never be defaulted to
  January 1st.
- **The ten-year window's reference date.** The guide says "within the last ten
  years from the current year" (p136) without saying which of the record's dates
  it tests. The implementation tests the END date, the more inclusive reading:
  an extra pre-explained gap costs a glance, a missing one costs a gap record
  the portal wanted.
