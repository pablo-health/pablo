// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient document API types (THERAPY-ak6m.2).
 *
 * Mirrors backend `app.routes.patient_documents.PatientDocumentResponse`
 * and the init / finalize request envelopes.
 */

export const ALLOWED_DOCUMENT_MIME_TYPES = [
  "application/pdf",
  "image/png",
  "image/jpeg",
] as const

export type AllowedDocumentMimeType = (typeof ALLOWED_DOCUMENT_MIME_TYPES)[number]

/**
 * Access + disclosure classification. Mirrors the backend
 * `DocumentCategory` enum:
 *
 * - `chart`: part of the patient record, visible to co-treating
 *   clinicians via patient_clinicians grants. Default.
 * - `consent`: a signed consent or authorization form attached to
 *   the patient's chart. Same access class as `chart` — not
 *   uploader-private.
 * - `therapist_private`: uploader-only. Provider working material.
 * - `psychotherapy_notes`: uploader-only. HIPAA §164.501 carve-out —
 *   subject to separate authorization for release and exempt from
 *   patient right-of-access. Immutable after upload.
 */
export const DOCUMENT_CATEGORIES = [
  "chart",
  "consent",
  "therapist_private",
  "psychotherapy_notes",
] as const

export type DocumentCategory = (typeof DOCUMENT_CATEGORIES)[number]

/**
 * Lifecycle of the off-request text-extraction job. `pending` right after
 * finalize; the backend worker moves it to `complete` (extraction ran —
 * `extracted_text` may still be null for a scanned PDF with no OCR
 * available, which is not a failure) or `failed`.
 */
export type ExtractionStatus = "pending" | "complete" | "failed"

export interface PatientDocumentResponse {
  id: string
  patient_id: string
  filename: string
  mime_type: string
  size_bytes: number
  created_at: string
  finalized_at: string | null
  category: DocumentCategory
  extracted_text: string | null
  extraction_status: ExtractionStatus
  text_extraction_failed: boolean
}

export interface PatientDocumentListResponse {
  data: PatientDocumentResponse[]
  total: number
}

export interface InitUploadRequest {
  filename: string
  mime_type: string
  size_bytes: number
  /**
   * Defaults to `chart` server-side. Pass `therapist_private` or
   * `psychotherapy_notes` to restrict to the uploader. Category is
   * immutable after upload — pick deliberately.
   */
  category?: DocumentCategory
}

/**
 * Self-describing recipe for a browser-direct upload, produced by the
 * backend's configured storage provider. The client executes it as-is:
 *
 * - method "PUT" (GCS): raw-body PUT with `headers` attached (they carry
 *   the signed content-type and size-range constraints); `fields` empty.
 * - method "POST" (S3): multipart/form-data POST with `fields` (the
 *   signed policy) as the leading form entries and the file last;
 *   `headers` empty — the browser sets the multipart boundary.
 */
export interface UploadTarget {
  url: string
  method: "PUT" | "POST"
  headers: Record<string, string>
  fields: Record<string, string>
}

export interface InitUploadResponse {
  document_id: string
  upload: UploadTarget
  /** For pre-flight UX only; the storage layer enforces the cap. */
  max_bytes: number
}

export interface DeleteDocumentResponse {
  message: string
}

export interface DocumentDownloadUrlResponse {
  url: string
}
