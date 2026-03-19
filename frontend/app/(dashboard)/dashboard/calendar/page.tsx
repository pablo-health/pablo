// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useState } from "react"
import { CalendarView } from "@/components/calendar/CalendarView"
import { AppointmentModal } from "@/components/calendar/AppointmentModal"
import type { AppointmentResponse } from "@/types/scheduling"

export default function CalendarPage() {
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
        <h1 className="text-2xl font-display font-semibold text-neutral-900">Calendar</h1>
        <button
          onClick={() => handleSelectSlot(new Date().toISOString(), "")}
          className="btn-primary px-4 py-2 rounded-lg text-sm font-medium"
        >
          New Appointment
        </button>
      </div>

      <div className="card p-4">
        <CalendarView
          onSelectSlot={handleSelectSlot}
          onSelectAppointment={handleSelectAppointment}
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
