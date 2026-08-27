// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { useFreeSlots } from "@/hooks/useAvailability"

function toTimeInputValue(iso: string): string {
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`
}

function formatSlotTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
}

interface AvailabilitySlotPickerProps {
  date: string
  duration: number
  selectedTime: string
  onSelect: (timeStr: string) => void
  token?: string
}

/**
 * Lists open slots for a chosen date, sourced from the free-slots engine.
 * A suggestion, not a gate — the date/time inputs above it always accept a
 * manually-typed time regardless of what (or whether) this renders.
 */
export function AvailabilitySlotPicker({
  date,
  duration,
  selectedTime,
  onSelect,
  token,
}: AvailabilitySlotPickerProps) {
  const { data, isLoading } = useFreeSlots(date, duration, token)

  if (isLoading) {
    return (
      <p className="mt-2 text-[12.5px]" style={{ color: "var(--ed-ink-soft)" }}>
        Checking your availability…
      </p>
    )
  }

  if (!data) return null

  if (!data.configured) {
    return (
      <p className="mt-2 text-[12.5px]" style={{ color: "var(--ed-ink-soft)" }}>
        You haven&apos;t set up your availability yet.{" "}
        <Link
          href="/dashboard/settings"
          className="font-semibold underline"
          style={{ color: "var(--ed-accent)" }}
        >
          Set it up
        </Link>{" "}
        to see open slots here — you can still enter a time manually.
      </p>
    )
  }

  if (data.slots.length === 0) {
    return (
      <p className="mt-2 text-[12.5px]" style={{ color: "var(--ed-ink-soft)" }}>
        No openings on this day — you can still enter a time manually.
      </p>
    )
  }

  return (
    <div className="mt-2 flex flex-wrap gap-[7px]" role="group" aria-label="Open slots">
      {data.slots.map((slot) => {
        const timeStr = toTimeInputValue(slot.start)
        const active = timeStr === selectedTime
        return (
          <button
            key={slot.start}
            type="button"
            aria-pressed={active}
            onClick={() => onSelect(timeStr)}
            className="cursor-pointer rounded-full border px-3 py-[6px] text-[12.5px] font-semibold"
            style={{
              borderColor: active ? "var(--ed-cta-bg)" : "var(--ed-field-border)",
              backgroundColor: active ? "var(--ed-cta-bg)" : "transparent",
              color: active ? "var(--ed-cta-fg)" : "var(--ed-ink-muted)",
            }}
          >
            {formatSlotTime(slot.start)}
          </button>
        )
      })}
    </div>
  )
}
