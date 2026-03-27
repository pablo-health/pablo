// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { CalendarView } from "@/components/calendar/CalendarView"
import { StatusLegend } from "@/components/calendar/StatusLegend"
import { AppointmentModal } from "@/components/calendar/AppointmentModal"
import { usePreferences, useSavePreferences } from "@/hooks/usePreferences"
import {
  getICalSyncStatus,
  triggerICalSync,
  type ICalConnectionStatus,
  type ICalSyncResponse,
} from "@/lib/api/scheduling"
import { Loader2, RefreshCw } from "lucide-react"
import type { AppointmentResponse } from "@/types/scheduling"

export default function CalendarPage() {
  const { data: preferences } = usePreferences()
  const saveMutation = useSavePreferences()
  const lastSavedView = useRef(preferences?.calendar_default_view)
  const [modalOpen, setModalOpen] = useState(false)
  const [selectedAppointment, setSelectedAppointment] = useState<AppointmentResponse | null>(null)
  const [defaultStart, setDefaultStart] = useState<string>()
  const [defaultEnd, setDefaultEnd] = useState<string>()
  const [syncing, setSyncing] = useState(false)
  const [syncStatus, setSyncStatus] = useState<ICalConnectionStatus[]>([])
  const [syncResult, setSyncResult] = useState<string | null>(null)

  useEffect(() => {
    getICalSyncStatus()
      .then((s) => setSyncStatus(s.connections))
      .catch(() => {})
  }, [])

  const handleSync = useCallback(async () => {
    setSyncing(true)
    setSyncResult(null)
    try {
      const results = await triggerICalSync()
      const totals = results.reduce(
        (acc: { created: number; updated: number }, r: ICalSyncResponse) => ({
          created: acc.created + r.created,
          updated: acc.updated + r.updated,
        }),
        { created: 0, updated: 0 }
      )
      setSyncResult(
        totals.created || totals.updated
          ? `${totals.created} new, ${totals.updated} updated`
          : "Up to date"
      )
      // Refresh status
      const s = await getICalSyncStatus()
      setSyncStatus(s.connections)
      // Clear result after 5 seconds
      setTimeout(() => setSyncResult(null), 5000)
    } catch {
      setSyncResult("Sync failed")
    } finally {
      setSyncing(false)
    }
  }, [])

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

  const handleCreateNew = useCallback(() => {
    handleSelectSlot(new Date().toISOString(), "")
  }, [handleSelectSlot])

  const handleViewChange = useCallback(
    (view: string) => {
      if (!preferences || view === lastSavedView.current) return
      lastSavedView.current = view
      saveMutation.mutate({ ...preferences, calendar_default_view: view })
    },
    [preferences, saveMutation]
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-semibold text-neutral-900">Calendar</h1>
          <p className="text-sm text-neutral-600 mt-1">Schedule and manage appointments</p>
        </div>
        {syncStatus.length > 0 && (
          <div className="flex items-center gap-3">
            {syncResult && (
              <span className="text-sm text-neutral-500">{syncResult}</span>
            )}
            {!syncResult && syncStatus[0]?.last_synced_at && (
              <span className="text-xs text-neutral-400">
                Synced {new Date(syncStatus[0].last_synced_at).toLocaleTimeString()}
              </span>
            )}
            <button
              onClick={handleSync}
              disabled={syncing}
              className="inline-flex items-center gap-1.5 rounded-md border border-neutral-200 px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-50 disabled:opacity-50"
              aria-label="Sync calendar"
            >
              {syncing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              Sync
            </button>
          </div>
        )}
      </div>

      <StatusLegend />

      <div className="card p-4" aria-label="Weekly appointment calendar">
        <CalendarView
          onSelectSlot={handleSelectSlot}
          onSelectAppointment={handleSelectAppointment}
          onCreateNew={handleCreateNew}
          workingHoursStart={preferences?.working_hours_start}
          workingHoursEnd={preferences?.working_hours_end}
          defaultView={preferences?.calendar_default_view}
          onViewChange={handleViewChange}
        />
      </div>

      <AppointmentModal
        open={modalOpen}
        onClose={handleClose}
        defaultStart={defaultStart}
        defaultEnd={defaultEnd}
        appointment={selectedAppointment}
        preferences={preferences}
      />
    </div>
  )
}
