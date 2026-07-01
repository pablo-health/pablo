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

export interface InitUploadResponse {
  document_id: string
  upload_url: string
  required_content_type: string
  max_bytes: number
  required_size_header: string
}

export interface DeleteDocumentResponse {
  message: string
}

export interface DocumentDownloadUrlResponse {
  url: string
}
