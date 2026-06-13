// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { AppointmentResponse } from "@/types/scheduling"
import type { SessionStatus } from "@/types/sessions"

/** A session whose note has finished generating and awaits review. */
export interface AwaitingReviewItem {
  session_id: string
  patient_name: string
  session_date: string
  status: SessionStatus
  note_finalized_at: string | null
}

/** Aggregate payload backing the dashboard panels in a single request. */
export interface DashboardSummary {
  today_appointments: AppointmentResponse[]
  /** patient_id -> last session date (ISO), for today's appointment patients only. */
  last_visit_by_patient: Record<string, string | null>
  week_confirmed_count: number
  notes_pending_count: number
  transcription_pending_count: number
  awaiting_review_total: number
  awaiting_review: AwaitingReviewItem[]
}
