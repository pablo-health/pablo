// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useState } from "react"
import FullCalendar from "@fullcalendar/react"
import dayGridPlugin from "@fullcalendar/daygrid"
import timeGridPlugin from "@fullcalendar/timegrid"
import interactionPlugin from "@fullcalendar/interaction"
import type { DateSelectArg, EventClickArg, EventDropArg, DatesSetArg, EventContentArg } from "@fullcalendar/core"
import type { AppointmentResponse } from "@/types/scheduling"
import { useAppointmentList, useUpdateAppointment } from "@/hooks/useAppointments"
import { User, Users } from "lucide-react"

const STATUS_COLORS: Record<string, { bg: string; text: string }> = {
  confirmed: { bg: "#C2D9E5", text: "#3D5F71" },
  completed: { bg: "#C2DBC6", text: "#3D5640" },
  cancelled: { bg: "#E8D5B5", text: "#553F33" },
  no_show: { bg: "#FECACA", text: "#991B1B" },
}

interface CalendarViewProps {
  onSelectSlot: (start: string, end: string) => void
  onSelectAppointment: (appointment: AppointmentResponse) => void
  workingHoursStart?: number
  workingHoursEnd?: number
}

function toSlotTime(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00:00`
}

export function CalendarView({
  onSelectSlot,
  onSelectAppointment,
  workingHoursStart = 8,
  workingHoursEnd = 18,
}: CalendarViewProps) {
  const [dateRange, setDateRange] = useState({ start: "", end: "" })
  const [dateRangeText, setDateRangeText] = useState("")
  const { data } = useAppointmentList(dateRange.start, dateRange.end)
  const updateMutation = useUpdateAppointment()

  const events = (data?.data ?? []).map((appt) => {
    const colors = STATUS_COLORS[appt.status] ?? STATUS_COLORS.confirmed
    return {
      id: appt.id,
      title: appt.title,
      start: appt.start_at,
      end: appt.end_at,
      backgroundColor: colors.bg,
      borderColor: colors.bg,
      textColor: colors.text,
      extendedProps: appt,
    }
  })

  const handleDatesSet = useCallback((arg: DatesSetArg) => {
    setDateRange({
      start: arg.startStr,
      end: arg.endStr,
    })
    const start = arg.start.toLocaleDateString("en-US", { month: "long", day: "numeric" })
    const end = arg.end.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })
    setDateRangeText(`Showing ${start} to ${end}`)
  }, [])

  const handleSelect = useCallback(
    (arg: DateSelectArg) => {
      onSelectSlot(arg.startStr, arg.endStr)
    },
    [onSelectSlot]
  )

  const handleEventClick = useCallback(
    (arg: EventClickArg) => {
      const appt = arg.event.extendedProps as AppointmentResponse
      onSelectAppointment(appt)
    },
    [onSelectAppointment]
  )

  const handleEventDrop = useCallback(
    (arg: EventDropArg) => {
      const appt = arg.event.extendedProps as AppointmentResponse
      updateMutation.mutate({
        appointmentId: appt.id,
        data: {
          start_at: arg.event.startStr,
          end_at: arg.event.endStr,
        },
      })
    },
    [updateMutation]
  )

  const renderEventContent = useCallback((arg: EventContentArg) => {
    const appt = arg.event.extendedProps as AppointmentResponse
    const isGroup = appt.session_type === "group" || appt.session_type === "couples"
    const timeText = arg.timeText

    return (
      <div className="flex items-start gap-1.5 px-1.5 py-1 overflow-hidden w-full">
        {isGroup
          ? <Users className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          : <User className="h-3.5 w-3.5 mt-0.5 shrink-0" />
        }
        <div className="min-w-0">
          <div className="font-medium text-xs leading-tight truncate">{arg.event.title}</div>
          {timeText && <div className="text-[10px] opacity-75 leading-tight">{timeText}</div>}
        </div>
      </div>
    )
  }, [])

  return (
    <>
    <div className="sr-only" aria-live="polite" role="status">
      {dateRangeText}
    </div>
    <FullCalendar
      plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
      initialView="timeGridWeek"
      headerToolbar={{
        left: "prev,next today",
        center: "title",
        right: "timeGridWeek,timeGridDay",
      }}
      selectable
      editable
      events={events}
      datesSet={handleDatesSet}
      select={handleSelect}
      eventClick={handleEventClick}
      eventDrop={handleEventDrop}
      eventContent={renderEventContent}
      slotMinTime={toSlotTime(Math.max(0, workingHoursStart - 1))}
      slotMaxTime={toSlotTime(Math.min(24, workingHoursEnd + 1))}
      scrollTime={toSlotTime(workingHoursStart)}
      businessHours={{
        daysOfWeek: [1, 2, 3, 4, 5],
        startTime: toSlotTime(workingHoursStart),
        endTime: toSlotTime(workingHoursEnd),
      }}
      allDaySlot={false}
      nowIndicator
      height="auto"
    />
    </>
  )
}
