// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { CSSProperties } from "react"
import type { AppointmentResponse } from "@/types/scheduling"
import { Video } from "lucide-react"
import { format } from "date-fns"
import { editorialStatusMeta } from "./status"

interface EditorialEventCardProps {
  appointment: AppointmentResponse
  patientName: string | undefined
  onClick: (appointment: AppointmentResponse) => void
  /** Shortest blocks (height < 30px): single line, no time subline, tight padding. */
  micro?: boolean
  /** Short blocks (height < 44px): full name, but no time subline or video icon. */
  compact?: boolean
}

/** Two-line clamp for normal/tall blocks; single-line ellipsis for micro. */
const NAME_STYLE: CSSProperties = {
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
  wordBreak: "break-word",
  fontSize: 12,
  fontWeight: 600,
  lineHeight: 1.22,
}

const MICRO_NAME_STYLE: CSSProperties = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: 12,
  fontWeight: 600,
  lineHeight: 1.22,
}

export function EditorialEventCard({
  appointment,
  patientName,
  onClick,
  micro,
  compact,
}: EditorialEventCardProps) {
  const meta = editorialStatusMeta(appointment.status)
  const start = new Date(appointment.start_at)
  const end = new Date(appointment.end_at)
  const title = patientName ?? appointment.title
  const cancelled = appointment.status === "cancelled"
  const showMeta = !compact && !micro

  return (
    <button
      type="button"
      onClick={() => onClick(appointment)}
      className="ed-event group relative flex h-full w-full flex-col items-start overflow-hidden text-left"
      style={{
        padding: micro ? "2px 7px 2px 9px" : "5px 9px 5px 11px",
        backgroundColor: meta.bg,
        color: meta.fg,
        textDecoration: cancelled ? "line-through" : undefined,
      }}
      aria-label={`${title} at ${format(start, "h:mm a")} — ${meta.label}`}
    >
      {/* 3px colored rail is the sole status cue on the card — icons removed. */}
      <span
        aria-hidden
        className="ed-event-rail absolute left-0 top-0 h-full w-[3px]"
        style={{ backgroundColor: meta.rail }}
      />
      <div className="flex w-full min-w-0 items-start gap-1.5 pl-[3px]">
        <div className="min-w-0 flex-1">
          <div style={micro ? MICRO_NAME_STYLE : NAME_STYLE}>{title}</div>
          {showMeta && (
            <div className="mt-px text-[10px] uppercase tracking-[0.05em] opacity-[0.72]">
              {format(start, "h:mm")} – {format(end, "h:mm a")}
            </div>
          )}
        </div>
        {appointment.video_link && showMeta && (
          <Video className="mt-px h-3 w-3 shrink-0 opacity-60" aria-hidden />
        )}
      </div>
    </button>
  )
}
