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
