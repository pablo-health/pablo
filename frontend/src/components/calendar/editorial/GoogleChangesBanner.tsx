// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { format } from "date-fns"
import {
  GOOGLE_SYNC_STATUS,
  type AppointmentResponse,
  type GoogleChangeResolution,
} from "@/types/scheduling"
import { NoticeButton, type ResolveGoogleChange } from "./GoogleChangeNotice"

interface GoogleChangesBannerProps {
  /** Every appointment in view, cancelled ones included. */
  appointments: AppointmentResponse[]
  patientMap: Map<string, string>
  /** Sessions a bulk removal in Google left waiting, across the whole calendar. */
  heldCount: number
  onResolve: ResolveGoogleChange
  onResolveHeld: (resolution: GoogleChangeResolution) => void
  pending?: boolean
  now?: Date
}

/**
 * The Google Calendar changes waiting on the therapist, above the grid.
 *
 * A quietly cancelled session is hidden from the grid with the rest of the
 * cancelled ones, so its Undo has to live somewhere that is always on screen.
 * The bulk prompt is one line for the whole calendar, not one per session.
 */
export function GoogleChangesBanner({
  appointments,
  patientMap,
  heldCount,
  onResolve,
  onResolveHeld,
  pending = false,
  now = new Date(),
}: GoogleChangesBannerProps) {
  const removed = appointments.filter(
    (a) =>
      a.google_sync_status === GOOGLE_SYNC_STATUS.removedInGoogle &&
      new Date(a.start_at) > now,
  )
  if (heldCount === 0 && removed.length === 0) return null

  return (
    <div
      role="status"
      className="flex flex-col gap-2 rounded-lg px-4 py-2.5 text-sm"
      style={{
        backgroundColor: "var(--ed-status-noshow-bg)",
        color: "var(--ed-status-noshow-fg)",
      }}
    >
      {heldCount > 0 && (
        <div data-testid="google-held-removals" className="flex flex-wrap items-center gap-2">
          <p className="mr-auto font-medium">
            {heldCount === 1
              ? "1 session was removed from Google Calendar with many others, so it's still booked here."
              : `${heldCount} sessions were removed from Google Calendar at once, so they're still booked here.`}
          </p>
          <NoticeButton disabled={pending} onClick={() => onResolveHeld("keep_pablo")}>
            Put back in Google Calendar
          </NoticeButton>
          <NoticeButton disabled={pending} onClick={() => onResolveHeld("accept_google")}>
            Cancel here too
          </NoticeButton>
        </div>
      )}
      {removed.map((appointment) => (
        <div
          key={appointment.id}
          data-testid="google-removed-line"
          className="flex flex-wrap items-center gap-2"
        >
          <p className="mr-auto">
            <span className="font-medium">
              {patientMap.get(appointment.patient_id) ??
                appointment.patient_name ??
                appointment.title}
              , {format(new Date(appointment.start_at), "EEE MMM d, h:mm a")}
            </span>
            {": removed from Google Calendar, so cancelled here."}
          </p>
          <NoticeButton disabled={pending} onClick={() => onResolve(appointment, "keep_pablo")}>
            Undo
          </NoticeButton>
          <NoticeButton
            disabled={pending}
            onClick={() => onResolve(appointment, "accept_google")}
          >
            OK
          </NoticeButton>
        </div>
      ))}
    </div>
  )
}
