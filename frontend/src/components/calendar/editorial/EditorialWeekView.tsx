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
import { EditorialEventWrapper } from "./EditorialEventWrapper"
import { assignLanes } from "./laneLayout"
import {
  DAY_END_HOUR,
  DAY_START_HOUR,
  HOUR_ROW_PX,
  gridHours,
  minutesSinceMidnight,
  weekDays,
} from "./dateUtils"

interface EditorialWeekViewProps {
  anchor: Date
  appointments: AppointmentResponse[]
  patientMap: Map<string, string>
  onSelectSlot: (start: string) => void
  /** Single click on an event → open the peek popover anchored to its rect. */
  onPeek: (appointment: AppointmentResponse, anchorRect: DOMRect) => void
  /** Double click on an event → open the edit flow. */
  onEdit: (appointment: AppointmentResponse) => void
  /** Drag-to-reschedule → preserve duration, shift start (and day). */
  onMove: (appointment: AppointmentResponse, newStartIso: string) => void
  /** Right click on an event → open the status menu at the cursor. */
  onContextMenu: (appointment: AppointmentResponse, x: number, y: number) => void
  /** Hour to scroll to on mount / day change (defaults to 8). */
  scrollToHour?: number
  /** Working-hours window. Pass 0/24 to render the full day. */
  dayStart?: number
  dayEnd?: number
}

export function EditorialWeekView({
  anchor,
  appointments,
  patientMap,
  onSelectSlot,
  onPeek,
  onEdit,
  onMove,
  onContextMenu,
  scrollToHour = 8,
  dayStart = DAY_START_HOUR,
  dayEnd = DAY_END_HOUR,
}: EditorialWeekViewProps) {
  const days = useMemo(() => weekDays(anchor), [anchor])
  const hours = useMemo(() => gridHours(dayStart, dayEnd), [dayStart, dayEnd])
  const scrollerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollerRef.current) {
      scrollerRef.current.scrollTop = Math.max(scrollToHour - dayStart, 0) * HOUR_ROW_PX
    }
  }, [scrollToHour, dayStart])

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
          <HourRail hours={hours} />
          <div
            data-weekgrid="1"
            className="ed-daycols ed-hourlines relative grid flex-1"
            style={{ gridTemplateColumns: "repeat(7, minmax(0, 1fr))" }}
          >
            {days.map((day, idx) => (
              <DayColumn
                key={day.toISOString()}
                day={day}
                dayIndex={idx}
                lanes={dayBuckets[idx]}
                patientMap={patientMap}
                onSelectSlot={onSelectSlot}
                onPeek={onPeek}
                onEdit={onEdit}
                onMove={onMove}
                onContextMenu={onContextMenu}
                dayStart={dayStart}
                dayEnd={dayEnd}
              />
            ))}
            <NowLine days={days} dayStart={dayStart} dayEnd={dayEnd} />
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
      <div className="w-[60px] shrink-0" aria-hidden />
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

function HourRail({ hours }: { hours: number[] }) {
  return (
    <div
      className="ed-halfhour relative w-[60px] shrink-0"
      style={{ backgroundColor: "var(--ed-rail)" }}
    >
      {hours.map((h, i) => (
        <div
          key={h}
          className="relative flex items-start justify-end pr-2 pt-1 text-[10px] font-semibold uppercase tracking-[0.12em]"
          style={{ height: HOUR_ROW_PX, color: "var(--ed-ink-soft)" }}
        >
          {i === 0 ? "" : format(new Date().setHours(h, 0, 0, 0), "h a")}
        </div>
      ))}
    </div>
  )
}

function DayColumn({
  day,
  dayIndex,
  lanes,
  patientMap,
  onSelectSlot,
  onPeek,
  onEdit,
  onMove,
  onContextMenu,
  dayStart,
  dayEnd,
}: {
  day: Date
  /** 0-based index of this column within the visible 7-day week (used for
   * drag clamping so horizontal drags can't land outside the grid). */
  dayIndex: number
  lanes: ReturnType<typeof assignLanes>
  patientMap: Map<string, string>
  onSelectSlot: (start: string) => void
  onPeek: (appointment: AppointmentResponse, anchorRect: DOMRect) => void
  onEdit: (appointment: AppointmentResponse) => void
  onMove: (appointment: AppointmentResponse, newStartIso: string) => void
  onContextMenu: (appointment: AppointmentResponse, x: number, y: number) => void
  dayStart: number
  dayEnd: number
}) {
  const today = isToday(day)
  const totalHeight = HOUR_ROW_PX * (dayEnd - dayStart)
  const startOffsetMin = dayStart * 60

  const handleSlotClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button")) return
    const rect = e.currentTarget.getBoundingClientRect()
    const y = e.clientY - rect.top
    const minutesFromMidnight = Math.max(0, Math.floor((y / HOUR_ROW_PX) * 60) + startOffsetMin)
    const snapped = Math.floor(minutesFromMidnight / 15) * 15
    const start = new Date(startOfDay(day).getTime() + snapped * 60_000)
    onSelectSlot(start.toISOString())
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
        // With a dynamic window the grid always contains every appointment,
        // so no clamping is needed — render at the true position.
        const top = ((startMin - startOffsetMin) / 60) * HOUR_ROW_PX
        const height = Math.max(((endMin - startMin) / 60) * HOUR_ROW_PX - 2, 20)
        const widthPct = 100 / laneCount
        const left = lane * widthPct
        const micro = height < EVENT_MICRO_PX
        const compact = height < EVENT_COMPACT_PX
        return (
          <EditorialEventWrapper
            key={appointment.id}
            appointment={appointment}
            onPeek={onPeek}
            onEdit={onEdit}
            onContextMenu={onContextMenu}
            drag={{
              mode: "week",
              rowHeightPx: HOUR_ROW_PX,
              gridSelector: "[data-weekgrid]",
              sourceDayIndex: dayIndex,
              onMove,
            }}
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
              micro={micro}
              compact={compact}
            />
          </EditorialEventWrapper>
        )
      })}
    </div>
  )
}

function NowLine({
  days,
  dayStart,
  dayEnd,
}: {
  days: Date[]
  dayStart: number
  dayEnd: number
}) {
  const todayIdx = days.findIndex((d) => isToday(d))
  if (todayIdx === -1) return null
  const now = new Date()
  const nowMin = now.getHours() * 60 + now.getMinutes()
  if (nowMin < dayStart * 60 || nowMin > dayEnd * 60) return null
  const top = ((nowMin - dayStart * 60) / 60) * HOUR_ROW_PX
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
