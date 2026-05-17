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

export interface PatientDocumentResponse {
  id: string
  patient_id: string
  filename: string
  mime_type: string
  size_bytes: number
  created_at: string
  finalized_at: string | null
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
