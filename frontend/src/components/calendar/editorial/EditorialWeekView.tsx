// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useMemo, useRef } from "react"
import { format, isSameDay, isToday, startOfDay } from "date-fns"
import { useQueries } from "@tanstack/react-query"
import type { AppointmentResponse } from "@/types/scheduling"
import type { AvailabilityRule, FreeSlotsResponse } from "@/types/availability"
import { getFreeSlots } from "@/lib/api/availability"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuth } from "@/lib/auth-context"
import { summarize } from "@/components/settings/AvailabilitySettings"
import {
  EditorialEventCard,
  EVENT_COMPACT_PX,
  EVENT_MICRO_PX,
} from "./EditorialEventCard"
import { EditorialEventWrapper } from "./EditorialEventWrapper"
import { UnavailableLayer } from "./UnavailableLayer"
import { assignLanes } from "./laneLayout"
import { matchWholeDayBlockRule, rulesInForceForDate } from "./unavailability"
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
  /** All of the therapist's availability rules — used only to attribute a
   * fully-blocked day and to list what's in force for each day's tooltip;
   * the shading itself comes from the per-day free-slots fetch below. */
  availabilityRules: AvailabilityRule[]
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
  /** Height of one hour row in px, from the active density preset. */
  rowHeightPx?: number
}

export function EditorialWeekView({
  anchor,
  appointments,
  patientMap,
  availabilityRules,
  onSelectSlot,
  onPeek,
  onEdit,
  onMove,
  onContextMenu,
  scrollToHour = 8,
  dayStart = DAY_START_HOUR,
  dayEnd = DAY_END_HOUR,
  rowHeightPx = HOUR_ROW_PX,
}: EditorialWeekViewProps) {
  const days = useMemo(() => weekDays(anchor), [anchor])
  const hours = useMemo(() => gridHours(dayStart, dayEnd), [dayStart, dayEnd])
  const scrollerRef = useRef<HTMLDivElement>(null)

  // One free-slots query per visible day (a fixed 7, so this is a stable
  // number/order of hook calls across renders — see unavailability.ts for
  // why shading needs the real per-day response rather than re-deriving it
  // from `availabilityRules`).
  const { loading: authLoading } = useAuth()
  const dateStrs = useMemo(() => days.map((d) => format(d, "yyyy-MM-dd")), [days])
  const freeSlotsQueries = useQueries({
    queries: dateStrs.map((dateStr) => ({
      queryKey: queryKeys.availability.slots(dateStr, undefined),
      queryFn: () => getFreeSlots(dateStr),
      staleTime: 30 * 1000,
      enabled: !authLoading,
    })),
  })
  const freeSlotsByDay = useMemo(
    () => freeSlotsQueries.map((q) => q.data),
    [freeSlotsQueries],
  )

  useEffect(() => {
    if (scrollerRef.current) {
      scrollerRef.current.scrollTop = Math.max(scrollToHour - dayStart, 0) * rowHeightPx
    }
  }, [scrollToHour, dayStart, rowHeightPx])

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
      <DayHeaderRow
        days={days}
        availabilityRules={availabilityRules}
        freeSlotsByDay={freeSlotsByDay}
      />
      <div ref={scrollerRef} className="relative max-h-[68vh] overflow-y-auto">
        <div className="flex">
          <HourRail hours={hours} rowHeightPx={rowHeightPx} />
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
                availabilityRules={availabilityRules}
                freeSlots={freeSlotsByDay[idx]}
                onSelectSlot={onSelectSlot}
                onPeek={onPeek}
                onEdit={onEdit}
                onMove={onMove}
                onContextMenu={onContextMenu}
                dayStart={dayStart}
                dayEnd={dayEnd}
                rowHeightPx={rowHeightPx}
              />
            ))}
            <NowLine days={days} dayStart={dayStart} dayEnd={dayEnd} rowHeightPx={rowHeightPx} />
          </div>
        </div>
      </div>
    </div>
  )
}

