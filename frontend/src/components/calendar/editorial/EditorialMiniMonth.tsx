// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { addMonths, format, isSameDay, isSameMonth, isToday, startOfMonth } from "date-fns"
import { monthGridDays } from "./dateUtils"

interface EditorialMiniMonthProps {
  /** Day(s) shown highlighted as the visible range. */
  highlighted: Date[]
  selected: Date
  onSelect: (date: Date) => void
}

const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"]

export function EditorialMiniMonth({ highlighted, selected, onSelect }: EditorialMiniMonthProps) {
  const [browse, setBrowse] = useState<Date>(startOfMonth(selected))
  const days = monthGridDays(browse)
  const highlightedKeys = new Set(highlighted.map((d) => d.toDateString()))

  return (
    <div className="select-none">
      <div className="mb-3 flex items-center justify-between">
        <h3
          className="font-display text-base font-semibold tracking-tight"
          style={{ color: "var(--ed-ink)" }}
        >
          {format(browse, "MMMM yyyy")}
        </h3>
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => setBrowse((d) => addMonths(d, -1))}
            className="rounded-full p-1 transition-colors hover:bg-[var(--ed-pill-hover)]"
            style={{ color: "var(--ed-ink-muted)" }}
            aria-label="Previous month"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setBrowse((d) => addMonths(d, 1))}
            className="rounded-full p-1 transition-colors hover:bg-[var(--ed-pill-hover)]"
            style={{ color: "var(--ed-ink-muted)" }}
            aria-label="Next month"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div
        className="mb-1 grid grid-cols-7 gap-y-1 text-center text-[10px] font-semibold uppercase tracking-[0.18em]"
        style={{ color: "var(--ed-ink-soft)" }}
      >
        {WEEKDAYS.map((d, i) => (
          <span key={i}>{d}</span>
        ))}
      </div>

      <div className="grid grid-cols-7 gap-y-0.5">
        {days.map((day, i) => {
          const inMonth = isSameMonth(day, browse)
          const today = isToday(day)
          const isHighlighted = highlightedKeys.has(day.toDateString())
          const isSelected = isSameDay(day, selected)
          return (
            <button
              key={i}
              type="button"
              onClick={() => onSelect(day)}
              data-today={today}
              data-highlighted={isHighlighted}
              className="ed-mini-day relative mx-auto flex h-8 w-8 items-center justify-center rounded-full text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
              style={{
                color: today
                  ? "var(--ed-today-circle-fg)"
                  : inMonth
                    ? "var(--ed-ink)"
                    : "var(--ed-ink-soft)",
                backgroundColor: today
                  ? "var(--ed-today-circle)"
                  : isHighlighted
                    ? "var(--ed-pill-hover)"
                    : "transparent",
                fontWeight: today || isSelected ? 600 : 400,
              }}
              aria-label={format(day, "PPPP")}
              aria-current={today ? "date" : undefined}
            >
              {format(day, "d")}
              {!today && isSelected && (
                <span
                  aria-hidden
                  className="absolute -bottom-0.5 h-0.5 w-1.5 rounded-full"
                  style={{ backgroundColor: "var(--ed-ink)" }}
                />
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
