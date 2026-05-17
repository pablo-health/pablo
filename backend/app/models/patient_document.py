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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
