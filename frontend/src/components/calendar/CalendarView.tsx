// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useState } from "react"
import FullCalendar from "@fullcalendar/react"
import dayGridPlugin from "@fullcalendar/daygrid"
import timeGridPlugin from "@fullcalendar/timegrid"
import interactionPlugin from "@fullcalendar/interaction"
import type { DateSelectArg, EventClickArg, EventDropArg, DatesSetArg } from "@fullcalendar/core"
import type { AppointmentResponse } from "@/types/scheduling"
import { useAppointmentList, useUpdateAppointment } from "@/hooks/useAppointments"

const STATUS_COLORS: Record<string, string> = {
  confirmed: "#3b82f6",
  cancelled: "#9ca3af",
  no_show: "#ef4444",
  completed: "#22c55e",
}

interface CalendarViewProps {
  onSelectSlot: (start: string, end: string) => void
  onSelectAppointment: (appointment: AppointmentResponse) => void
}

export function CalendarView({ onSelectSlot, onSelectAppointment }: CalendarViewProps) {
  const [dateRange, setDateRange] = useState({ start: "", end: "" })
  const { data } = useAppointmentList(dateRange.start, dateRange.end)
  const updateMutation = useUpdateAppointment()

  const events = (data?.data ?? []).map((appt) => ({
    id: appt.id,
    title: appt.title,
    start: appt.start_at,
    end: appt.end_at,
    backgroundColor: STATUS_COLORS[appt.status] ?? "#3b82f6",
    borderColor: STATUS_COLORS[appt.status] ?? "#3b82f6",
    extendedProps: appt,
  }))

  const handleDatesSet = useCallback((arg: DatesSetArg) => {
    setDateRange({
      start: arg.startStr,
      end: arg.endStr,
    })
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

  return (
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
      slotMinTime="07:00:00"
      slotMaxTime="21:00:00"
      allDaySlot={false}
      nowIndicator
      height="auto"
    />
  )
}
