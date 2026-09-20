// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Appointments in the patient portal: the list, and booking when the practice
 * offers it.
 *
 * The whole surface hangs off the session token it is handed. Nothing here
 * goes looking for a signed-in user, because on this surface there isn't one.
 *
 * **The practice's policy is asked for, not inferred.** The module fetches
 * what the practice allows before it renders anything, so the first paint is
 * already right: a booking control where booking is offered, and a sentence
 * naming how to book where it is not. Learning the answer from a 403 would
 * mean drawing a button and taking it away.
 *
 * **A failed policy fetch degrades to the list, not to an error.** Reading
 * your own appointments does not depend on knowing the practice's booking
 * rules, and losing the whole section over a secondary call would take away
 * the part that still works. The same direction the shell's capability
 * document fails in, and for the same reason.
 *
 * After a booking, a reschedule or a cancellation the list is refetched
 * rather than patched. The server decides the resulting status — a practice
 * that reviews requests answers PENDING where one that confirms answers
 * CONFIRMED — so a refetch is both simpler and the only version certainly
 * true.
 */

"use client"

import { useCallback, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import {
  getBookingOptions,
  getPatientAppointments,
  type PatientAppointment,
  type PatientBookingOptions,
} from "@/lib/api/patientAppointments"
import { AppointmentsList } from "./AppointmentsList"
import { BookingFlow } from "./BookingFlow"
import {
  BOOKING_OFF,
  CONTACT_TO_BOOK,
  LOAD_FAILED,
  NOTHING_BOOKABLE,
  REQUEST_APPOINTMENT,
  callToBook,
} from "./appointmentsCopy"

const keys = {
  appointments: (token: string) => ["patient-appointments", "list", token] as const,
  options: (token: string) => ["patient-appointments", "options", token] as const,
}

export interface PortalAppointmentsProps {
  sessionToken: string
  /** Fixed "now" for tests; defaults to the real clock. */
  now?: Date
}

export function PortalAppointments({ sessionToken, now }: PortalAppointmentsProps) {
  const queryClient = useQueryClient()
  const [booking, setBooking] = useState<{ rescheduling?: PatientAppointment } | null>(null)
  // Set when the practice turns booking off mid-journey. Held so the module
  // stops offering it without waiting for the policy query to come round.
  const [closedMidJourney, setClosedMidJourney] = useState(false)

  const appointments = useQuery({
    queryKey: keys.appointments(sessionToken),
    queryFn: () => getPatientAppointments(sessionToken),
  })

  const options = useQuery({
    queryKey: keys.options(sessionToken),
    queryFn: () => getBookingOptions(sessionToken),
    retry: false,
  })

  const refetchAll = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: keys.appointments(sessionToken) })
    void queryClient.invalidateQueries({ queryKey: keys.options(sessionToken) })
  }, [queryClient, sessionToken])

  const handleBookingClosed = useCallback(() => {
    setClosedMidJourney(true)
    setBooking(null)
    refetchAll()
  }, [refetchAll])

  if (appointments.isError || (appointments.data && !appointments.data.ok)) {
    return (
      <p data-testid="portal-appointments-error" className="text-sm text-neutral-600">
        {LOAD_FAILED}
      </p>
    )
  }

  if (!appointments.data) {
    return (
      <p data-testid="portal-appointments-loading" className="text-sm text-neutral-600">
        Loading…
      </p>
    )
  }

  // A policy we could not read is treated as "not offered here": the module
  // renders the list and says how to reach the practice, which is true
  // whatever the answer would have been. Offering a booking control on a
  // guess would send the patient into a flow that may 403 at the first step.
  const policy: PatientBookingOptions | null =
    options.data && options.data.ok ? options.data.data : null
  const timeZone = policy?.practice_timezone ?? "UTC"
  const canBook =
    !closedMidJourney && policy !== null && policy.self_booking && policy.session_types.length > 0
  const canChange = !closedMidJourney && policy !== null && policy.self_booking

  if (booking !== null && policy !== null) {
    return (
      <BookingFlow
        token={sessionToken}
        timeZone={timeZone}
        durationMinutes={
          booking.rescheduling?.duration_minutes ?? policy.session_types[0]?.duration_minutes ?? 50
        }
        maxHorizonDays={policy.max_horizon_days}
        sessionType={policy.session_types[0]?.name}
        rescheduling={booking.rescheduling}
        now={now}
        onDone={(booked) => {
          setBooking(null)
          if (booked !== null) refetchAll()
        }}
        onBookingClosed={handleBookingClosed}
      />
    )
  }

  return (
    <div data-testid="portal-appointments" className="space-y-4">
      <AppointmentsList
        token={sessionToken}
        appointments={appointments.data.data}
        timeZone={timeZone}
        canChange={canChange}
        now={now}
        onReschedule={(appointment) => setBooking({ rescheduling: appointment })}
        onCancelled={refetchAll}
      />

      {canBook ? (
        <Button
          type="button"
          data-testid="portal-appointments-book"
          onClick={() => setBooking({})}
        >
          {REQUEST_APPOINTMENT}
        </Button>
      ) : (
        <p data-testid="portal-appointments-booking-off" className="text-sm text-neutral-600">
          {/* Two different true statements, and which one depends on what the
              practice did. "Booking is off" would be wrong for a practice
              that turned it on and has opened no appointment type. */}
          {policy !== null && policy.self_booking ? NOTHING_BOOKABLE : BOOKING_OFF}{" "}
          {policy?.practice_phone ? callToBook(policy.practice_phone) : CONTACT_TO_BOOK}
        </p>
      )}
    </div>
  )
}
