// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { format, isAfter, startOfDay } from "date-fns"

/** Whole days, both ends included, `start` never after `end`. */
export interface DayRange {
  start: Date
  end: Date
}

/** The handlers a view wires onto its day cells. */
export interface DayRangeHandlers {
  /** Press on a day. `selectNow` selects it at once; otherwise the range
   * starts only once the pointer reaches another day, so a plain click can
   * keep meaning what it meant before. */
  begin: (day: Date, selectNow: boolean) => void
  /** The pointer, still pressed, has reached `day`. */
  extend: (day: Date) => void
  /** Select one day outright (keyboard), or stretch to it with Shift. */
  pick: (day: Date, stretch: boolean) => void
  /** Whether a press turned into a drag across days. */
  dragged: () => boolean
  isSelected: (day: Date) => boolean
}

function ordered(a: Date, b: Date): DayRange {
  const x = startOfDay(a)
  const y = startOfDay(b)
  return isAfter(x, y) ? { start: y, end: x } : { start: x, end: y }
}

/** `YYYY-MM-DD` for a calendar day, as the browser sees it. */
export function civilDate(day: Date): string {
  return format(day, "yyyy-MM-dd")
}

/**
 * Select a day, or drag across several, on the calendar's day cells.
 *
 * Kept apart from any one view so the week header and the month grid select
 * the same way, and the selection survives switching between them.
 */
export function useDayRangeSelection(): {
  range: DayRange | null
  clear: () => void
  handlers: DayRangeHandlers
} {
  const [range, setRange] = useState<DayRange | null>(null)
  const origin = useRef<Date | null>(null)
  const moved = useRef(false)

  // A drag ends wherever the pointer is let go, on a day cell or not.
  useEffect(() => {
    const end = () => {
      origin.current = null
    }
    window.addEventListener("pointerup", end)
    window.addEventListener("pointercancel", end)
    return () => {
      window.removeEventListener("pointerup", end)
      window.removeEventListener("pointercancel", end)
    }
  }, [])

  const clear = useCallback(() => setRange(null), [])

  // Escape lets go of the selection, like any other transient choice.
  useEffect(() => {
    if (!range) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setRange(null)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [range])

  const handlers = useMemo<DayRangeHandlers>(
    () => ({
      begin: (day, selectNow) => {
        origin.current = day
        moved.current = false
        if (selectNow) setRange(ordered(day, day))
      },
      extend: (day) => {
        const from = origin.current
        if (!from) return
        if (civilDate(from) !== civilDate(day)) moved.current = true
        if (moved.current) setRange(ordered(from, day))
      },
      pick: (day, stretch) => {
        setRange((current) => (stretch && current ? ordered(current.start, day) : ordered(day, day)))
      },
      dragged: () => moved.current,
      isSelected: (day) => {
        if (!range) return false
        const d = startOfDay(day).getTime()
        return d >= range.start.getTime() && d <= range.end.getTime()
      },
    }),
    [range],
  )

  return { range, clear, handlers }
}
