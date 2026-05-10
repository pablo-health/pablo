// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useMemo } from "react"
import { format, isSameDay, isSameMonth, isToday } from "date-fns"
import type { AppointmentResponse } from "@/types/scheduling"
import { monthGridDays } from "./dateUtils"

interface EditorialMonthViewProps {
  anchor: Date
  appointments: AppointmentResponse[]
  patientMap: Map<string, string>
  onSelectDay: (date: Date) => void
  onSelectAppointment: (appointment: AppointmentResponse) => void
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
const MAX_CHIPS_PER_DAY = 3

const STATUS_DOT: Record<string, string> = {
  confirmed: "var(--ed-status-confirmed-rail)",
  completed: "var(--ed-status-completed-rail)",
  cancelled: "var(--ed-status-cancelled-rail)",
  no_show: "var(--ed-status-noshow-rail)",
}

export function EditorialMonthView({
  anchor,
  appointments,
  patientMap,
  onSelectDay,
  onSelectAppointment,
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
                {visible.map((appt) => {
                  const start = new Date(appt.start_at)
                  const name = patientMap.get(appt.patient_id) ?? appt.title
                  const cancelled = appt.status === "cancelled"
                  return (
                    <span
                      key={appt.id}
                      role="button"
                      tabIndex={0}
                      onClick={(e) => {
                        e.stopPropagation()
                        onSelectAppointment(appt)
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault()
                          e.stopPropagation()
                          onSelectAppointment(appt)
                        }
                      }}
                      className="ed-event group flex items-center gap-1.5 truncate rounded-md px-1.5 py-0.5 text-[11px]"
                      style={{
                        color: "var(--ed-ink)",
                        textDecoration: cancelled ? "line-through" : undefined,
                        opacity: cancelled ? 0.6 : 1,
                      }}
                    >
                      <span
                        aria-hidden
                        className="h-1.5 w-1.5 shrink-0 rounded-full"
                        style={{
                          backgroundColor:
                            STATUS_DOT[appt.status] ?? STATUS_DOT.confirmed,
                        }}
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
                })}
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
