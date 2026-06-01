// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useMemo, useRef } from "react"
import { format, isSameMonth, isToday } from "date-fns"
import type { AppointmentResponse } from "@/types/scheduling"
import { monthGridDays } from "./dateUtils"
import { editorialStatusMeta } from "./status"

/** Click/double-click disambiguation window (ms) — mirrors the wrapper used
 * by week/day views so month chips behave identically. */
const CLICK_DELAY_MS = 220

interface EditorialMonthViewProps {
  anchor: Date
  appointments: AppointmentResponse[]
  patientMap: Map<string, string>
  onSelectDay: (date: Date) => void
  /** Single click on a chip → open the peek popover anchored to its rect. */
  onPeek: (appointment: AppointmentResponse, anchorRect: DOMRect) => void
  /** Double click on a chip → open the edit flow. */
  onEdit: (appointment: AppointmentResponse) => void
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
const MAX_CHIPS_PER_DAY = 3

export function EditorialMonthView({
  anchor,
  appointments,
  patientMap,
  onSelectDay,
  onPeek,
  onEdit,
}: EditorialMonthViewProps) {
  const days = useMemo(() => monthGridDays(anchor), [anchor])

  const apptsByDay = useMemo(() => {
    const map = new Map<string, AppointmentResponse[]>()
    for (const appt of appointments) {
      const key = format(new Date(appt.start_at), "yyyy-MM-dd")
      const list = map.get(key) ?? []
      list.push(appt)
      map.set(key, list)
    }
    for (const list of map.values()) {
      list.sort(
        (a, b) => new Date(a.start_at).getTime() - new Date(b.start_at).getTime(),
      )
    }
    return map
  }, [appointments])

  return (
    <div
      className="ed-fade-in flex flex-col overflow-hidden rounded-xl"
      style={{
        backgroundColor: "var(--ed-canvas-elev)",
        boxShadow: "var(--ed-shadow-card)",
      }}
    >
      <div
        className="grid border-b text-[10px] font-semibold uppercase tracking-[0.18em]"
        style={{
          borderColor: "var(--ed-hairline-strong)",
          gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
          color: "var(--ed-ink-soft)",
        }}
      >
        {WEEKDAYS.map((d) => (
          <div key={d} className="px-3 py-3 text-left">
            {d}
          </div>
        ))}
      </div>
      <div
        className="grid"
        style={{
          gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
          gridTemplateRows: "repeat(6, minmax(112px, 1fr))",
        }}
      >
        {days.map((day, idx) => {
          const inMonth = isSameMonth(day, anchor)
          const today = isToday(day)
          const key = format(day, "yyyy-MM-dd")
          const dayAppts = apptsByDay.get(key) ?? []
          const visible = dayAppts.slice(0, MAX_CHIPS_PER_DAY)
          const overflow = dayAppts.length - visible.length

          return (
            <div
              key={idx}
              role="button"
              tabIndex={0}
              onClick={() => onSelectDay(day)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault()
                  onSelectDay(day)
                }
              }}
              className="flex cursor-pointer flex-col items-stretch gap-1 px-2 py-2 text-left outline-none transition-colors hover:bg-[var(--ed-pill-hover)] focus-visible:bg-[var(--ed-pill-hover)]"
              style={{
                borderTop: idx >= 7 ? "1px solid var(--ed-hairline)" : undefined,
                borderLeft:
                  idx % 7 !== 0 ? "1px solid var(--ed-hairline)" : undefined,
                backgroundColor: today ? "var(--ed-today-tint)" : undefined,
                opacity: inMonth ? 1 : 0.5,
              }}
              aria-label={format(day, "PPPP")}
            >
              <div className="flex items-center gap-1.5">
                {today ? (
                  <span
                    className="flex h-6 w-6 items-center justify-center rounded-full font-display text-[12px] font-semibold"
                    style={{
                      backgroundColor: "var(--ed-today-circle)",
                      color: "var(--ed-today-circle-fg)",
                    }}
                  >
                    {format(day, "d")}
                  </span>
                ) : (
                  <span
                    className="font-display text-[14px] font-semibold"
                    style={{
                      color: inMonth ? "var(--ed-ink)" : "var(--ed-ink-soft)",
                    }}
                  >
                    {format(day, "d")}
                  </span>
                )}
              </div>

              <div className="flex flex-col gap-0.5">
                {visible.map((appt) => (
                  <MonthChip
                    key={appt.id}
                    appointment={appt}
                    name={patientMap.get(appt.patient_id) ?? appt.title}
                    onPeek={onPeek}
                    onEdit={onEdit}
                  />
                ))}
                {overflow > 0 && (
                  <span
                    className="px-1.5 text-[10px] font-medium"
                    style={{ color: "var(--ed-ink-soft)" }}
                  >
                    +{overflow} more
                  </span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

interface MonthChipProps {
  appointment: AppointmentResponse
  name: string
  onPeek: (appointment: AppointmentResponse, anchorRect: DOMRect) => void
  onEdit: (appointment: AppointmentResponse) => void
}

/** A single month-view event chip with click (peek) / dblclick (edit)
 * disambiguation matching the week/day {@link EditorialEventWrapper}. */
function MonthChip({ appointment, name, onPeek, onEdit }: MonthChipProps) {
  const ref = useRef<HTMLSpanElement>(null)
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const start = new Date(appointment.start_at)
  const cancelled = appointment.status === "cancelled"
  const meta = editorialStatusMeta(appointment.status)
  const StatusIcon = meta.Icon

  useEffect(() => {
    return () => {
      if (clickTimer.current) clearTimeout(clickTimer.current)
    }
  }, [])

  const peek = () => {
    if (clickTimer.current) clearTimeout(clickTimer.current)
    const rect = ref.current?.getBoundingClientRect()
    if (!rect) return
    clickTimer.current = setTimeout(() => {
      clickTimer.current = null
      onPeek(appointment, rect)
    }, CLICK_DELAY_MS)
  }

  const edit = () => {
    if (clickTimer.current) {
      clearTimeout(clickTimer.current)
      clickTimer.current = null
    }
    onEdit(appointment)
  }

  return (
    <span
      ref={ref}
      role="button"
      tabIndex={0}
      onClick={(e) => {
        e.stopPropagation()
        peek()
      }}
      onDoubleClick={(e) => {
        e.stopPropagation()
        edit()
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          e.stopPropagation()
          edit()
        }
      }}
      aria-label={`${name} at ${format(start, "h:mm a")} — ${meta.label}`}
      className="ed-event group flex items-center gap-1.5 truncate rounded-md px-1.5 py-0.5 text-[11px]"
      style={{
        color: "var(--ed-ink)",
        textDecoration: cancelled ? "line-through" : undefined,
        opacity: cancelled ? 0.6 : 1,
      }}
    >
      <StatusIcon
        aria-hidden
        className="h-3 w-3 shrink-0"
        style={{ color: meta.rail }}
      />
      <span
        className="shrink-0 font-semibold tabular-nums"
        style={{ color: "var(--ed-ink-muted)" }}
      >
        {format(start, "h:mm")}
      </span>
      <span className="truncate">{name}</span>
    </span>
  )
}
