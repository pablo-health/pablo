// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Scheduling Types
 *
 * Type definitions for appointment scheduling, matching backend API models.
 */

export type AppointmentStatus = "confirmed" | "cancelled" | "no_show" | "completed"

export type RecurrenceFrequency = "weekly" | "biweekly" | "monthly"

export type SessionType = "individual" | "couples" | "group"

export interface AppointmentResponse {
  id: string
  user_id: string
  patient_id: string
  title: string
  /** Patient display name, resolved server-side. Optional so cached
   * payloads from older servers still typecheck. */
  patient_name?: string | null
  start_at: string
  end_at: string
  duration_minutes: number
  status: AppointmentStatus
  session_type: string
  video_link: string | null
  video_platform: string | null
  notes: string | null
  note_type: string
  recurrence_rule: string | null
  recurring_appointment_id: string | null
  recurrence_index: number | null
  is_exception: boolean
  google_event_id: string | null
  google_sync_status: string | null
  session_id: string | null
  created_at: string
  updated_at: string | null
}

export interface AppointmentListResponse {
  data: AppointmentResponse[]
  total: number
}

export interface CreateAppointmentRequest {
  patient_id: string
  title: string
  start_at: string
  end_at: string
  duration_minutes: number
  session_type?: string
  video_link?: string | null
  video_platform?: string | null
  notes?: string | null
  note_type?: string
}

export interface CreateRecurringAppointmentRequest {
  patient_id: string
  title: string
  start_at: string
  end_at: string
  duration_minutes: number
  session_type?: string
  video_link?: string | null
  video_platform?: string | null
  notes?: string | null
  note_type?: string
  frequency: RecurrenceFrequency
  timezone: string
  end_date?: string | null
  count?: number | null
}

export interface UpdateAppointmentRequest {
  title?: string
  patient_id?: string
  start_at?: string
  end_at?: string
  duration_minutes?: number
  session_type?: string
  video_link?: string | null
  video_platform?: string | null
  notes?: string | null
  note_type?: string
  status?: AppointmentStatus
}

/**
 * Request to edit all future occurrences in a recurring series. The
 * backend's edit-series endpoint only accepts this subset of fields —
 * it does not carry start/end time, duration, or patient changes.
 */
export interface EditSeriesRequest {
  title?: string
  session_type?: string
  video_link?: string | null
  video_platform?: string | null
  notes?: string | null
  note_type?: string
}

// --- Appointment type models -------------------------------------------------

export type AppointmentAudience = "new" | "existing" | "both"
export type HorizonUnit = "business" | "days"

export interface AppointmentTypeResponse {
  id: string
  user_id: string
  name: string
  default_fee_cents: number | null
  duration_minutes: number
  audience: AppointmentAudience
  /** `null` means "use the practice default", distinct from `0` (no notice). */
  min_notice_hours: number | null
  earliest_offer_business_days: number
  horizon: number
  horizon_unit: HorizonUnit
  self_bookable: boolean
  offerable: boolean
  created_at: string | null
  updated_at: string | null
}

export interface AppointmentTypeListResponse {
  data: AppointmentTypeResponse[]
  total: number
  /** True once the practice's pre-existing types were joined by seeded ones. */
  migrated: boolean
}

export interface CreateAppointmentTypeRequest {
  name: string
  default_fee_cents?: number | null
  duration_minutes?: number
  audience?: AppointmentAudience
  min_notice_hours?: number | null
  earliest_offer_business_days?: number
  horizon?: number
  horizon_unit?: HorizonUnit
  self_bookable?: boolean
  offerable?: boolean
}

export interface UpdateAppointmentTypeRequest {
  name?: string
  default_fee_cents?: number | null
  duration_minutes?: number
  audience?: AppointmentAudience
  min_notice_hours?: number | null
  earliest_offer_business_days?: number
  horizon?: number
  horizon_unit?: HorizonUnit
  self_bookable?: boolean
  offerable?: boolean
}

// --- Scheduling policy models -------------------------------------------------

export interface SchedulingPolicyResponse {
  min_notice_hours: number
  max_horizon_days: number
  cancel_cutoff_hours: number
  reschedule_cutoff_hours: number
  pending_hold_hours: number
  self_book_existing: boolean
  self_book_new: boolean
  self_book_mode: "request" | "auto"
  new_patient_flow: "consult" | "intake"
  intake_forms_due_hours: number
}

export type UpdateSchedulingPolicyRequest = Partial<SchedulingPolicyResponse>
