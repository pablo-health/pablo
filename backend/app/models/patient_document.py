# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient document domain dataclass (THERAPY-ak6m.2).

A ``PatientDocument`` is a file (PDF, PNG, JPEG) attached to a patient's
chart. Two principals put one there — a clinician, or the patient the
chart is about — and exactly one of ``user_id`` and
``uploaded_by_patient_id`` says which. The database enforces that with a
CHECK rather than trusting either writer to keep the pair honest.

v1 lifecycle is two-phase:

1. ``init`` mints a V4 signed PUT URL and inserts a row with
   ``finalized_at=NULL`` — placeholder, not yet visible in list reads.
2. ``finalize`` verifies the GCS blob, runs PyMuPDF text extraction
   (or marks ``extracted_text=NULL`` for scanned PDFs that ak6m.2.3
   will OCR later), and stamps ``finalized_at``.

Soft delete sets ``deleted_at``; GCS-object cleanup is deferred to
ak6m.2.1.

Documents carry a :class:`DocumentCategory` that drives access,
release-of-records eligibility, and audit-action granularity. The
category is set at init and is immutable — see the enum docstring
for the regulatory rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class DocumentCategory(StrEnum):
    """Access + disclosure classification for an uploaded document.

    Six values, chosen to give us regulatory hooks now and physical-
    separation room later (the enum can become a partition predicate
    if compliance review pushes for separate tables or buckets).

    * ``CHART`` — part of the patient record. Visible to anyone with
      a ``patient_clinicians`` grant on the patient. Releasable to
      the patient via the standard HIPAA right-of-access workflow.
      Default for new uploads. Examples: labs, intake, insurance, ID,
      ROIs, prior-provider records, patient-supplied homework.

    * ``CONSENT`` — a signed consent or authorization form attached
      to the patient's chart. Same access class as ``CHART``: visible
      to anyone with a ``patient_clinicians`` grant on the patient and
      releasable via the standard right-of-access workflow. Not
      restricted, not uploader-only.

    * ``INTAKE_ARTIFACT`` — something a patient was asked to send in
      before they are seen: a photo of an insurance card, a referral
      letter, a prior record they had to hand. Same access class as
      ``CHART``.

    * ``MESSAGE`` — a file attached to secure correspondence between
      the patient and the practice. Same access class as ``CHART``.

    ``INTAKE_ARTIFACT`` and ``MESSAGE`` are the two a patient may
    upload into and read back themselves; see :attr:`is_patient_facing`
    for what rests on that.

    * ``THERAPIST_PRIVATE`` — provider's working material, uploader-
      only. Outside the standard patient record but without the
      psychotherapy-notes carve-out. Examples: consult letters
      marked confidential, supervision feedback PDFs, raw notes the
      provider exported from another system.

    * ``PSYCHOTHERAPY_NOTES`` — explicit HIPAA §164.501 carve-out.
      Uploader-only. Subject to:

      * §164.508(a)(2) — disclosure requires a *separate*, specific
        authorization; a generic release-of-records signature does
        NOT cover them.
      * §164.524(a)(1)(i) — patient right-of-access does NOT extend
        to this category.
      * Stricter audit retention (use a distinct action so the
        access log is queryable independently).

    Access semantics for ``THERAPIST_PRIVATE`` and
    ``PSYCHOTHERAPY_NOTES`` are identical (uploader-only); the
    downstream workflows (release-of-records filter, patient portal
    visibility) diverge. Keeping them as separate enum values now
    means the column already encodes the distinction the workflows
    will need.

    Immutability: category is set at init and never changes. This
    matches the regulatory provenance idea ("recorded during a
    session") and avoids the audit ambiguity of "was this *ever* in
    the chart?".
    """

    CHART = "chart"
    CONSENT = "consent"
    INTAKE_ARTIFACT = "intake_artifact"
    MESSAGE = "message"
    THERAPIST_PRIVATE = "therapist_private"
    PSYCHOTHERAPY_NOTES = "psychotherapy_notes"

    @property
    def is_restricted(self) -> bool:
        """Uploader-only categories. Drives RLS, audit-action choice."""
        return self in (
            DocumentCategory.THERAPIST_PRIVATE,
            DocumentCategory.PSYCHOTHERAPY_NOTES,
        )

    @property
    def is_patient_facing(self) -> bool:
        """Categories the patient themselves may upload into and read back.

        The complement is not "private" — ``chart`` and ``consent`` hold
        plenty a patient is entitled to receive. It is that the patient
        portal is not the surface those come out of: a right-of-access
        request is a workflow with an identity check and a response
        deadline, and ``psychotherapy_notes`` is carved out of it
        altogether (§164.524(a)(1)(i)). So this is the set the patient's
        own routes read and write, and everything else on their chart
        reaches them the way the regulation says it does.

        Three layers test it, and each is the backstop for a different
        mistake: the request model refuses an unacceptable category
        outright, the repository filters reads to this set, and the row
        policy carries it too so a route that forgot cannot disclose a
        note by asking for it.
        """
        return self in (
            DocumentCategory.INTAKE_ARTIFACT,
            DocumentCategory.MESSAGE,
        )


