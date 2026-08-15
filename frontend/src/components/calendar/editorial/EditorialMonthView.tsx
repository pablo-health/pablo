// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useMemo, useRef } from "react"
import { format, isSameMonth, isToday } from "date-fns"
import type { AppointmentResponse } from "@/types/scheduling"
import { monthGridDays } from "./dateUtils"
import { editorialStatusMeta } from "./status"
import { useClickPeekEdit } from "./useClickPeekEdit"

interface EditorialMonthViewProps {
  anchor: Date
  appointments: AppointmentResponse[]
  patientMap: Map<string, string>
  onSelectDay: (date: Date) => void
  /** Single click on a chip → open the peek popover anchored to its rect. */
  onPeek: (appointment: AppointmentResponse, anchorRect: DOMRect) => void
  /** Double click on a chip → open the edit flow. */
  onEdit: (appointment: AppointmentResponse) => void
  /** Right click on a chip → open the status menu at the cursor. */
  onContextMenu: (appointment: AppointmentResponse, x: number, y: number) => void
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
  onContextMenu,
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
        className="grid border-b text-[10.5px] font-semibold uppercase tracking-[0.16em]"
        style={{
          borderColor: "var(--ed-hairline-strong)",
          gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
          color: "var(--ed-ink-soft)",
        }}
      >
        {WEEKDAYS.map((d) => (
          <div key={d} className="px-3 py-3 text-center">
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
              className="flex cursor-pointer flex-col items-stretch gap-[3px] px-[7px] py-[6px] text-left outline-none transition-colors hover:bg-[var(--ed-pill-hover)] focus-visible:bg-[var(--ed-pill-hover)]"
              style={{
                borderTop: idx >= 7 ? "1px solid var(--ed-hairline)" : undefined,
                backgroundColor: today ? "var(--ed-today-tint)" : undefined,
              }}
              aria-label={format(day, "PPPP")}
            >
              <div className="flex items-center justify-end">
                {today ? (
                  <span
                    className="flex h-[22px] w-[22px] items-center justify-center rounded-full font-display text-[12px] font-semibold"
                    style={{
                      backgroundColor: "var(--ed-today-circle)",
                      color: "var(--ed-today-circle-fg)",
                    }}
                  >
                    {format(day, "d")}
                  </span>
                ) : (
                  <span
                    className="font-display text-[12px] font-medium"
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
                    name={appt.patient_name ?? patientMap.get(appt.patient_id) ?? appt.title}
                    onPeek={onPeek}
                    onEdit={onEdit}
                    onContextMenu={onContextMenu}
                  />
                ))}
                {overflow > 0 && (
                  <span
                    className="px-1.5 text-[10.5px] font-semibold"
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
  onContextMenu: (appointment: AppointmentResponse, x: number, y: number) => void
}

/** A single month-view event chip with click (peek) / dblclick (edit) /
 * right-click (status menu) disambiguation via the shared
 * {@link useClickPeekEdit} hook, matching week/day behaviour exactly. */
function MonthChip({ appointment, name, onPeek, onEdit, onContextMenu }: MonthChipProps) {
  const ref = useRef<HTMLSpanElement>(null)
  const start = new Date(appointment.start_at)
  const cancelled = appointment.status === "cancelled"
  const meta = editorialStatusMeta(appointment.status)

  const { handleClick, handleDoubleClick, handleContextMenu } =
    useClickPeekEdit({
      appointment,
      onPeek,
      onEdit,
      onContextMenu,
      getRect: () => ref.current?.getBoundingClientRect(),
    })

  return (
    <span
      ref={ref}
      role="button"
      tabIndex={0}
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      onContextMenu={handleContextMenu}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault()
          e.stopPropagation()
          const rect = ref.current?.getBoundingClientRect()
          if (rect) onPeek(appointment, rect)
        }
      }}
      aria-label={`${name} at ${format(start, "h:mm a")} — ${meta.label}`}
      className="group flex items-center gap-1.5 truncate rounded-[5px] px-1.5 py-0.5 text-[11px]"
      style={{
        backgroundColor: meta.bg,
        color: meta.fg,
        textDecoration: cancelled ? "line-through" : undefined,
        opacity: cancelled ? 0.6 : 1,
      }}
    >
      <span
        aria-hidden
        className="h-[5px] w-[5px] shrink-0 rounded-full"
        style={{ backgroundColor: meta.rail }}
      />
      <span className="shrink-0 tabular-nums" style={{ opacity: 0.85 }}>
        {format(start, "h:mm")}
      </span>
      <span className="truncate font-semibold">{name}</span>
    </span>
  )
}
