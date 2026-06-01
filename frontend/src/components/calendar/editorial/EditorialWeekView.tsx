// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useMemo, useRef } from "react"
import { format, isSameDay, isToday, startOfDay } from "date-fns"
import type { AppointmentResponse } from "@/types/scheduling"
import { EditorialEventCard } from "./EditorialEventCard"
import { assignLanes } from "./laneLayout"
import { HOUR_ROW_PX, minutesSinceMidnight, weekDays } from "./dateUtils"

interface EditorialWeekViewProps {
  anchor: Date
  appointments: AppointmentResponse[]
  patientMap: Map<string, string>
  onSelectSlot: (start: string, end: string) => void
  onSelectAppointment: (appointment: AppointmentResponse) => void
  /** Hour to scroll to on mount / day change (defaults to 8). */
  scrollToHour?: number
}

const HOURS = Array.from({ length: 24 }, (_, i) => i)

export function EditorialWeekView({
  anchor,
  appointments,
  patientMap,
  onSelectSlot,
  onSelectAppointment,
  scrollToHour = 8,
}: EditorialWeekViewProps) {
  const days = useMemo(() => weekDays(anchor), [anchor])
  const scrollerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollerRef.current) {
      scrollerRef.current.scrollTop = scrollToHour * HOUR_ROW_PX
    }
  }, [scrollToHour])

  const dayBuckets = useMemo(() => {
    const buckets: AppointmentResponse[][] = days.map(() => [])
    for (const appt of appointments) {
      const start = new Date(appt.start_at)
      const idx = days.findIndex((d) => isSameDay(d, start))
      if (idx >= 0) buckets[idx].push(appt)
    }
    return buckets.map((b) => assignLanes(b))
  }, [days, appointments])

  return (
    <div
      className="ed-fade-in flex flex-col overflow-hidden rounded-xl"
      style={{
        backgroundColor: "var(--ed-canvas-elev)",
        boxShadow: "var(--ed-shadow-card)",
      }}
    >
      <DayHeaderRow days={days} />
      <div ref={scrollerRef} className="relative max-h-[68vh] overflow-y-auto">
        <div className="flex">
          <HourRail />
          <div
            className="ed-daycols ed-hourlines relative grid flex-1"
            style={{ gridTemplateColumns: "repeat(7, minmax(0, 1fr))" }}
          >
            {days.map((day, idx) => (
              <DayColumn
                key={day.toISOString()}
                day={day}
                lanes={dayBuckets[idx]}
                patientMap={patientMap}
                onSelectSlot={onSelectSlot}
                onSelectAppointment={onSelectAppointment}
              />
            ))}
            <NowLine days={days} />
          </div>
        </div>
      </div>
    </div>
  )
}

function DayHeaderRow({ days }: { days: Date[] }) {
  return (
    <div
      className="flex border-b"
      style={{ borderColor: "var(--ed-hairline-strong)" }}
    >
      <div className="w-16 shrink-0" aria-hidden />
      <div
        className="ed-daycols grid flex-1"
        style={{ gridTemplateColumns: "repeat(7, minmax(0, 1fr))" }}
      >
        {days.map((day) => {
          const today = isToday(day)
          return (
            <div
              key={day.toISOString()}
              className="flex items-center justify-center py-3"
            >
              {today ? (
                <span
                  className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[13px] font-semibold"
                  style={{
                    backgroundColor: "var(--ed-today-circle)",
                    color: "var(--ed-today-circle-fg)",
                  }}
                >
                  {format(day, "EEE d")}
                </span>
              ) : (
                <span
                  className="text-[13px] font-medium tracking-tight"
                  style={{ color: "var(--ed-ink-muted)" }}
                >
                  {format(day, "EEE d")}
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function HourRail() {
  return (
    <div
      className="ed-halfhour relative w-16 shrink-0"
      style={{ backgroundColor: "var(--ed-rail)" }}
    >
      {HOURS.map((h) => (
        <div
          key={h}
          className="relative flex items-start justify-end pr-2 pt-1 text-[10px] font-semibold uppercase tracking-[0.14em]"
          style={{ height: HOUR_ROW_PX, color: "var(--ed-ink-soft)" }}
        >
          {h === 0 ? "" : format(new Date().setHours(h, 0, 0, 0), "h a")}
        </div>
      ))}
    </div>
  )
}

function DayColumn({
  day,
  lanes,
  patientMap,
  onSelectSlot,
  onSelectAppointment,
}: {
  day: Date
  lanes: ReturnType<typeof assignLanes>
  patientMap: Map<string, string>
  onSelectSlot: (start: string, end: string) => void
  onSelectAppointment: (appointment: AppointmentResponse) => void
}) {
  const today = isToday(day)
  const totalHeight = HOUR_ROW_PX * 24

  const handleSlotClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button")) return
    const rect = e.currentTarget.getBoundingClientRect()
    const y = e.clientY - rect.top
    const minutesFromMidnight = Math.max(0, Math.floor((y / HOUR_ROW_PX) * 60))
    const snapped = Math.floor(minutesFromMidnight / 15) * 15
    const start = new Date(startOfDay(day).getTime() + snapped * 60_000)
    const end = new Date(start.getTime() + 50 * 60_000)
    onSelectSlot(start.toISOString(), end.toISOString())
  }

  return (
    <div
      className="relative cursor-pointer"
      style={{
        height: totalHeight,
        backgroundColor: today ? "var(--ed-today-tint)" : "transparent",
      }}
      onClick={handleSlotClick}
      aria-label={`${format(day, "EEEE MMM d")} schedule. Click to add appointment.`}
    >
      {lanes.map(({ appointment, lane, laneCount }) => {
        const startMin = minutesSinceMidnight(appointment.start_at)
        const endMin = minutesSinceMidnight(appointment.end_at)
        const top = (startMin / 60) * HOUR_ROW_PX
        const height = Math.max(((endMin - startMin) / 60) * HOUR_ROW_PX - 2, 22)
        const widthPct = 100 / laneCount
        const left = lane * widthPct
        const micro = height < 30
        const compact = height < 44
        return (
          <div
            key={appointment.id}
            className="absolute z-10 px-0.5"
            style={{
              top,
              height,
              left: `${left}%`,
              width: `calc(${widthPct}% - 2px)`,
            }}
          >
            <EditorialEventCard
              appointment={appointment}
              patientName={patientMap.get(appointment.patient_id)}
              onClick={onSelectAppointment}
              micro={micro}
              compact={compact}
            />
          </div>
        )
      })}
    </div>
  )
}

function NowLine({ days }: { days: Date[] }) {
  const todayIdx = days.findIndex((d) => isToday(d))
  if (todayIdx === -1) return null
  const now = new Date()
  const top = ((now.getHours() * 60 + now.getMinutes()) / 60) * HOUR_ROW_PX
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute left-0 right-0 z-20"
      style={{ top, height: 1, backgroundColor: "var(--ed-now-line)" }}
    >
      <div
        className="absolute h-2 w-2 -translate-y-1 rounded-full"
        style={{
          left: `calc(${(todayIdx / 7) * 100}% - 4px)`,
          backgroundColor: "var(--ed-now-line)",
        }}
      />
    </div>
  )
}
