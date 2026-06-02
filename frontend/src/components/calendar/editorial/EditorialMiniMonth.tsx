// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { addMonths, format, isSameDay, isSameMonth, isToday, startOfMonth } from "date-fns"
import { monthGridDays } from "./dateUtils"

interface EditorialMiniMonthProps {
  selected: Date
  onSelect: (date: Date) => void
}

const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"]

export function EditorialMiniMonth({ selected, onSelect }: EditorialMiniMonthProps) {
  const [browse, setBrowse] = useState<Date>(startOfMonth(selected))
  const days = monthGridDays(browse)

  return (
    <div className="select-none">
      <div className="mb-2.5 flex items-center justify-between">
        <h3
          className="font-display text-[15.5px] font-semibold tracking-[-0.01em]"
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
        className="mb-1 grid grid-cols-7 gap-y-1 text-center text-[10px] font-semibold uppercase tracking-[0.14em]"
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
          const isSelected = isSameDay(day, selected)
          return (
            <button
              key={i}
              type="button"
              onClick={() => onSelect(day)}
              data-today={today}
              className="ed-mini-day relative mx-auto flex h-[30px] w-[30px] items-center justify-center rounded-full text-[12.5px] outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
              style={{
                color: today
                  ? "var(--ed-mini-today-fg)"
                  : inMonth
                    ? "var(--ed-ink)"
                    : "var(--ed-ink-soft)",
                backgroundColor: today ? "var(--ed-mini-today-bg)" : "transparent",
                fontWeight: today || isSelected ? 600 : 400,
              }}
              aria-label={format(day, "PPPP")}
              aria-current={today ? "date" : undefined}
            >
              {format(day, "d")}
              {!today && isSelected && (
                <span
                  aria-hidden
                  className="absolute bottom-[1px] h-0.5 w-1.5 rounded-full"
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
