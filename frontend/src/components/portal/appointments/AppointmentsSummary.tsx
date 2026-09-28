// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The appointments tile's line on the portal home screen: the next one.
 *
 * Reads the list and the practice's booking options under the same query
 * keys as the appointments section, so opening it draws from what the tile
 * just fetched. The options are needed for one thing only, the practice's
 * timezone: a time in the wrong zone is worse than no time, so without it
 * the tile shows its label alone rather than guessing a zone.
 *
 * An appointment the practice has not confirmed yet reads "Requested", not
 * "Next" — the tile does not call something booked that is still a request.
 *
 * Renders nothing while loading or on a failed fetch.
 */

"use client"

import { useQuery } from "@tanstack/react-query"
import {
  getBookingOptions,
  getPatientAppointments,
  type PatientAppointment,
} from "@/lib/api/patientAppointments"
import { isOver } from "./AppointmentsList"
import { formatWhen } from "./formatting"
import { keys } from "./PortalAppointments"

export function appointmentsSummaryLine(
  appointments: PatientAppointment[],
  timeZone: string,
  now: Date,
): string {
  const next = appointments
    .filter((appointment) => !isOver(appointment, now))
    .sort((a, b) => a.start_at.localeCompare(b.start_at))[0]
  if (next === undefined) return "No upcoming appointments"
  const when = formatWhen(next.start_at, timeZone)
  return next.status === "pending" ? `Requested: ${when}` : `Next: ${when}`
}

export function AppointmentsSummary({
  sessionToken,
  now,
}: {
  sessionToken: string
  /** Fixed "now" for tests; defaults to the real clock. */
  now?: Date
}) {
  const appointments = useQuery({
    queryKey: keys.appointments(sessionToken),
    queryFn: () => getPatientAppointments(sessionToken),
  })
  const options = useQuery({
    queryKey: keys.options(sessionToken),
    queryFn: () => getBookingOptions(sessionToken),
    retry: false,
  })
  if (!appointments.data?.ok || !options.data?.ok) return null
  return (
    <>
      {appointmentsSummaryLine(
        appointments.data.data,
        options.data.data.practice_timezone,
        now ?? new Date(),
      )}
    </>
  )
}
