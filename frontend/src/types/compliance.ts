// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Compliance reminder types.
 *
 * Mirrors backend/app/routes/compliance.py response shapes.
 */

export interface ComplianceTemplate {
  item_type: string
  label: string
  description: string
  cadence_days: number | null
  reminder_windows: number[]
  multi_instance: boolean
  min_edition: "core" | "solo" | "practice"
  sort_order: number
}

export interface ComplianceItem {
  id: string
  item_type: string
  label: string
  due_date: string | null
  notes: string | null
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface ComplianceItemPayload {
  item_type: string
  label: string
  due_date: string | null
  notes: string | null
}

export interface ComplianceDocument {
  id: string
  compliance_item_id: string | null
  filename: string
  mime_type: string
  size_bytes: number
  document_type: string
  description: string | null
  uploaded_at: string
  uploaded_by_user_id: string
}

/** Whitelist mirrors COMPLIANCE_DOC_ALLOWED_MIME_TYPES in the backend route. */
export const COMPLIANCE_DOC_ALLOWED_MIME_TYPES = [
  "application/pdf",
  "image/png",
  "image/jpeg",
] as const

/** Mirrors settings.compliance_documents_max_bytes (25 MiB) so the picker
 *  rejects oversized files before a doomed round-trip. */
export const COMPLIANCE_DOC_MAX_BYTES = 25 * 1024 * 1024
