// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useMemo, useState } from "react"
import { useAppointmentList } from "@/hooks/useAppointments"
import { usePatientList } from "@/hooks/usePatients"
import type {
  AppointmentResponse,
  AppointmentStatus,
} from "@/types/scheduling"
import { CalendarDays } from "lucide-react"
import "./editorial.css"
import { EditorialDateHeader } from "./EditorialDateHeader"
import { EditorialViewSwitcher } from "./EditorialViewSwitcher"
import { EditorialWeekView } from "./EditorialWeekView"
import { EditorialDayView } from "./EditorialDayView"
import { EditorialMonthView } from "./EditorialMonthView"
import { EditorialSidebar, type EditorialTheme } from "./EditorialSidebar"
import { EditorialMiniMonth } from "./EditorialMiniMonth"
import { shiftAnchor, visibleRange, type EditorialView } from "./dateUtils"

const ALL_STATUSES: AppointmentStatus[] = [
  "confirmed",
  "completed",
  "cancelled",
  "no_show",
]
const DEFAULT_STATUS_FILTERS = new Set<AppointmentStatus>([
  "confirmed",
  "completed",
  "no_show",
])

interface EditorialCalendarProps {
  defaultView?: EditorialView
  workingHoursStart?: number
  theme: EditorialTheme
  onSelectSlot: (start: string) => void
  onSelectAppointment: (appointment: AppointmentResponse) => void
  onCreateNew: () => void
  onViewChange?: (view: EditorialView) => void
}

export function EditorialCalendar({
  defaultView = "week",
  workingHoursStart = 8,
  theme,
  onSelectSlot,
  onSelectAppointment,
  onCreateNew,
  onViewChange,
}: EditorialCalendarProps) {
  const [view, setView] = useState<EditorialView>(defaultView)
  const [anchor, setAnchor] = useState<Date>(() => new Date())
  const [statusFilters, setStatusFilters] = useState<Set<AppointmentStatus>>(
    DEFAULT_STATUS_FILTERS,
  )
  const [pickerOpen, setPickerOpen] = useState(false)

  const range = useMemo(() => visibleRange(view, anchor), [view, anchor])
  const { data } = useAppointmentList(
    range.start.toISOString(),
    range.end.toISOString(),
  )
  const { data: patientData } = usePatientList()

  const patientMap = useMemo(() => {
    const map = new Map<string, string>()
    for (const p of patientData?.data ?? []) {
      map.set(p.id, `${p.first_name} ${p.last_name}`)
    }
    return map
  }, [patientData])

  const filteredAppointments = useMemo(() => {
    const all = data?.data ?? []
    return all.filter((a) =>
      statusFilters.has(a.status as AppointmentStatus),
    )
  }, [data, statusFilters])

  const handleViewChange = useCallback(
    (next: EditorialView) => {
      setView(next)
      onViewChange?.(next)
    },
    [onViewChange],
  )

  const handleToggleStatus = useCallback((status: AppointmentStatus) => {
    setStatusFilters((prev) => {
      const next = new Set(prev)
      if (next.has(status)) next.delete(status)
      else next.add(status)
      return next
    })
  }, [])

  const handleMonthDaySelect = useCallback(
    (date: Date) => {
      setAnchor(date)
      handleViewChange("day")
    },
    [handleViewChange],
  )

  const handlePickerSelect = useCallback((date: Date) => {
    setAnchor(date)
    setPickerOpen(false)
  }, [])

  return (
    <div
      data-editorial-theme={theme}
      className="ed-canvas relative flex min-h-[720px] overflow-hidden rounded-2xl"
      style={{ color: "var(--ed-ink)" }}
    >
      <EditorialSidebar
        selected={anchor}
        statusFilters={statusFilters}
        onSelectDate={(d) => setAnchor(d)}
        onCreateNew={onCreateNew}
        onToggleStatus={handleToggleStatus}
      />

      <div className="relative flex flex-1 flex-col gap-6 px-6 py-6 sm:px-8 sm:py-8">
        <EditorialDateHeader
          view={view}
          anchor={anchor}
          onPrev={() => setAnchor((a) => shiftAnchor(view, a, -1))}
          onNext={() => setAnchor((a) => shiftAnchor(view, a, 1))}
          onToday={() => setAnchor(new Date())}
          onPickerOpen={() => setPickerOpen((p) => !p)}
        />

        <div className="flex items-center justify-between gap-4">
          <EditorialViewSwitcher view={view} onChange={handleViewChange} />
          <button
            type="button"
            onClick={() => setPickerOpen((p) => !p)}
            className="flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium tracking-wide transition-colors hover:bg-[var(--ed-pill-hover)] lg:hidden"
            style={{ color: "var(--ed-ink-muted)" }}
            aria-label="Pick a date"
          >
            <CalendarDays className="h-4 w-4" />
            Pick date
          </button>
        </div>

        {pickerOpen && (
          <div
            className="absolute right-6 top-32 z-30 w-[300px] rounded-2xl p-4 lg:right-8"
            style={{
              backgroundColor: "var(--ed-canvas-elev)",
              boxShadow: "var(--ed-shadow-card-hover)",
              border: "1px solid var(--ed-hairline-strong)",
            }}
          >
            <EditorialMiniMonth selected={anchor} onSelect={handlePickerSelect} />
          </div>
        )}

        {view === "week" && (
          <EditorialWeekView
            anchor={anchor}
            appointments={filteredAppointments}
            patientMap={patientMap}
            onSelectSlot={onSelectSlot}
            onSelectAppointment={onSelectAppointment}
            scrollToHour={workingHoursStart}
          />
        )}
        {view === "day" && (
          <EditorialDayView
            anchor={anchor}
            appointments={filteredAppointments}
            patientMap={patientMap}
            onSelectSlot={onSelectSlot}
            onSelectAppointment={onSelectAppointment}
            scrollToHour={workingHoursStart}
          />
        )}
        {view === "month" && (
          <EditorialMonthView
            anchor={anchor}
            appointments={filteredAppointments}
            patientMap={patientMap}
            onSelectDay={handleMonthDaySelect}
            onSelectAppointment={onSelectAppointment}
          />
        )}

        <UnmatchedBanner appointments={filteredAppointments} />
        <StatusFooter statusFilters={statusFilters} />
      </div>
    </div>
  )
}

function UnmatchedBanner({ appointments }: { appointments: AppointmentResponse[] }) {
  const count = appointments.filter(
    (a) => a.patient_id === "" && a.notes?.startsWith("ical_client:"),
  ).length
  if (count === 0) return null
  return (
    <div
      className="rounded-lg px-4 py-2.5 text-sm font-medium"
      style={{
        backgroundColor: "var(--ed-status-confirmed-bg)",
        color: "var(--ed-status-confirmed-fg)",
      }}
      role="status"
    >
      {count} appointment{count === 1 ? "" : "s"} from your EHR need patient matching
    </div>
  )
}

function StatusFooter({ statusFilters }: { statusFilters: Set<AppointmentStatus> }) {
  // Subtle reminder of which statuses are hidden — editorial caption style.
  const hidden = ALL_STATUSES.filter((s) => !statusFilters.has(s))
  if (hidden.length === 0) return null
  return (
    <p
      className="text-[11px] italic"
      style={{ color: "var(--ed-ink-soft)" }}
    >
      Hiding: {hidden.map((s) => s.replace("_", " ")).join(", ")}
    </p>
  )
}

