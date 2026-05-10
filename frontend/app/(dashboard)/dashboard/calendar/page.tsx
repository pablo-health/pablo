// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { CalendarView } from "@/components/calendar/CalendarView"
import { StatusLegend } from "@/components/calendar/StatusLegend"
import { AppointmentModal } from "@/components/calendar/AppointmentModal"
import {
  EditorialCalendar,
  type CalendarStyle,
  type EditorialTheme,
  type EditorialView,
} from "@/components/calendar/editorial"
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

const STYLE_KEY = "pablo.calendar.style"
const THEME_KEY = "pablo.calendar.theme"

function readStored<T extends string>(key: string, fallback: T, valid: T[]): T {
  if (typeof window === "undefined") return fallback
  const raw = window.localStorage.getItem(key) as T | null
  return raw && valid.includes(raw) ? raw : fallback
}

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
  const { data: preferences } = usePreferences()
  const saveMutation = useSavePreferences()
  const lastSavedView = useRef<string | undefined>(undefined)
  const [modalOpen, setModalOpen] = useState(false)
  const [selectedAppointment, setSelectedAppointment] = useState<AppointmentResponse | null>(null)
  const [defaultStart, setDefaultStart] = useState<string>()
  const [defaultEnd, setDefaultEnd] = useState<string>()
  const [syncing, setSyncing] = useState(false)
  const [syncStatus, setSyncStatus] = useState<ICalConnectionStatus[]>([])
  const [syncResult, setSyncResult] = useState<string | null>(null)
  const [calendarStyle, setCalendarStyle] = useState<CalendarStyle>(() =>
    readStored<CalendarStyle>(STYLE_KEY, "editorial", ["editorial", "classic"]),
  )
  const [editorialTheme, setEditorialTheme] = useState<EditorialTheme>(() =>
    readStored<EditorialTheme>(THEME_KEY, "light", ["light", "dark"]),
  )

  useEffect(() => {
    if (typeof window !== "undefined")
      window.localStorage.setItem(STYLE_KEY, calendarStyle)
  }, [calendarStyle])

  useEffect(() => {
    if (typeof window !== "undefined")
      window.localStorage.setItem(THEME_KEY, editorialTheme)
  }, [editorialTheme])

  const syncTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    if (authLoading) return
    getICalSyncStatus()
      .then((s) => setSyncStatus(s.connections))
      .catch(() => {})
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

      {calendarStyle === "classic" && <StatusLegend />}

      {calendarStyle === "editorial" ? (
        <div aria-label="Weekly appointment calendar">
          <EditorialCalendar
            theme={editorialTheme}
            onThemeChange={setEditorialTheme}
            style={calendarStyle}
            onStyleChange={setCalendarStyle}
            workingHoursStart={preferences?.working_hours_start}
            defaultView={toEditorialView(preferences?.calendar_default_view) ?? "week"}
            onSelectSlot={handleSelectSlot}
            onSelectAppointment={handleSelectAppointment}
            onCreateNew={handleCreateNew}
            onViewChange={handleEditorialViewChange}
          />
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-primary-50/60 px-4 py-2.5">
            <span className="text-sm text-neutral-700">
              You&rsquo;re using the classic calendar.
            </span>
            <button
              type="button"
              onClick={() => setCalendarStyle("editorial")}
              className="inline-flex items-center gap-1.5 rounded-full bg-neutral-900 px-4 py-1.5 text-sm font-medium text-primary-50 transition-colors hover:bg-neutral-800"
            >
              Switch to editorial
              <span aria-hidden>→</span>
            </button>
          </div>
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
        </>
      )}

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
