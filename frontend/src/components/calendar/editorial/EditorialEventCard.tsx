// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { AppointmentResponse } from "@/types/scheduling"
import { User, Users, Video } from "lucide-react"
import { format } from "date-fns"
import { editorialStatusMeta } from "./status"

interface EditorialEventCardProps {
  appointment: AppointmentResponse
  patientName: string | undefined
  onClick: (appointment: AppointmentResponse) => void
  /** Compact mode hides the time label — used for very short events. */
  compact?: boolean
}

export function EditorialEventCard({
  appointment,
  patientName,
  onClick,
  compact,
}: EditorialEventCardProps) {
  const meta = editorialStatusMeta(appointment.status)
  const isGroup =
    appointment.session_type === "group" || appointment.session_type === "couples"
  const start = new Date(appointment.start_at)
  const end = new Date(appointment.end_at)
  const Icon = isGroup ? Users : User
  const StatusIcon = meta.Icon
  const title = patientName ?? appointment.title
  const cancelled = appointment.status === "cancelled"

  return (
    <button
      type="button"
      onClick={() => onClick(appointment)}
      className="ed-event group relative flex h-full w-full flex-col items-start overflow-hidden px-2.5 py-1.5 text-left"
      style={{
        backgroundColor: meta.bg,
        color: meta.fg,
        textDecoration: cancelled ? "line-through" : undefined,
      }}
      aria-label={`${title} at ${format(start, "h:mm a")} — ${meta.label}`}
    >
      <span
        aria-hidden
        className="ed-event-rail absolute left-0 top-0 h-full w-[3px]"
        style={{ backgroundColor: meta.rail }}
      />
      <div className="flex min-w-0 items-start gap-1.5 pl-1">
        <Icon className="mt-0.5 h-3 w-3 shrink-0 opacity-70" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] font-semibold leading-tight">{title}</div>
          {!compact && (
            <div className="mt-0.5 text-[10px] uppercase tracking-[0.06em] opacity-70">
              {format(start, "h:mm")} – {format(end, "h:mm a")}
            </div>
          )}
        </div>
        {appointment.video_link && !compact && (
          <Video className="mt-0.5 h-3 w-3 shrink-0 opacity-60" aria-hidden />
        )}
        {/* Non-color status cue — distinct shape per status. */}
        <StatusIcon
          className="mt-0.5 h-3 w-3 shrink-0 opacity-80"
          aria-hidden
        />
      </div>
    </button>
  )
}
