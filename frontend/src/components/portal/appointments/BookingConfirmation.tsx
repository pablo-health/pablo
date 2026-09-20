// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What a patient is left holding once the booking went through.
 *
 * Two states, and the server picks which: a practice that confirms
 * automatically answers with a CONFIRMED appointment, one that reviews
 * requests answers with a PENDING one. So this reads `appointment.status`
 * rather than a prop about how the practice is configured — the screen says
 * what happened, not what was expected to.
 *
 * In-app only. Whether a confirmation email follows is the practice's
 * reminder configuration and not this component's to promise; saying "we've
 * emailed you" on a deployment that sends nothing is exactly the kind of
 * claim the copy guide rules out.
 */

"use client"

import { CheckCircle2, Clock } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { PatientAppointment } from "@/lib/api/patientAppointments"
import {
  BACK_TO_APPOINTMENTS,
  BOOKED_HEADLINE,
  REQUEST_SENT_EXPECTATION,
  REQUEST_SENT_HEADLINE,
  RESCHEDULED_HEADLINE,
} from "./appointmentsCopy"
import { formatWhenLong } from "./formatting"

export interface BookingConfirmationProps {
  appointment: PatientAppointment
  timeZone: string
  mode: "book" | "reschedule"
  onDone: () => void
}

export function BookingConfirmation({
  appointment,
  timeZone,
  mode,
  onDone,
}: BookingConfirmationProps) {
  const pending = appointment.status === "pending"
  const headline = pending
    ? REQUEST_SENT_HEADLINE
    : mode === "reschedule"
      ? RESCHEDULED_HEADLINE
      : BOOKED_HEADLINE

  return (
    <div
      data-testid="appointments-confirmation"
      className="flex flex-col items-center gap-3 py-8 text-center"
    >
      {pending ? (
        <Clock className="size-10 text-amber-600" aria-hidden="true" />
      ) : (
        <CheckCircle2 className="size-10 text-green-600" aria-hidden="true" />
      )}

      <h3 data-testid="appointments-confirmation-headline" className="text-lg font-semibold text-neutral-900">
        {headline}
      </h3>

      <p data-testid="appointments-confirmation-when" className="text-sm text-neutral-700">
        {formatWhenLong(appointment.start_at, timeZone)}
      </p>

      {pending && (
        <p className="max-w-sm text-sm text-neutral-600">{REQUEST_SENT_EXPECTATION}</p>
      )}

      <Button
        type="button"
        variant="outline"
        onClick={onDone}
        data-testid="appointments-confirmation-done"
      >
        {BACK_TO_APPOINTMENTS}
      </Button>
    </div>
  )
}
