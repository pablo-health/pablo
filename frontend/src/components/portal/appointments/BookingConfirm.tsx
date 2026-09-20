// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The review-and-submit step: one time, one kind of appointment, one button.
 *
 * Owns its own submit rather than reporting a choice upward, because the
 * outcomes it has to handle are answers to this request and nowhere else:
 *
 *   409 `LATE_CHANGE_NOT_ACKNOWLEDGED` — rescheduling inside the practice's
 *     notice period. The engine refuses ONCE with a sentence written to be
 *     shown, and the identical request carrying the acknowledgement succeeds.
 *     So the refusal is drawn here, in the server's own words, with the
 *     button that sends it again. Never pre-acknowledged: the flag is what
 *     proves the warning was given, and sending it up front would prove
 *     nothing.
 *   409 otherwise — the time went while the patient was reading. Back to the
 *     picker with a fresh fetch; the engine populates no alternatives, so
 *     there is nothing to offer but the current openings.
 *   403 — booking stopped between browsing and confirming. Handed up, so the
 *     module says what a patient can do instead, in one place.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  bookPatientAppointment,
  reschedulePatientAppointment,
  type PatientAppointment,
  type PatientSlot,
} from "@/lib/api/patientAppointments"
import { ACTION_FAILED, CONFIRM_LATE_CHANGE, KEEP_APPOINTMENT } from "./appointmentsCopy"
import { formatWhenLong } from "./formatting"

export interface BookingConfirmProps {
  token: string
  timeZone: string
  slot: PatientSlot
  sessionType: string
  mode: "book" | "reschedule"
  /** Required when rescheduling — the appointment being moved. */
  appointmentId?: string
  onSuccess: (appointment: PatientAppointment) => void
  onSlotTaken: () => void
  onBookingClosed: () => void
  onBack: () => void
}

export function BookingConfirm({
  token,
  timeZone,
  slot,
  sessionType,
  mode,
  appointmentId,
  onSuccess,
  onSlotTaken,
  onBookingClosed,
  onBack,
}: BookingConfirmProps) {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The engine's own sentence about the notice period, held until answered.
  const [lateChange, setLateChange] = useState<string | null>(null)

  async function submit(acknowledgeLateChange: boolean) {
    if (submitting) return
    setSubmitting(true)
    setError(null)

    const result =
      mode === "reschedule" && appointmentId !== undefined
        ? await reschedulePatientAppointment({
            token,
            appointmentId,
            startAt: slot.start_at,
            acknowledgeLateChange,
          })
        : await bookPatientAppointment({
            token,
            startAt: slot.start_at,
            sessionType,
            durationMinutes: slot.duration_minutes,
          })

    setSubmitting(false)

    if (result.ok) {
      onSuccess(result.data)
      return
    }
    if (result.status === 403) {
      onBookingClosed()
      return
    }
    if (result.code === "LATE_CHANGE_NOT_ACKNOWLEDGED") {
      setLateChange(result.message)
      return
    }
    if (result.status === 409) {
      onSlotTaken()
      return
    }
    setError(ACTION_FAILED)
  }

  if (lateChange !== null) {
    return (
      <div data-testid="appointments-late-change" className="space-y-4">
        <p className="text-sm text-neutral-800">{lateChange}</p>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            data-testid="appointments-late-change-cancel"
            onClick={() => {
              setLateChange(null)
              onBack()
            }}
          >
            {KEEP_APPOINTMENT}
          </Button>
          <Button
            type="button"
            disabled={submitting}
            data-testid="appointments-late-change-confirm"
            onClick={() => void submit(true)}
          >
            {CONFIRM_LATE_CHANGE}
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div data-testid="appointments-confirm" className="space-y-4">
      <div>
        <p className="text-sm text-neutral-600">
          {mode === "reschedule" ? "New time" : "You're booking"}
        </p>
        <p
          data-testid="appointments-confirm-when"
          className="text-base font-medium text-neutral-900"
        >
          {formatWhenLong(slot.start_at, timeZone)}
        </p>
        <p className="text-sm text-neutral-600">{sessionType}</p>
      </div>

      {error !== null && (
        <p data-testid="appointments-confirm-error" className="text-sm text-red-600">
          {error}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={onBack}
          disabled={submitting}
          data-testid="appointments-confirm-back"
        >
          Back
        </Button>
        <Button
          type="button"
          onClick={() => void submit(false)}
          disabled={submitting}
          data-testid="appointments-confirm-submit"
        >
          {submitting
            ? "Sending…"
            : mode === "reschedule"
              ? "Confirm new time"
              : "Confirm booking"}
        </Button>
      </div>
    </div>
  )
}
