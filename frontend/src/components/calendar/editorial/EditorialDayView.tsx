// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useMemo, useRef } from "react"
import { format, isSameDay, isToday, startOfDay } from "date-fns"
import type { AppointmentResponse } from "@/types/scheduling"
import {
  EditorialEventCard,
  EVENT_COMPACT_PX,
  EVENT_MICRO_PX,
} from "./EditorialEventCard"
import { assignLanes } from "./laneLayout"
import { HOUR_ROW_PX, minutesSinceMidnight } from "./dateUtils"

interface EditorialDayViewProps {
  anchor: Date
  appointments: AppointmentResponse[]
  patientMap: Map<string, string>
  onSelectSlot: (start: string) => void
  onSelectAppointment: (appointment: AppointmentResponse) => void
  scrollToHour?: number
}

const HOURS = Array.from({ length: 24 }, (_, i) => i)

export function EditorialDayView({
  anchor,
  appointments,
  patientMap,
  onSelectSlot,
  onSelectAppointment,
  scrollToHour = 8,
}: EditorialDayViewProps) {
  const scrollerRef = useRef<HTMLDivElement>(null)
  const today = isToday(anchor)

  useEffect(() => {
    if (scrollerRef.current) scrollerRef.current.scrollTop = scrollToHour * HOUR_ROW_PX
  }, [scrollToHour])

  const lanes = useMemo(() => {
    const dayAppts = appointments.filter((a) => isSameDay(new Date(a.start_at), anchor))
    return assignLanes(dayAppts)
  }, [appointments, anchor])

  const handleSlotClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button")) return
    const rect = e.currentTarget.getBoundingClientRect()
    const y = e.clientY - rect.top
    const minutes = Math.max(0, Math.floor((y / HOUR_ROW_PX) * 60))
    const snapped = Math.floor(minutes / 15) * 15
    const start = new Date(startOfDay(anchor).getTime() + snapped * 60_000)
    onSelectSlot(start.toISOString())
  }

  return (
    <div
      className="ed-fade-in flex flex-col overflow-hidden rounded-xl"
      style={{
        backgroundColor: "var(--ed-canvas-elev)",
        boxShadow: "var(--ed-shadow-card)",
      }}
    >
      <div ref={scrollerRef} className="relative max-h-[68vh] overflow-y-auto">
        <div className="flex">
          <div
            className="ed-halfhour relative w-20 shrink-0"
            style={{ backgroundColor: "var(--ed-rail)" }}
          >
            {HOURS.map((h) => (
              <div
                key={h}
                className="flex items-start justify-end pr-3 pt-1 text-[11px] font-semibold uppercase tracking-[0.14em]"
                style={{ height: HOUR_ROW_PX, color: "var(--ed-ink-soft)" }}
              >
                {h === 0 ? "" : format(new Date().setHours(h, 0, 0, 0), "h a")}
              </div>
            ))}
          </div>

          <div
            className="ed-hourlines relative flex-1 cursor-pointer"
            style={{
              height: HOUR_ROW_PX * 24,
              backgroundColor: today ? "var(--ed-today-tint)" : "transparent",
            }}
            onClick={handleSlotClick}
            aria-label={`${format(anchor, "EEEE MMM d")} schedule. Click to add appointment.`}
          >
            {lanes.map(({ appointment, lane, laneCount }) => {
              const startMin = minutesSinceMidnight(appointment.start_at)
              const endMin = minutesSinceMidnight(appointment.end_at)
              const top = (startMin / 60) * HOUR_ROW_PX
              const height = Math.max(((endMin - startMin) / 60) * HOUR_ROW_PX - 2, 26)
              const widthPct = 100 / laneCount
              const left = lane * widthPct
              const micro = height < EVENT_MICRO_PX
              const compact = height < EVENT_COMPACT_PX
              return (
                <div
                  key={appointment.id}
                  className="absolute z-10 px-1"
                  style={{
                    top,
                    height,
                    left: `calc(${left}% + 16px)`,
                    width: `calc(${widthPct}% - 32px)`,
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
            {today && <DayNowLine />}
          </div>
        </div>
      </div>
    </div>
  )
}

function DayNowLine() {
  const now = new Date()
  const top = ((now.getHours() * 60 + now.getMinutes()) / 60) * HOUR_ROW_PX
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute left-0 right-0 z-20"
      style={{ top, height: 1, backgroundColor: "var(--ed-now-line)" }}
    >
      <div
        className="absolute -left-1 -top-1 h-2 w-2 rounded-full"
        style={{ backgroundColor: "var(--ed-now-line)" }}
      />
    </div>
  )
}
