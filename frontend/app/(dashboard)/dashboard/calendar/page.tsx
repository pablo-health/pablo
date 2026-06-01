// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { AppointmentModal } from "@/components/calendar/AppointmentModal"
import {
  EditorialCalendar,
  type EditorialView,
} from "@/components/calendar/editorial"
import { useTheme } from "@/components/theme/ThemeProvider"
import { usePreferences, useSavePreferences } from "@/hooks/usePreferences"
import {
  getICalSyncStatus,
  triggerICalSync,
  type ICalConnectionStatus,
  type ICalSyncResponse,
} from "@/lib/api/scheduling"
import { useAuth } from "@/lib/auth-context"
import { Loader2, RefreshCw } from "lucide-react"
import type { AppointmentResponse } from "@/types/scheduling"

function toEditorialView(raw: string | undefined): EditorialView | undefined {
  if (raw === "timeGridDay") return "day"
  if (raw === "timeGridWeek") return "week"
  if (raw === "dayGridMonth") return "month"
  return undefined
}

function fromEditorialView(v: EditorialView): string {
  return v === "day" ? "timeGridDay" : v === "week" ? "timeGridWeek" : "dayGridMonth"
}

export default function CalendarPage() {
  const { loading: authLoading } = useAuth()
  const { theme } = useTheme()
  const { data: preferences } = usePreferences()
  const saveMutation = useSavePreferences()
  const lastSavedView = useRef<string | undefined>(undefined)
  const [modalOpen, setModalOpen] = useState(false)
  const [selectedAppointment, setSelectedAppointment] = useState<AppointmentResponse | null>(null)
  const [defaultStart, setDefaultStart] = useState<string>()
  const [syncing, setSyncing] = useState(false)
  const [syncStatus, setSyncStatus] = useState<ICalConnectionStatus[]>([])
  const [syncResult, setSyncResult] = useState<string | null>(null)

  // The editorial calendar follows the global theme: Dark → dark, all other
  // (light) themes → light, which derives its --ed-* from global tokens.
  const editorialTheme = theme === "dark" ? "dark" : "light"

  const syncTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    if (authLoading) return
    getICalSyncStatus()
      .then((s) => setSyncStatus(s.connections))
      .catch((err) => {
        console.error("getICalSyncStatus failed:", err)
      })
  }, [authLoading])

  // Sync lastSavedView ref when preferences load asynchronously
  useEffect(() => {
    if (preferences?.calendar_default_view) {
      lastSavedView.current = preferences.calendar_default_view
    }
  }, [preferences?.calendar_default_view])

  // Clean up sync result timer on unmount
  useEffect(() => {
    return () => {
      if (syncTimerRef.current) clearTimeout(syncTimerRef.current)
    }
  }, [])

  const handleSync = useCallback(async () => {
    setSyncing(true)
    setSyncResult(null)
    if (syncTimerRef.current) clearTimeout(syncTimerRef.current)
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
      const s = await getICalSyncStatus()
      setSyncStatus(s.connections)
      syncTimerRef.current = setTimeout(() => setSyncResult(null), 5000)
    } catch {
      setSyncResult("Sync failed")
    } finally {
      setSyncing(false)
    }
  }, [])

  const handleSelectSlot = useCallback((start: string) => {
    setSelectedAppointment(null)
    setDefaultStart(start)
    setModalOpen(true)
  }, [])

  const handleSelectAppointment = useCallback((appointment: AppointmentResponse) => {
    setSelectedAppointment(appointment)
    setDefaultStart(undefined)
    setModalOpen(true)
  }, [])

  const handleClose = useCallback(() => {
    setModalOpen(false)
    setSelectedAppointment(null)
    setDefaultStart(undefined)
  }, [])

  const handleCreateNew = useCallback(() => {
    handleSelectSlot(new Date().toISOString())
  }, [handleSelectSlot])

  const handleViewChange = useCallback(
    (view: string) => {
      if (!preferences || view === lastSavedView.current) return
      lastSavedView.current = view
      saveMutation.mutate({ ...preferences, calendar_default_view: view })
    },
    [preferences, saveMutation]
  )

  const handleEditorialViewChange = useCallback(
    (v: EditorialView) => handleViewChange(fromEditorialView(v)),
    [handleViewChange],
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

      <div aria-label="Weekly appointment calendar">
        <EditorialCalendar
          theme={editorialTheme}
          workingHoursStart={preferences?.working_hours_start}
          defaultView={toEditorialView(preferences?.calendar_default_view) ?? "week"}
          onSelectSlot={handleSelectSlot}
          onSelectAppointment={handleSelectAppointment}
          onCreateNew={handleCreateNew}
          onViewChange={handleEditorialViewChange}
        />
      </div>

      <AppointmentModal
        open={modalOpen}
        onClose={handleClose}
        defaultStart={defaultStart}
        appointment={selectedAppointment}
        preferences={preferences}
        theme={editorialTheme}
      />
    </div>
  )
}