#: The same set as a plain value, for the SQL predicates and the response
#: models that cannot call a property on a member they do not yet have.
PATIENT_FACING_CATEGORIES: frozenset[DocumentCategory] = frozenset(
    c for c in DocumentCategory if c.is_patient_facing
)


class ExtractionStatus(StrEnum):
    """Lifecycle of the off-request text-extraction job.

    ``finalize`` does the cheap blob validation inline and enqueues the
    GCS download + PyMuPDF + Document AI work to a Cloud Tasks worker
    rather than running it on the HTTP request thread. ``PENDING`` is
    stamped the moment the job is enqueued; the worker
    moves it to ``COMPLETE`` (extraction ran, text may still be
    ``None`` for a scanned PDF with OCR unavailable — that is not a
    failure) or ``FAILED`` (a deterministic extraction error, or a
    transient error on the queue's last delivery attempt).

    ``None`` on the row (pre-migration rows, finalized under the old
    synchronous path) is treated as ``COMPLETE`` — those documents
    already carry their final extraction result.
    """

    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class PatientDocument:
    """One uploaded file, and exactly one uploader.

    ``user_id`` names the clinician who uploaded it; ``uploaded_by_patient_id``
    names the patient. Precisely one is set — the database says so with a
    CHECK, and :attr:`uploaded_by` is how a reader asks which without
    re-deriving the rule.
    """

    id: str
    patient_id: str
    user_id: str | None
    filename: str
    mime_type: str
    gcs_path: str
    size_bytes: int
    created_at: datetime
    extracted_text: str | None = None
    finalized_at: datetime | None = None
    deleted_at: datetime | None = None
    category: DocumentCategory = field(default=DocumentCategory.CHART)
    # Which extractor produced extracted_text. See PatientDocumentRow
    # for the value set.
    extracted_via: str | None = None
    extraction_metadata: dict[str, object] | None = None
    # Legacy rows (extracted synchronously, pre-THERAPY-ul6d) read as
    # COMPLETE — see ExtractionStatus docstring.
    extraction_status: ExtractionStatus = field(default=ExtractionStatus.COMPLETE)
    #: Set when the patient uploaded it themselves; NULL when a clinician
    #: did. Always the chart's own patient — the row policy pins it to the
    #: calling principal, and no route takes it from a request body.
    uploaded_by_patient_id: str | None = None

    @property
    def uploaded_by(self) -> str:
        """``'patient'`` or ``'clinician'`` — who put this on the chart.

        Read off ``uploaded_by_patient_id`` rather than off ``user_id``,
        because that is the column the patient path sets. A row with
        neither cannot exist: the CHECK refuses it.
        """
        return "patient" if self.uploaded_by_patient_id is not None else "clinician"