function DayHeaderRow({
  days,
  availabilityRules,
  freeSlotsByDay,
}: {
  days: Date[]
  availabilityRules: AvailabilityRule[]
  freeSlotsByDay: (FreeSlotsResponse | undefined)[]
}) {
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
        {days.map((day, idx) => {
          const today = isToday(day)
          // `configured === false` (no rules at all) must never render a
          // blocked label — only a real whole-day-blocking rule does.
          const configured = freeSlotsByDay[idx]?.configured === true
          const blockRule = configured
            ? matchWholeDayBlockRule(availabilityRules, day)
            : undefined
          return (
            <div
              key={day.toISOString()}
              className="flex flex-col items-center justify-center gap-1 py-3"
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
              {blockRule && (
                <span
                  className="rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wide"
                  style={{
                    backgroundColor: "var(--ed-hairline-strong)",
                    color: "var(--ed-ink-muted)",
                  }}
                >
                  {summarize(blockRule)}
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function HourRail({ hours, rowHeightPx }: { hours: number[]; rowHeightPx: number }) {
  return (
    <div
      className="ed-halfhour relative w-[60px] shrink-0"
      style={{ backgroundColor: "var(--ed-rail)" }}
    >
      {hours.map((h, i) => (
        <div
          key={h}
          className="relative flex items-start justify-end pr-2 pt-1 text-[10px] font-semibold uppercase tracking-[0.12em]"
          style={{ height: rowHeightPx, color: "var(--ed-ink-soft)" }}
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
  availabilityRules,
  freeSlots,
  onSelectSlot,
  onPeek,
  onEdit,
  onMove,
  onContextMenu,
  dayStart,
  dayEnd,
  rowHeightPx,
}: {
  day: Date
  /** 0-based index of this column within the visible 7-day week (used for
   * drag clamping so horizontal drags can't land outside the grid). */
  dayIndex: number
  lanes: ReturnType<typeof assignLanes>
  patientMap: Map<string, string>
  /** All of the therapist's availability rules — used only for the
   * in-force tooltip; shading comes from `freeSlots`. */
  availabilityRules: AvailabilityRule[]
  /** This day's free slots. Undefined while loading — renders no shading
   * until it resolves. */
  freeSlots?: FreeSlotsResponse
  onSelectSlot: (start: string) => void
  onPeek: (appointment: AppointmentResponse, anchorRect: DOMRect) => void
  onEdit: (appointment: AppointmentResponse) => void
  onMove: (appointment: AppointmentResponse, newStartIso: string) => void
  onContextMenu: (appointment: AppointmentResponse, x: number, y: number) => void
  dayStart: number
  dayEnd: number
  rowHeightPx: number
}) {
  const today = isToday(day)
  const totalHeight = rowHeightPx * (dayEnd - dayStart)
  const startOffsetMin = dayStart * 60

  const handleSlotClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button")) return
    const rect = e.currentTarget.getBoundingClientRect()
    const y = e.clientY - rect.top
    const minutesFromMidnight = Math.max(0, Math.floor((y / rowHeightPx) * 60) + startOffsetMin)
    const snapped = Math.floor(minutesFromMidnight / 15) * 15
    const start = new Date(startOfDay(day).getTime() + snapped * 60_000)
    onSelectSlot(start.toISOString())
  }

  // `configured === false` means the therapist has no rules at all — never
  // shade an unconfigured calendar as unavailable.
  const showUnavailable = freeSlots?.configured === true
  const inForceLabel = showUnavailable
    ? rulesInForceForDate(availabilityRules, day).map(summarize).filter(Boolean).join(" · ")
    : ""

  return (
    <div
      className="relative cursor-pointer"
      style={{
        height: totalHeight,
        backgroundColor: today ? "var(--ed-today-tint)" : "transparent",
      }}
      onClick={handleSlotClick}
      aria-label={`${format(day, "EEEE MMM d")} schedule. Click to add appointment.`}
      title={inForceLabel || undefined}
    >
      {freeSlots && freeSlots.configured && (
        <UnavailableLayer
          slots={freeSlots.slots}
          dayStartHour={dayStart}
          dayEndHour={dayEnd}
          rowHeightPx={rowHeightPx}
        />
      )}
      {lanes.map(({ appointment, lane, laneCount }) => {
        const startMin = minutesSinceMidnight(appointment.start_at)
        const endMin = minutesSinceMidnight(appointment.end_at)
        // With a dynamic window the grid always contains every appointment,
        // so no clamping is needed — render at the true position.
        const top = ((startMin - startOffsetMin) / 60) * rowHeightPx
        const height = Math.max(((endMin - startMin) / 60) * rowHeightPx - 2, 20)
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
              rowHeightPx,
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
              patientName={appointment.patient_name ?? patientMap.get(appointment.patient_id)}
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
  rowHeightPx,
}: {
  days: Date[]
  dayStart: number
  dayEnd: number
  rowHeightPx: number
}) {
  const todayIdx = days.findIndex((d) => isToday(d))
  if (todayIdx === -1) return null
  const now = new Date()
  const nowMin = now.getHours() * 60 + now.getMinutes()
  if (nowMin < dayStart * 60 || nowMin > dayEnd * 60) return null
  const top = ((nowMin - dayStart * 60) / 60) * rowHeightPx
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
