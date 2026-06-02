// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import Link from "next/link"
import { useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { useAppointmentList } from "@/hooks/useAppointments"
import { usePatientList } from "@/hooks/usePatients"
import { useUserTimeZone } from "@/hooks/usePreferences"
import { isCompanionAvailable } from "@/lib/companion"
import type { AppointmentResponse } from "@/types/scheduling"
import { CompanionGetDialog } from "./CompanionGetDialog"

const STATUS_BADGES: Record<string, { label: string; cls: string }> = {
  confirmed: { label: "Scheduled", cls: "bg-secondary-50 text-secondary-700" },
  completed: { label: "Done", cls: "bg-neutral-100 text-neutral-600" },
  cancelled: { label: "Cancelled", cls: "bg-neutral-100 text-neutral-500" },
  no_show: { label: "No-show", cls: "bg-red-50 text-red-700" },
}

// Recording happens in the desktop companion app, not the browser.
// See docs/url-scheme.md for the full pablohealth:// grammar.
function startSessionUri(appointmentId: string): string {
  return `pablohealth://session/start?appointment=${encodeURIComponent(appointmentId)}`
}

export function TodayPanel() {
  const { start, end } = todayBounds()
  const timeZone = useUserTimeZone()
  const { data, isLoading } = useAppointmentList(start, end)
  const { data: patientData } = usePatientList()
  const [companionDialogOpen, setCompanionDialogOpen] = useState(false)
  const companionAvailable = isCompanionAvailable()

  const lastVisitByPatient = useMemo(() => {
    const m = new Map<string, string | null>()
    for (const p of patientData?.data ?? []) m.set(p.id, p.last_session_date)
    return m
  }, [patientData])

  const appts = useMemo(() => {
    const rows = data?.data ?? []
    return [...rows].sort((a, b) => a.start_at.localeCompare(b.start_at))
  }, [data])

  return (
    <div className="card">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h2 className="text-xl font-display font-semibold text-neutral-900">
            Today
          </h2>
          <p className="text-sm text-neutral-600 mt-1">
            Sessions scheduled for {todayLabel(timeZone)}.
          </p>
        </div>
        <Link
          href="/dashboard/calendar"
          className="text-sm text-primary-700 hover:underline"
        >
          Open calendar
        </Link>
      </div>

      {isLoading ? (
        <p className="text-sm text-neutral-500 py-6 text-center">Loading…</p>
      ) : appts.length === 0 ? (
        <EmptyDay />
      ) : (
        <>
          <ul className="divide-y divide-neutral-100">
            {appts.map((a) => (
              <AppointmentRow
                key={a.id}
                appointment={a}
                lastVisit={lastVisitByPatient.get(a.patient_id) ?? null}
                timeZone={timeZone}
                companionAvailable={companionAvailable}
              />
            ))}
          </ul>
          <CompanionFooter onGetApp={() => setCompanionDialogOpen(true)} />
          <CompanionGetDialog
            open={companionDialogOpen}
            onOpenChange={setCompanionDialogOpen}
          />
        </>
      )}
    </div>
  )
}

interface AppointmentRowProps {
  appointment: AppointmentResponse
  lastVisit: string | null
  timeZone: string
  companionAvailable: boolean
}

function AppointmentRow({
  appointment,
  lastVisit,
  timeZone,
  companionAvailable,
}: AppointmentRowProps) {
  const start = new Date(appointment.start_at)
  const time = start.toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  })
  const badge = STATUS_BADGES[appointment.status]
  const startable =
    companionAvailable &&
    appointment.status === "confirmed" &&
    !appointment.session_id
  const lastVisitLabel = formatLastVisit(lastVisit, appointment.start_at)

  return (
    <li className="flex items-center gap-3 py-3">
      <div className="w-20 shrink-0 font-mono text-sm text-neutral-700">
        {time}
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-medium text-neutral-900 truncate">
          {appointment.title}
        </p>
        <p className="text-xs text-neutral-500 truncate">
          {appointment.duration_minutes} min · {appointment.session_type}
          {appointment.video_platform ? ` · ${appointment.video_platform}` : ""}
          {lastVisitLabel ? ` · ${lastVisitLabel}` : ""}
        </p>
      </div>
      {badge && (
        <span className={`text-xs px-2 py-0.5 rounded-full ${badge.cls}`}>
          {badge.label}
        </span>
      )}
      {startable ? (
        <Button asChild size="sm">
          {/* External URL scheme — not a Next route. */}
          <a href={startSessionUri(appointment.id)}>Start session</a>
        </Button>
      ) : appointment.session_id ? (
        <Button asChild size="sm" variant="outline">
          <Link href={`/dashboard/sessions/${appointment.session_id}`}>
            Open
          </Link>
        </Button>
      ) : null}
    </li>
  )
}

function CompanionFooter({ onGetApp }: { onGetApp: () => void }) {
  return (
    <p className="text-xs text-neutral-500 mt-3 pt-3 border-t border-neutral-100 text-center">
      Recording happens in the Pablo desktop app.{" "}
      <button
        type="button"
        onClick={onGetApp}
        className="text-primary-700 hover:underline"
      >
        Don&apos;t have it yet?
      </button>
    </p>
  )
}

function EmptyDay() {
  return (
    <div className="flex flex-col items-center text-center py-6">
      <Image src="/pablo-rest.webp" alt="Pablo bear" width={72} height={72} />
      <p className="text-sm text-neutral-700 mt-3">
        No sessions today. Enjoy the breathing room.
      </p>
    </div>
  )
}

// The query window is the browser's local day. Day *labels* and times
// follow the user's timezone preference (see todayLabel / AppointmentRow);
// these line up whenever the preference matches the browser zone, which is
// the default. A tz-aware window would only matter for a clinician viewing
// the dashboard from a different zone than their preference near midnight.
function todayBounds(): { start: string; end: string } {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const end = new Date(start)
  end.setDate(end.getDate() + 1)
  return { start: start.toISOString(), end: end.toISOString() }
}

// "last visit 4w ago" — coarse-grained on purpose; therapists don't need
// exact day counts, just "is this someone I just saw or haven't seen in a
// while." Falls back to null when there's no prior visit (first session)
// or when the appointment is in the past relative to last_session_date
// (e.g. data churn after a cancellation).
export function formatLastVisit(
  lastSessionDate: string | null,
  appointmentStart: string,
): string | null {
  if (!lastSessionDate) return null
  const last = new Date(lastSessionDate).getTime()
  const ref = new Date(appointmentStart).getTime()
  const diffMs = ref - last
  if (diffMs <= 0) return null
  const days = Math.round(diffMs / (1000 * 60 * 60 * 24))
  if (days <= 1) return "last visit yesterday"
  if (days < 7) return `last visit ${days}d ago`
  const weeks = Math.round(days / 7)
  if (weeks < 8) return `last visit ${weeks}w ago`
  const months = Math.round(days / 30)
  return `last visit ${months}mo ago`
}

function todayLabel(timeZone: string): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone,
  })
}
