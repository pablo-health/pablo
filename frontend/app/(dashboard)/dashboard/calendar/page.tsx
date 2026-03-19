// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useState } from "react"
import { Plus, Calendar as CalendarIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { CalendarView } from "@/components/calendar/CalendarView"
import { StatusLegend } from "@/components/calendar/StatusLegend"
import { AppointmentModal } from "@/components/calendar/AppointmentModal"
import { usePreferences } from "@/hooks/usePreferences"
import type { AppointmentResponse } from "@/types/scheduling"

export default function CalendarPage() {
  const { data: preferences } = usePreferences()
  const [modalOpen, setModalOpen] = useState(false)
  const [selectedAppointment, setSelectedAppointment] = useState<AppointmentResponse | null>(null)
  const [defaultStart, setDefaultStart] = useState<string>()
  const [defaultEnd, setDefaultEnd] = useState<string>()

  const handleSelectSlot = useCallback((start: string, end: string) => {
    setSelectedAppointment(null)
    setDefaultStart(start)
    setDefaultEnd(end)
    setModalOpen(true)
  }, [])

  const handleSelectAppointment = useCallback((appointment: AppointmentResponse) => {
    setSelectedAppointment(appointment)
    setDefaultStart(undefined)
    setDefaultEnd(undefined)
    setModalOpen(true)
  }, [])

  const handleClose = useCallback(() => {
    setModalOpen(false)
    setSelectedAppointment(null)
    setDefaultStart(undefined)
    setDefaultEnd(undefined)
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-semibold text-neutral-900">Calendar</h1>
          <p className="text-sm text-neutral-600 mt-1">Schedule and manage appointments</p>
        </div>
        <Button onClick={() => handleSelectSlot(new Date().toISOString(), "")}>
          <Plus className="h-4 w-4" />
          New Appointment
        </Button>
      </div>

      <StatusLegend />

      <div className="card p-4" aria-label="Weekly appointment calendar">
        <CalendarView
          onSelectSlot={handleSelectSlot}
          onSelectAppointment={handleSelectAppointment}
          workingHoursStart={preferences?.working_hours_start}
          workingHoursEnd={preferences?.working_hours_end}
        />
      </div>

      <AppointmentModal
        open={modalOpen}
        onClose={handleClose}
        defaultStart={defaultStart}
        defaultEnd={defaultEnd}
        appointment={selectedAppointment}
      />
    </div>
  )
}
