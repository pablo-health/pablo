// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { format, isSameDay } from "date-fns"
import { X } from "lucide-react"
import { CalendarRangeActions } from "@/components/calendar/CalendarRangeActions"
import { civilDate, type DayRange } from "./useDayRangeSelection"

/** "Mon Dec 22", or "Mon Dec 22 – Fri Jan 2" for more than one day. */
export function rangeLabel(range: DayRange): string {
  const first = format(range.start, "EEE MMM d")
  return isSameDay(range.start, range.end) ? first : `${first} – ${format(range.end, "EEE MMM d")}`
}

/**
 * The selected days, with room for actions on them.
 *
 * The actions themselves come from the {@link CalendarRangeActions} slot; the
 * bar itself only names the selection and lets go of it.
 */
export function CalendarRangeBar({ range, onClear }: { range: DayRange; onClear: () => void }) {
  return (
    <div
      role="region"
      aria-label="Selected dates"
      data-testid="calendar-range-selection"
      className="ed-fade-in flex flex-wrap items-center gap-3 rounded-xl px-4 py-2"
      style={{
        backgroundColor: "var(--ed-canvas-elev)",
        border: "1px solid var(--ed-hairline-strong)",
      }}
    >
      <span className="text-sm font-semibold" style={{ color: "var(--ed-ink)" }}>
        {rangeLabel(range)}
      </span>
      <div className="flex flex-1 flex-wrap items-center gap-2">
        <CalendarRangeActions
          startDate={civilDate(range.start)}
          endDate={civilDate(range.end)}
          onDone={onClear}
        />
      </div>
      <button
        type="button"
        onClick={onClear}
        aria-label="Clear selected dates"
        className="rounded-full p-1 transition-colors hover:bg-[var(--ed-pill-hover)]"
        style={{ color: "var(--ed-ink-muted)" }}
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
