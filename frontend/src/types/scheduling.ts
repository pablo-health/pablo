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
  start_at: string
  end_at: string
  duration_minutes: number
  status: AppointmentStatus
  session_type: string
  video_link: string | null
  video_platform: string | null
  notes: string | null
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
}
