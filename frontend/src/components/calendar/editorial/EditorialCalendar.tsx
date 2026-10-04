// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useMemo, useState } from "react"
import type { CSSProperties } from "react"
import { useAppointmentList, useUpdateAppointment } from "@/hooks/useAppointments"
import {
  useHeldGoogleRemovals,
  useResolveGoogleChange,
  useResolveHeldGoogleRemovals,
} from "@/hooks/useGoogleCalendarChanges"
import { usePatientList } from "@/hooks/usePatients"
import { useOutsideSessions } from "@/hooks/useOutsideSessions"
import { useAvailabilityRules } from "@/hooks/useAvailability"
import { summarize } from "@/components/settings/AvailabilitySettings"
import { useToast } from "@/components/ui/Toast"
import { ApiError } from "@/lib/api/client"
import type {
  AppointmentResponse,
  AppointmentStatus,
  GoogleChangeResolution,
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
import { AutoBookedNotice } from "./AutoBookedNotice"
import { GoogleChangesBanner } from "./GoogleChangesBanner"
import { needsGoogleDecision } from "./GoogleChangeNotice"
import { useOutsideReview } from "./useOutsideReview"
import { matchWholeDayBlockRule } from "./unavailability"
import { openingAnchor } from "./schedule"
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
  /** The practice's zone, which availability rules are kept in. Defaults to
   * the browser's. */
  timeZone?: string
  /** Open on the week of the next working day when nothing is left in this
   * one. For the first look after setup; see `openingAnchor`. */
  skipSpentWeek?: boolean
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
  timeZone,
  skipSpentWeek = false,
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
  const { showToast } = useToast()

  const handleUpdateError = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 409) {
        showToast("That time conflicts with another appointment.", "error")
      } else {
        showToast("Couldn't update the appointment. Please try again.", "error")
      }
    },
    [showToast],
  )

  const { data: heldGoogleRemovals } = useHeldGoogleRemovals()
  const resolveGoogleChange = useResolveGoogleChange()
  const resolveHeldGoogleRemovals = useResolveHeldGoogleRemovals()
  const googleChangePending =
    resolveGoogleChange.isPending || resolveHeldGoogleRemovals.isPending

  const handleResolveGoogleChange = useCallback(
    (appointment: AppointmentResponse, resolution: GoogleChangeResolution) => {
      resolveGoogleChange.mutate(
        { appointmentId: appointment.id, resolution },
        { onSuccess: () => setPeek(null), onError: handleUpdateError },
      )
    },
    [resolveGoogleChange, handleUpdateError],
  )

  const handleResolveHeld = useCallback(
    (resolution: GoogleChangeResolution) => {
      resolveHeldGoogleRemovals.mutate(resolution, { onError: handleUpdateError })
    },
    [resolveHeldGoogleRemovals, handleUpdateError],
  )

  // Events from the clinician's own calendar still waiting for a client.
  const { data: outsideData } = useOutsideSessions(
    range.start.toISOString(),
    range.end.toISOString(),
  )
  const outsideSessions = useMemo(() => outsideData?.events ?? [], [outsideData])
  const outsideReview = useOutsideReview()

  const { data: availabilityRulesData } = useAvailabilityRules()
  const availabilityRules = useMemo(
    () => availabilityRulesData?.data ?? [],
    [availabilityRulesData],
  )
  // Only day view needs this at the EditorialCalendar level — week view
  // attributes each of its own 7 columns internally.
  const dayBlockedLabel = useMemo(() => {
    if (view !== "day" || availabilityRules.length === 0) return undefined
    const rule = matchWholeDayBlockRule(availabilityRules, anchor)
    return rule ? summarize(rule) : undefined
  }, [view, availabilityRules, anchor])

  // Decided once, as soon as the hours and this week's sessions are in, and
  // never again: after that the anchor is the clinician's to move.
  const [openingDecided, setOpeningDecided] = useState(!skipSpentWeek)
  if (!openingDecided && availabilityRulesData && data) {
    setOpeningDecided(true)
    const now = new Date()
    const next = openingAnchor(availabilityRulesData.data, data.data, now, timeZone)
    if (next !== now) setAnchor(next)
  }

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
        updateAppointment.mutate(
          {
            appointmentId: appointment.id,
            data: { status },
          },
          { onError: handleUpdateError },
        )
      }
      setCtxMenu(null)
    },
    [updateAppointment, handleUpdateError],
  )

  const handleMove = useCallback(
    (appointment: AppointmentResponse, newStartIso: string) => {
      // Preserve the original duration; the new start arrives already snapped
      // and clamped within its day by the event wrapper.
      const newEnd = new Date(
        new Date(newStartIso).getTime() +
          appointment.duration_minutes * 60_000,
      ).toISOString()
      updateAppointment.mutate(
        {
          appointmentId: appointment.id,
          data: { start_at: newStartIso, end_at: newEnd },
        },
        { onError: handleUpdateError },
      )
    },
    [updateAppointment, handleUpdateError],
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
            blockedLabel={dayBlockedLabel}
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

        <GoogleChangesBanner
          appointments={data?.data ?? []}
          patientMap={patientMap}
          heldCount={heldGoogleRemovals?.count ?? 0}
          onResolve={handleResolveGoogleChange}
          onResolveHeld={handleResolveHeld}
          outsideCount={outsideReview.count}
          outsideFromGoogle={outsideReview.fromGoogleOnly}
          onReviewOutside={outsideReview.openAll}
          pending={googleChangePending}
        />

        <AutoBookedNotice />

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
            availabilityRules={availabilityRules}
            timeZone={timeZone}
            onSelectSlot={onSelectSlot}
            onPeek={handlePeek}
            onEdit={handleEdit}
            onMove={handleMove}
            onContextMenu={handleContextMenu}
            scrollToHour={workingHoursStart}
            dayStart={dayStart}
            dayEnd={dayEnd}
            rowHeightPx={preset.rowPx}
            outsideSessions={outsideSessions}
            onOpenOutside={outsideReview.openSingle}
          />
        )}
        {view === "day" && (
          <EditorialDayView
            anchor={anchor}
            appointments={filteredAppointments}
            patientMap={patientMap}
            availabilityRules={availabilityRules}
            timeZone={timeZone}
            onSelectSlot={onSelectSlot}
            onPeek={handlePeek}
            onEdit={handleEdit}
            onMove={handleMove}
            onContextMenu={handleContextMenu}
            scrollToHour={workingHoursStart}
            dayStart={dayStart}
            dayEnd={dayEnd}
            rowHeightPx={preset.rowPx}
            outsideSessions={outsideSessions}
            onOpenOutside={outsideReview.openSingle}
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

        <StatusFooter statusFilters={statusFilters} />
      </div>

      {peek && (
        <EditorialEventPeek
          appointment={peek.appointment}
          patientName={peekPatientName}
          anchorRect={peek.anchorRect}
          onClose={() => setPeek(null)}
          onEdit={handleEdit}
          onResolveGoogleChange={
            needsGoogleDecision(peek.appointment) ? handleResolveGoogleChange : undefined
          }
          googleChangePending={googleChangePending}
        />
      )}

      {outsideReview.dialog}

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

