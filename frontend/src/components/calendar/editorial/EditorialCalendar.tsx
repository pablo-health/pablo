// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useMemo, useState } from "react"
import type { CSSProperties } from "react"
import { useAppointmentList, useUpdateAppointment } from "@/hooks/useAppointments"
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
import { EditorialEventPeek } from "./EditorialEventPeek"
import { EditorialEventContextMenu } from "./EditorialEventContextMenu"
import {
  DENSITY_PRESETS,
  dynamicDayWindow,
  shiftAnchor,
  visibleRange,
  type CalendarDensity,
  type EditorialView,
} from "./dateUtils"

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
  density?: CalendarDensity
  onSelectSlot: (start: string) => void
  /** Edit entrypoint — opens the edit sheet (double-click or peek's Edit). */
  onSelectAppointment: (appointment: AppointmentResponse) => void
  onCreateNew: () => void
  onViewChange?: (view: EditorialView) => void
}

interface PeekState {
  appointment: AppointmentResponse
  anchorRect: DOMRect
}

interface CtxMenuState {
  appointment: AppointmentResponse
  x: number
  y: number
}

export function EditorialCalendar({
  defaultView = "week",
  workingHoursStart = 8,
  theme,
  density = "balanced",
  onSelectSlot,
  onSelectAppointment,
  onCreateNew,
  onViewChange,
}: EditorialCalendarProps) {
  const preset = DENSITY_PRESETS[density]
  const [view, setView] = useState<EditorialView>(defaultView)
  const [anchor, setAnchor] = useState<Date>(() => new Date())
  const [statusFilters, setStatusFilters] = useState<Set<AppointmentStatus>>(
    DEFAULT_STATUS_FILTERS,
  )
  const [pickerOpen, setPickerOpen] = useState(false)
  const [peek, setPeek] = useState<PeekState | null>(null)
  const [ctxMenu, setCtxMenu] = useState<CtxMenuState | null>(null)

  const range = useMemo(() => visibleRange(view, anchor), [view, anchor])
  const { data } = useAppointmentList(
    range.start.toISOString(),
    range.end.toISOString(),
  )
  const { data: patientData } = usePatientList()
  const updateAppointment = useUpdateAppointment()

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

  // Dynamic working-hours window expands to contain any out-of-default
  // appointments so they render at their true position rather than being
  // clamped to the 7–20 boundary.
  const { dayStart, dayEnd } = useMemo(
    () => dynamicDayWindow(filteredAppointments),
    [filteredAppointments],
  )

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

  const handlePeek = useCallback(
    (appointment: AppointmentResponse, anchorRect: DOMRect) => {
      setPeek({ appointment, anchorRect })
    },
    [],
  )

  const handleEdit = useCallback(
    (appointment: AppointmentResponse) => {
      setPeek(null)
      onSelectAppointment(appointment)
    },
    [onSelectAppointment],
  )

  const handleContextMenu = useCallback(
    (appointment: AppointmentResponse, x: number, y: number) => {
      setPeek(null)
      setCtxMenu({ appointment, x, y })
    },
    [],
  )

  const handleSetStatus = useCallback(
    (appointment: AppointmentResponse, status: AppointmentStatus) => {
      if (status !== appointment.status) {
        updateAppointment.mutate({
          appointmentId: appointment.id,
          data: { status },
        })
      }
      setCtxMenu(null)
    },
    [updateAppointment],
  )

  const handleMove = useCallback(
    (appointment: AppointmentResponse, newStartIso: string) => {
      // Preserve the original duration; the new start arrives already snapped
      // and clamped within its day by the event wrapper.
      const newEnd = new Date(
        new Date(newStartIso).getTime() +
          appointment.duration_minutes * 60_000,
      ).toISOString()
      updateAppointment.mutate({
        appointmentId: appointment.id,
        data: { start_at: newStartIso, end_at: newEnd },
      })
    },
    [updateAppointment],
  )

  const peekPatientName = peek
    ? patientMap.get(peek.appointment.patient_id)
    : undefined

  return (
    <div
      data-editorial-theme={theme}
      data-density={density}
      className="ed-canvas relative flex min-h-[720px] overflow-hidden rounded-[18px]"
      style={{
        color: "var(--ed-ink)",
        border: "1px solid var(--ed-hairline)",
        boxShadow: "var(--ed-shadow-card)",
        "--ed-row-h": `${preset.rowPx}px`,
        "--ed-stack-gap": `${preset.stackGapPx}px`,
        "--ed-stack-pad-y": `${preset.stackPadYPx}px`,
      } as CSSProperties} // CSSProperties has no index signature for custom properties
    >
      <EditorialSidebar
        selected={anchor}
        statusFilters={statusFilters}
        onSelectDate={(d) => setAnchor(d)}
        onCreateNew={onCreateNew}
        onToggleStatus={handleToggleStatus}
      />

      <div
        className="relative flex flex-1 flex-col px-6 sm:px-8"
        style={{
          gap: "var(--ed-stack-gap)",
          paddingTop: "var(--ed-stack-pad-y)",
          paddingBottom: "var(--ed-stack-pad-y)",
        }}
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <EditorialDateHeader
            view={view}
            anchor={anchor}
            onPrev={() => setAnchor((a) => shiftAnchor(view, a, -1))}
            onNext={() => setAnchor((a) => shiftAnchor(view, a, 1))}
            onToday={() => setAnchor(new Date())}
            onPickerOpen={() => setPickerOpen((p) => !p)}
          />
          <div className="flex items-center gap-4">
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
            onPeek={handlePeek}
            onEdit={handleEdit}
            onMove={handleMove}
            onContextMenu={handleContextMenu}
            scrollToHour={workingHoursStart}
            dayStart={dayStart}
            dayEnd={dayEnd}
            rowHeightPx={preset.rowPx}
          />
        )}
        {view === "day" && (
          <EditorialDayView
            anchor={anchor}
            appointments={filteredAppointments}
            patientMap={patientMap}
            onSelectSlot={onSelectSlot}
            onPeek={handlePeek}
            onEdit={handleEdit}
            onMove={handleMove}
            onContextMenu={handleContextMenu}
            scrollToHour={workingHoursStart}
            dayStart={dayStart}
            dayEnd={dayEnd}
            rowHeightPx={preset.rowPx}
          />
        )}
        {view === "month" && (
          <EditorialMonthView
            anchor={anchor}
            appointments={filteredAppointments}
            patientMap={patientMap}
            onSelectDay={handleMonthDaySelect}
            onPeek={handlePeek}
            onEdit={handleEdit}
            onContextMenu={handleContextMenu}
          />
        )}

        <UnmatchedBanner appointments={filteredAppointments} />
        <StatusFooter statusFilters={statusFilters} />
      </div>

      {peek && (
        <EditorialEventPeek
          appointment={peek.appointment}
          patientName={peekPatientName}
          anchorRect={peek.anchorRect}
          onClose={() => setPeek(null)}
          onEdit={handleEdit}
        />
      )}

      {ctxMenu && (
        <EditorialEventContextMenu
          appointment={ctxMenu.appointment}
          x={ctxMenu.x}
          y={ctxMenu.y}
          onClose={() => setCtxMenu(null)}
          onSetStatus={handleSetStatus}
          onEdit={handleEdit}
        />
      )}
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

