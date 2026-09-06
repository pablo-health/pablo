// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useMemo, useRef } from "react"
import { format, isSameDay, isToday, startOfDay } from "date-fns"
import type { AppointmentResponse } from "@/types/scheduling"
import type { AvailabilityRule, FreeSlotsResponse } from "@/types/availability"
import { summarize } from "@/components/settings/AvailabilitySettings"
import {
  EditorialEventCard,
  EVENT_COMPACT_PX,
  EVENT_MICRO_PX,
} from "./EditorialEventCard"
import { EditorialEventWrapper } from "./EditorialEventWrapper"
import { UnavailableLayer } from "./UnavailableLayer"
import { assignLanes } from "./laneLayout"
import { rulesInForceForDate } from "./unavailability"
import {
  DAY_END_HOUR,
  DAY_START_HOUR,
  HOUR_ROW_PX,
  gridHours,
  minutesSinceMidnight,
} from "./dateUtils"

interface EditorialDayViewProps {
  anchor: Date
  appointments: AppointmentResponse[]
  patientMap: Map<string, string>
  /** All of the therapist's availability rules — used only to attribute a
   * fully-blocked day and to list what's in force for the tooltip; the
   * shading itself comes from `freeSlots`, not from these. */
  availabilityRules: AvailabilityRule[]
  /** Free slots for `anchor`, at whatever duration the caller queried with.
   * Undefined while loading — renders no shading until it resolves, rather
   * than flashing "fully unavailable". */
  freeSlots?: FreeSlotsResponse
  onSelectSlot: (start: string) => void
  /** Single click on an event → open the peek popover anchored to its rect. */
  onPeek: (appointment: AppointmentResponse, anchorRect: DOMRect) => void
  /** Double click on an event → open the edit flow. */
  onEdit: (appointment: AppointmentResponse) => void
  /** Drag-to-reschedule → preserve duration, shift start (vertical only). */
  onMove: (appointment: AppointmentResponse, newStartIso: string) => void
  /** Right click on an event → open the status menu at the cursor. */
  onContextMenu: (appointment: AppointmentResponse, x: number, y: number) => void
  scrollToHour?: number
  /** Working-hours window. Pass 0/24 to render the full day. */
  dayStart?: number
  dayEnd?: number
  /** Height of one hour row in px, from the active density preset. */
  rowHeightPx?: number
}

export function EditorialDayView({
  anchor,
  appointments,
  patientMap,
  availabilityRules,
  freeSlots,
  onSelectSlot,
  onPeek,
  onEdit,
  onMove,
  onContextMenu,
  scrollToHour = 8,
  dayStart = DAY_START_HOUR,
  dayEnd = DAY_END_HOUR,
  rowHeightPx = HOUR_ROW_PX,
}: EditorialDayViewProps) {
  const scrollerRef = useRef<HTMLDivElement>(null)
  const today = isToday(anchor)
  const hours = useMemo(() => gridHours(dayStart, dayEnd), [dayStart, dayEnd])
  const startOffsetMin = dayStart * 60
  const totalHeight = rowHeightPx * (dayEnd - dayStart)

  useEffect(() => {
    if (scrollerRef.current) {
      scrollerRef.current.scrollTop = Math.max(scrollToHour - dayStart, 0) * rowHeightPx
    }
  }, [scrollToHour, dayStart, rowHeightPx])

  const lanes = useMemo(() => {
    const dayAppts = appointments.filter((a) => isSameDay(new Date(a.start_at), anchor))
    return assignLanes(dayAppts)
  }, [appointments, anchor])

  // `configured === false` means the therapist has no rules at all — never
  // shade an unconfigured calendar as unavailable.
  const showUnavailable = freeSlots?.configured === true
  const inForceLabel = useMemo(
    () =>
      showUnavailable
        ? rulesInForceForDate(availabilityRules, anchor)
            .map(summarize)
            .filter(Boolean)
            .join(" · ")
        : "",
    [showUnavailable, availabilityRules, anchor],
  )

  const handleSlotClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button")) return
    const rect = e.currentTarget.getBoundingClientRect()
    const y = e.clientY - rect.top
    const minutes = Math.max(0, Math.floor((y / rowHeightPx) * 60) + startOffsetMin)
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
            className="ed-halfhour relative w-[72px] shrink-0"
            style={{ backgroundColor: "var(--ed-rail)" }}
          >
            {hours.map((h, i) => (
              <div
                key={h}
                className="flex items-start justify-end pr-3 pt-1 text-[10px] font-semibold uppercase tracking-[0.12em]"
                style={{ height: rowHeightPx, color: "var(--ed-ink-soft)" }}
              >
                {i === 0 ? "" : format(new Date().setHours(h, 0, 0, 0), "h a")}
              </div>
            ))}
          </div>

          <div
            data-daycanvas="1"
            className="ed-hourlines relative flex-1 cursor-pointer"
            style={{
              height: totalHeight,
              backgroundColor: today ? "var(--ed-today-tint)" : "transparent",
            }}
            onClick={handleSlotClick}
            aria-label={`${format(anchor, "EEEE MMM d")} schedule. Click to add appointment.`}
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
                    mode: "day",
                    rowHeightPx,
                    gridSelector: "[data-daycanvas]",
                    onMove,
                  }}
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
                    patientName={appointment.patient_name ?? patientMap.get(appointment.patient_id)}
                    micro={micro}
                    compact={compact}
                  />
                </EditorialEventWrapper>
              )
            })}
            {today && <DayNowLine dayStart={dayStart} dayEnd={dayEnd} rowHeightPx={rowHeightPx} />}
          </div>
        </div>
      </div>
    </div>
  )
}

function DayNowLine({
  dayStart,
  dayEnd,
  rowHeightPx,
}: {
  dayStart: number
  dayEnd: number
  rowHeightPx: number
}) {
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
        className="absolute -left-1 -top-1 h-2 w-2 rounded-full"
        style={{ backgroundColor: "var(--ed-now-line)" }}
      />
    </div>
  )
}
