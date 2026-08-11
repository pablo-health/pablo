# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient document domain dataclass (THERAPY-ak6m.2).

A ``PatientDocument`` is a clinician-uploaded file (PDF, PNG, JPEG)
attached to a patient's chart. v1 lifecycle is two-phase:

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

    Four values, chosen to give us regulatory hooks now and physical-
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
    THERAPIST_PRIVATE = "therapist_private"
    PSYCHOTHERAPY_NOTES = "psychotherapy_notes"

    @property
    def is_restricted(self) -> bool:
        """Uploader-only categories. Drives RLS, audit-action choice."""
        return self in (
            DocumentCategory.THERAPIST_PRIVATE,
            DocumentCategory.PSYCHOTHERAPY_NOTES,
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
    id: str
    patient_id: str
    user_id: str
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
