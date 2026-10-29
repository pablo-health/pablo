// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { AppointmentResponse } from "@/types/scheduling"
import type { SessionStatus } from "@/types/sessions"

export interface AppointmentBadge {
  label: string
  cls: string
}

const APPOINTMENT_BADGES: Record<string, AppointmentBadge> = {
  confirmed: { label: "Scheduled", cls: "bg-secondary-50 text-secondary-700" },
  completed: { label: "Done", cls: "bg-neutral-100 text-neutral-600" },
  cancelled: { label: "Cancelled", cls: "bg-neutral-100 text-neutral-500" },
  no_show: { label: "No-show", cls: "bg-red-50 text-red-700" },
}

const IN_SESSION = { label: "In session", cls: "bg-primary-50 text-primary-700" }
const DRAFTING = { label: "Drafting note", cls: "bg-primary-50 text-primary-700" }

// Starting a session links it to the appointment without changing the
// appointment's own status, which stays "confirmed" for scheduling and
// claims. Once a session is linked, its status is what the visit is at.
const SESSION_BADGES: Partial<Record<SessionStatus, AppointmentBadge>> = {
  scheduled: IN_SESSION,
  in_progress: IN_SESSION,
  recording_complete: DRAFTING,
  queued: DRAFTING,
  transcribing: DRAFTING,
  processing: DRAFTING,
  pending_review: { label: "To review", cls: "bg-amber-50 text-amber-700" },
  finalized: { label: "Signed", cls: "bg-neutral-100 text-neutral-600" },
  failed: { label: "Draft failed", cls: "bg-red-50 text-red-700" },
}

export function appointmentBadge(
  appointment: Pick<AppointmentResponse, "status" | "session_id" | "session_status">,
): AppointmentBadge | undefined {
  const { status, session_id, session_status } = appointment
  const visitHappened = status !== "cancelled" && status !== "no_show"
  if (visitHappened && session_id && session_status) {
    const fromSession = SESSION_BADGES[session_status]
    if (fromSession) return fromSession
  }
  return APPOINTMENT_BADGES[status]
}
