// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { useMemo } from "react"
import { useAppointmentList } from "@/hooks/useAppointments"
import { useSessionList } from "@/hooks/useSessions"

export function WeekPanel() {
  const { start, end } = restOfWeekBounds()
  const { data: apptData } = useAppointmentList(start, end)
  const { data: sessionData, isLoading: sessionsLoading } = useSessionList()

  const stats = useMemo(() => {
    const sessions = sessionData?.data ?? []
    const notesPending = sessions.filter(
      (s) => s.note !== null && s.note.finalized_at === null,
    ).length
    const transcriptionPending = sessions.filter(
      (s) => s.status === "queued" || s.status === "processing",
    ).length
    const upcoming = (apptData?.data ?? []).filter(
      (a) => a.status === "confirmed",
    ).length
    return { notesPending, transcriptionPending, upcoming }
  }, [sessionData, apptData])

  return (
    <div className="card">
      <h2 className="text-xl font-display font-semibold text-neutral-900">
        This week
      </h2>
      <p className="text-sm text-neutral-600 mt-1 mb-4">
        Loose ends to tie before the weekend.
      </p>

      <ul className="space-y-2">
        <StatRow
          label="Notes awaiting your signature"
          value={stats.notesPending}
          loading={sessionsLoading}
          href="/dashboard/sessions"
          urgent={stats.notesPending > 0}
        />
        <StatRow
          label="Transcripts still processing"
          value={stats.transcriptionPending}
          loading={sessionsLoading}
          href="/dashboard/sessions"
        />
        <StatRow
          label="Upcoming sessions"
          value={stats.upcoming}
          loading={false}
          href="/dashboard/calendar"
        />
      </ul>
    </div>
  )
}

interface StatRowProps {
  label: string
  value: number
  loading: boolean
  href: string
  urgent?: boolean
}

function StatRow({ label, value, loading, href, urgent }: StatRowProps) {
  const valueCls = urgent
    ? "text-primary-700 font-semibold"
    : "text-neutral-700 font-medium"
  return (
    <li>
      <Link
        href={href}
        className="flex items-center justify-between rounded-md px-3 py-2 -mx-3 hover:bg-neutral-50 transition-colors"
      >
        <span className="text-sm text-neutral-700">{label}</span>
        <span className={`text-lg ${valueCls}`}>
          {loading ? "—" : value}
        </span>
      </Link>
    </li>
  )
}

function restOfWeekBounds(): { start: string; end: string } {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const end = new Date(start)
  // Advance to next Monday 00:00.
  const daysUntilMonday = (8 - start.getDay()) % 7 || 7
  end.setDate(end.getDate() + daysUntilMonday)
  return { start: start.toISOString(), end: end.toISOString() }
}
