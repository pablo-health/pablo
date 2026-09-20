// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The booking journey as a small state machine: browse -> confirm -> done.
 *
 * Reused unchanged for rescheduling by passing the appointment being moved.
 * Nothing is pre-selected in that case — the patient still picks a new time —
 * but the kind of appointment comes from the existing one rather than being
 * chosen again. That is the engine's rule, not a simplification: a reschedule
 * that could also change the session type would be a booking wearing a
 * different verb, and would let a short check-in become a long slot the
 * practice never opened.
 *
 * Shell-agnostic. The session token is injected, never read from an auth
 * hook, because there is no signed-in user on this surface to read one from.
 */

"use client"

import { useState } from "react"
import type { PatientAppointment, PatientSlot } from "@/lib/api/patientAppointments"
import { BookingConfirm } from "./BookingConfirm"
import { BookingConfirmation } from "./BookingConfirmation"
import { SlotPicker } from "./SlotPicker"
import { SLOT_TAKEN } from "./appointmentsCopy"

type Step = "browse" | "confirm" | "done"

export interface BookingFlowProps {
  token: string
  timeZone: string
  durationMinutes: number
  maxHorizonDays: number
  /** Required when booking. The kind of appointment being asked for. */
  sessionType?: string
  /** Present means this is a reschedule of that appointment. */
  rescheduling?: PatientAppointment
  /** Fixed "now" for tests; defaults to the real clock. */
  now?: Date
  /** The journey finished or was abandoned. Carries the result, if any. */
  onDone: (booked: PatientAppointment | null) => void
  /** The practice stopped taking bookings mid-journey. */
  onBookingClosed: () => void
}

export function BookingFlow({
  token,
  timeZone,
  durationMinutes,
  maxHorizonDays,
  sessionType,
  rescheduling,
  now,
  onDone,
  onBookingClosed,
}: BookingFlowProps) {
  const mode = rescheduling ? "reschedule" : "book"
  const effectiveSessionType = rescheduling?.session_type ?? sessionType ?? ""

  const [step, setStep] = useState<Step>("browse")
  const [slot, setSlot] = useState<PatientSlot | null>(null)
  const [booked, setBooked] = useState<PatientAppointment | null>(null)
  const [slotTaken, setSlotTaken] = useState(false)
  // Bumping this remounts the picker, which is what refetches the day. The
  // slot that was taken is gone from the server's answer, so a stale list is
  // the one thing the patient must not be sent back to.
  const [pickerKey, setPickerKey] = useState(0)

  if (step === "done" && booked !== null) {
    return (
      <BookingConfirmation
        appointment={booked}
        timeZone={timeZone}
        mode={mode}
        onDone={() => onDone(booked)}
      />
    )
  }

  if (step === "confirm" && slot !== null) {
    return (
      <BookingConfirm
        token={token}
        timeZone={timeZone}
        slot={slot}
        sessionType={effectiveSessionType}
        mode={mode}
        appointmentId={rescheduling?.id}
        onSuccess={(appointment) => {
          setBooked(appointment)
          setStep("done")
        }}
        onSlotTaken={() => {
          setSlotTaken(true)
          setPickerKey((key) => key + 1)
          setStep("browse")
        }}
        onBookingClosed={onBookingClosed}
        onBack={() => setStep("browse")}
      />
    )
  }

  return (
    <div className="space-y-3">
      {slotTaken && (
        <p data-testid="appointments-slot-taken" className="text-sm text-amber-700">
          {SLOT_TAKEN}
        </p>
      )}
      <SlotPicker
        key={pickerKey}
        token={token}
        timeZone={timeZone}
        durationMinutes={durationMinutes}
        maxHorizonDays={maxHorizonDays}
        now={now}
        onPick={(picked) => {
          setSlotTaken(false)
          setSlot(picked)
          setStep("confirm")
        }}
        onBookingClosed={onBookingClosed}
      />
      <button
        type="button"
        data-testid="appointments-booking-cancel"
        className="text-sm text-neutral-600 underline underline-offset-4"
        onClick={() => onDone(null)}
      >
        Never mind
      </button>
    </div>
  )
}
