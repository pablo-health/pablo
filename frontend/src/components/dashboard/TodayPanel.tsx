// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import Link from "next/link"
import { useMemo } from "react"
import { Button } from "@/components/ui/button"
import { useAppointmentList } from "@/hooks/useAppointments"
import type { AppointmentResponse } from "@/types/scheduling"

const STATUS_BADGES: Record<string, { label: string; cls: string }> = {
  confirmed: { label: "Scheduled", cls: "bg-secondary-50 text-secondary-700" },
  completed: { label: "Done", cls: "bg-neutral-100 text-neutral-600" },
  cancelled: { label: "Cancelled", cls: "bg-neutral-100 text-neutral-500" },
  no_show: { label: "No-show", cls: "bg-red-50 text-red-700" },
}

export function TodayPanel() {
  const { start, end } = todayBounds()
  const { data, isLoading } = useAppointmentList(start, end)

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
            Sessions scheduled for {todayLabel()}.
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
        <ul className="divide-y divide-neutral-100">
          {appts.map((a) => (
            <AppointmentRow key={a.id} appointment={a} />
          ))}
        </ul>
      )}
    </div>
  )
}

function AppointmentRow({ appointment }: { appointment: AppointmentResponse }) {
  const start = new Date(appointment.start_at)
  const time = start.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  })
  const badge = STATUS_BADGES[appointment.status]
  const startable =
    appointment.status === "confirmed" && !appointment.session_id

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
        </p>
      </div>
      {badge && (
        <span className={`text-xs px-2 py-0.5 rounded-full ${badge.cls}`}>
          {badge.label}
        </span>
      )}
      {startable ? (
        <Button asChild size="sm">
          <Link href={`/dashboard/calendar?appointment=${appointment.id}`}>
            Start session
          </Link>
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

function EmptyDay() {
  return (
    <div className="flex flex-col items-center text-center py-6">
      <Image src="/pablo-tie.webp" alt="Pablo bear" width={64} height={64} />
      <p className="text-sm text-neutral-700 mt-3">
        No sessions today. Enjoy the breathing room.
      </p>
    </div>
  )
}

function todayBounds(): { start: string; end: string } {
  const start = new Date()
  start.setHours(0, 0, 0, 0)
  const end = new Date(start)
  end.setDate(end.getDate() + 1)
  return { start: start.toISOString(), end: end.toISOString() }
}

function todayLabel(): string {
  return new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  })
}
