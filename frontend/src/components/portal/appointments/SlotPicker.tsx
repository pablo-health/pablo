// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A day of openings, with arrows to walk to the next one.
 *
 * Fetches on mount and on every day change, and owns its own loading, empty
 * and error states — the same self-contained pattern the rest of the portal's
 * screens use, so a slot can be dropped into the shell without a parent
 * arranging data for it.
 *
 * **Day navigation is bounded to the practice's own horizon**, which the
 * server enforces independently. Stopping the arrows at the same edge is a
 * courtesy that keeps a patient from walking into a day that answers "nothing
 * open" for a reason that has nothing to do with the diary; it is not the
 * control, and it must never be more permissive than the server.
 *
 * A 403 mid-browse means the practice turned booking off between one render
 * and the next. That surfaces to the parent rather than being drawn here: the
 * module already knows how to say what a patient can do instead, and two
 * screens answering the same question differently is how copy starts
 * contradicting itself.
 */

"use client"

import { useEffect, useState } from "react"
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { getPatientSlots, type PatientSlot } from "@/lib/api/patientAppointments"
import { NO_SLOTS, SLOTS_FAILED } from "./appointmentsCopy"
import { addDays, daysBetween, formatDayHeading, formatTime, practiceDate } from "./formatting"

type Phase = "loading" | "ready" | "error"

export interface SlotPickerProps {
  token: string
  timeZone: string
  durationMinutes: number
  maxHorizonDays: number
  /** Defaults to now. Tests inject a fixed instant so the day is not the clock's. */
  now?: Date
  onPick: (slot: PatientSlot) => void
  /** The practice stopped taking bookings while the patient was browsing. */
  onBookingClosed: () => void
}

export function SlotPicker({
  token,
  timeZone,
  durationMinutes,
  maxHorizonDays,
  now,
  onPick,
  onBookingClosed,
}: SlotPickerProps) {
  const firstDay = practiceDate(now ?? new Date(), timeZone)
  const [day, setDay] = useState(firstDay)
  const [phase, setPhase] = useState<Phase>("loading")
  const [slots, setSlots] = useState<PatientSlot[]>([])

  useEffect(() => {
    let cancelled = false

    async function load() {
      setPhase("loading")
      const result = await getPatientSlots({ token, date: day, durationMinutes })
      if (cancelled) return
      if (!result.ok) {
        if (result.status === 403) {
          onBookingClosed()
          return
        }
        setPhase("error")
        return
      }
      setSlots(result.data)
      setPhase("ready")
    }

    void load()
    return () => {
      cancelled = true
    }
    // `onBookingClosed` is deliberately absent: it is a parent callback whose
    // identity changes every render, and depending on it would refetch the
    // day on each one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, day, durationMinutes])

  const offset = daysBetween(firstDay, day)
  const canGoBack = offset > 0
  const canGoForward = offset < maxHorizonDays

  return (
    <div data-testid="appointments-slot-picker" className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={!canGoBack}
          aria-label="Previous day"
          data-testid="appointments-slots-prev"
          onClick={() => setDay((current) => addDays(current, -1))}
        >
          <ChevronLeft className="size-4" aria-hidden="true" />
        </Button>
        <span data-testid="appointments-slots-day" className="text-sm font-medium text-neutral-900">
          {formatDayHeading(day, timeZone)}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={!canGoForward}
          aria-label="Next day"
          data-testid="appointments-slots-next"
          onClick={() => setDay((current) => addDays(current, 1))}
        >
          <ChevronRight className="size-4" aria-hidden="true" />
        </Button>
      </div>

      {phase === "loading" ? (
        <div data-testid="appointments-slots-loading" className="flex justify-center py-6">
          <Loader2 className="size-5 animate-spin text-neutral-400" aria-hidden="true" />
          <span className="sr-only">Loading…</span>
        </div>
      ) : phase === "error" ? (
        <p data-testid="appointments-slots-error" className="text-sm text-neutral-600">
          {SLOTS_FAILED}
        </p>
      ) : slots.length === 0 ? (
        <p data-testid="appointments-slots-empty" className="text-sm text-neutral-600">
          {NO_SLOTS}
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {slots.map((slot) => (
            <Button
              key={slot.start_at}
              type="button"
              variant="outline"
              data-testid="appointments-slot"
              onClick={() => onPick(slot)}
            >
              {formatTime(slot.start_at, timeZone)}
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}
