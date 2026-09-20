// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient's appointments: what is coming, what has been, and what they
 * may do about either.
 *
 * **Actions are offered on what the practice allows and nothing else.** When
 * the practice does not take changes online, the row carries no Reschedule
 * and no Cancel — not a greyed-out pair. A disabled control with no sentence
 * beside it tells a patient they are being refused without telling them by
 * whom or what to do instead, which is the copy failure this module was
 * written to avoid.
 *
 * **The notice period is not enforced here.** The engine refuses a late
 * change once, in a sentence written to be shown, and accepts the same
 * request carrying an acknowledgement. So the button is live right up to the
 * appointment and the warning arrives from the server when it applies. The
 * alternative — disabling inside the cutoff — tells a patient who genuinely
 * cannot attend that their only remaining option is to not turn up, which
 * costs the practice the slot AND the warning.
 */

"use client"

import { useState } from "react"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  cancelPatientAppointment,
  type PatientAppointment,
} from "@/lib/api/patientAppointments"
import {
  ACTION_FAILED,
  CANCEL,
  CONFIRM_LATE_CHANGE,
  JOIN,
  KEEP_APPOINTMENT,
  NO_UPCOMING,
  PAST_HEADING,
  PENDING_NOTICE,
  RESCHEDULE,
  UPCOMING_HEADING,
} from "./appointmentsCopy"
import { formatWhen } from "./formatting"

/**
 * How long before the start a join link is worth offering.
 *
 * A courtesy, and only that: the link is whatever the practice put on the
 * appointment, and nothing here can tell whether the room is open. Early
 * enough that a patient who arrives punctually finds the button waiting, and
 * late enough that it is not sitting there all week.
 */
const JOIN_WINDOW_MINUTES = 15

function withinJoinWindow(appointment: PatientAppointment, now: Date): boolean {
  const start = new Date(appointment.start_at).getTime()
  const end = new Date(appointment.end_at).getTime()
  if (Number.isNaN(start) || Number.isNaN(end)) return false
  return now.getTime() >= start - JOIN_WINDOW_MINUTES * 60_000 && now.getTime() <= end
}

/** Settled one way or another, so nothing about it can still be changed. */
function isOver(appointment: PatientAppointment, now: Date): boolean {
  if (appointment.status === "cancelled") return true
  if (appointment.status === "completed" || appointment.status === "no_show") return true
  const start = new Date(appointment.start_at).getTime()
  return Number.isNaN(start) || start < now.getTime()
}

export interface AppointmentsListProps {
  token: string
  appointments: PatientAppointment[]
  timeZone: string
  /** Whether the practice lets a patient change an appointment online. */
  canChange: boolean
  /** Fixed "now" for tests; defaults to the real clock. */
  now?: Date
  onReschedule: (appointment: PatientAppointment) => void
  /** A cancellation went through; the parent refetches. */
  onCancelled: () => void
}

export function AppointmentsList({
  token,
  appointments,
  timeZone,
  canChange,
  now,
  onReschedule,
  onCancelled,
}: AppointmentsListProps) {
  const clock = now ?? new Date()
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The engine's late-change sentence, keyed by the appointment it is about.
  const [lateChange, setLateChange] = useState<{ id: string; message: string } | null>(null)

  async function cancel(appointment: PatientAppointment, acknowledgeLateChange: boolean) {
    setBusyId(appointment.id)
    setError(null)
    const result = await cancelPatientAppointment({
      token,
      appointmentId: appointment.id,
      acknowledgeLateChange,
    })
    setBusyId(null)

    if (result.ok) {
      setLateChange(null)
      onCancelled()
      return
    }
    if (result.code === "LATE_CHANGE_NOT_ACKNOWLEDGED") {
      setLateChange({ id: appointment.id, message: result.message ?? "" })
      return
    }
    setError(ACTION_FAILED)
  }

  const upcoming = appointments
    .filter((appointment) => !isOver(appointment, clock))
    .sort((a, b) => a.start_at.localeCompare(b.start_at))
  const past = appointments
    .filter((appointment) => isOver(appointment, clock))
    .sort((a, b) => b.start_at.localeCompare(a.start_at))

  function row(appointment: PatientAppointment, actionable: boolean) {
    const answering = lateChange?.id === appointment.id
    const joinable =
      actionable &&
      typeof appointment.video_link === "string" &&
      appointment.video_link !== "" &&
      withinJoinWindow(appointment, clock)

    return (
      <li
        key={appointment.id}
        data-testid="appointments-row"
        className="flex flex-col gap-1 rounded-md border border-neutral-200 p-3"
      >
        <span data-testid="appointments-row-when" className="text-sm font-medium text-neutral-900">
          {formatWhen(appointment.start_at, timeZone)}
        </span>
        <span className="text-sm text-neutral-600">{appointment.session_type}</span>

        {appointment.status === "pending" ? (
          <span data-testid="appointments-row-pending" className="text-sm text-amber-700">
            {PENDING_NOTICE}
          </span>
        ) : (
          <span data-testid="appointments-row-status" className="text-sm capitalize text-neutral-600">
            {appointment.status.replace("_", " ")}
          </span>
        )}

        {answering ? (
          <div data-testid="appointments-row-late-change" className="mt-2 space-y-2">
            <p className="text-sm text-neutral-800">{lateChange.message}</p>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busyId === appointment.id}
                data-testid="appointments-row-late-change-cancel"
                onClick={() => setLateChange(null)}
              >
                {KEEP_APPOINTMENT}
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={busyId === appointment.id}
                data-testid="appointments-row-late-change-confirm"
                onClick={() => void cancel(appointment, true)}
              >
                {CONFIRM_LATE_CHANGE}
              </Button>
            </div>
          </div>
        ) : (
          (joinable || (actionable && canChange)) && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {joinable && appointment.video_link && (
                <Button asChild type="button" size="sm" data-testid="appointments-row-join">
                  <a href={appointment.video_link} target="_blank" rel="noreferrer">
                    {JOIN}
                  </a>
                </Button>
              )}
              {actionable && canChange && (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    data-testid="appointments-row-reschedule"
                    onClick={() => onReschedule(appointment)}
                  >
                    {RESCHEDULE}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busyId === appointment.id}
                    data-testid="appointments-row-cancel"
                    onClick={() => void cancel(appointment, false)}
                  >
                    {busyId === appointment.id ? (
                      <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                    ) : (
                      CANCEL
                    )}
                  </Button>
                </>
              )}
            </div>
          )
        )}
      </li>
    )
  }

  return (
    <div data-testid="appointments-list" className="space-y-6">
      {error !== null && (
        <p data-testid="appointments-list-error" className="text-sm text-red-600">
          {error}
        </p>
      )}

      <section>
        <h3 className="mb-2 text-sm font-semibold text-neutral-900">{UPCOMING_HEADING}</h3>
        {upcoming.length === 0 ? (
          <p data-testid="appointments-none-upcoming" className="text-sm text-neutral-600">
            {NO_UPCOMING}
          </p>
        ) : (
          <ul className="space-y-2">{upcoming.map((a) => row(a, true))}</ul>
        )}
      </section>

      {past.length > 0 && (
        <details data-testid="appointments-past">
          <summary className="cursor-pointer text-sm font-semibold text-neutral-900">
            {PAST_HEADING}
          </summary>
          <ul className="mt-2 space-y-2">{past.map((a) => row(a, false))}</ul>
        </details>
      )}
    </div>
  )
}
