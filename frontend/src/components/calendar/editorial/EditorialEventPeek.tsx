// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useRef } from "react"
import { format } from "date-fns"
import { CalendarDays, User, Users, Video, X } from "lucide-react"
import type { AppointmentResponse } from "@/types/scheduling"
import { editorialStatusMeta } from "./status"

/** Popover dimensions used for viewport clamping. */
const PEEK_WIDTH = 320
const PEEK_MAX_HEIGHT = 260
const VIEWPORT_MARGIN = 12
const ANCHOR_GAP = 8

const SESSION_TYPE_LABELS: Record<string, string> = {
  individual: "Individual",
  couples: "Couples",
  group: "Group",
}

interface EditorialEventPeekProps {
  appointment: AppointmentResponse
  patientName: string | undefined
  /** Viewport rect of the clicked event; the peek anchors beside it. */
  anchorRect: DOMRect
  onClose: () => void
  /** "Edit" button / handoff to the existing edit flow. */
  onEdit: (appointment: AppointmentResponse) => void
}

/** Clamp the anchored peek into the viewport (position: fixed). */
function clampedPosition(anchorRect: DOMRect): { left: number; top: number } {
  const left = Math.min(
    Math.max(anchorRect.right + ANCHOR_GAP, VIEWPORT_MARGIN),
    window.innerWidth - PEEK_WIDTH - VIEWPORT_MARGIN,
  )
  const top = Math.min(
    Math.max(anchorRect.top, VIEWPORT_MARGIN),
    window.innerHeight - PEEK_MAX_HEIGHT - VIEWPORT_MARGIN,
  )
  return { left, top }
}

/**
 * Lightweight appointment detail popover shown on a single click. Closes on
 * outside pointerdown or Escape; the Edit button hands off to the edit flow.
 */
export function EditorialEventPeek({
  appointment,
  patientName,
  anchorRect,
  onClose,
  onEdit,
}: EditorialEventPeekProps) {
  const ref = useRef<HTMLDivElement>(null)
  const meta = editorialStatusMeta(appointment.status)
  const start = new Date(appointment.start_at)
  const end = new Date(appointment.end_at)
  const isGroup =
    appointment.session_type === "group" ||
    appointment.session_type === "couples"
  const SessionIcon = isGroup ? Users : User
  const sessionLabel =
    SESSION_TYPE_LABELS[appointment.session_type] ?? appointment.session_type

  useEffect(() => {
    const handlePointerDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("pointerdown", handlePointerDown)
    window.addEventListener("keydown", handleKeyDown)
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown)
      window.removeEventListener("keydown", handleKeyDown)
    }
  }, [onClose])

  const { left, top } = clampedPosition(anchorRect)

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={`${patientName ?? appointment.title} appointment details`}
      className="ed-dialog-in fixed z-[80] overflow-hidden rounded-2xl"
      style={{
        left,
        top,
        width: PEEK_WIDTH,
        maxWidth: "calc(100vw - 24px)",
        backgroundColor: "var(--ed-canvas-elev)",
        boxShadow: "var(--ed-shadow-modal)",
        border: "1px solid var(--ed-hairline-strong)",
      }}
    >
      <div style={{ height: 4, backgroundColor: meta.rail }} aria-hidden />
      <div className="p-5">
        <div className="flex items-start justify-between gap-3">
          <h3
            className="font-display text-[18px] font-semibold leading-snug"
            style={{ color: "var(--ed-ink)" }}
          >
            {patientName ?? appointment.title}
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="ed-iconbtn -mr-1.5 -mt-1 shrink-0 rounded-full p-1.5"
            style={{ color: "var(--ed-ink-muted)" }}
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <span
          className="mt-2 inline-block rounded-full px-2.5 py-1 text-[11.5px] font-semibold"
          style={{ backgroundColor: meta.bg, color: meta.fg }}
        >
          {meta.label}
        </span>

        <div
          className="mt-4 flex flex-col gap-2.5 text-[14px]"
          style={{ color: "var(--ed-ink-muted)" }}
        >
          <PeekRow icon={<CalendarDays className="h-[15px] w-[15px]" />}>
            {format(start, "EEEE, MMM d")} · {format(start, "h:mm")} –{" "}
            {format(end, "h:mm a")}
          </PeekRow>
          <PeekRow icon={<SessionIcon className="h-[15px] w-[15px]" />}>
            {sessionLabel} · {appointment.duration_minutes} min
          </PeekRow>
          {appointment.video_link && (
            <PeekRow icon={<Video className="h-[15px] w-[15px]" />}>
              <a
                href={appointment.video_link}
                target="_blank"
                rel="noreferrer"
                className="break-all underline-offset-2 hover:underline"
                style={{ color: "var(--ed-accent)" }}
              >
                {appointment.video_link}
              </a>
            </PeekRow>
          )}
        </div>

        <div className="mt-5 flex items-center justify-end gap-2.5">
          <span
            className="mr-auto text-[11.5px] italic"
            style={{ color: "var(--ed-ink-soft)" }}
          >
            Tip: double-click to edit
          </span>
          <button
            type="button"
            onClick={() => onEdit(appointment)}
            className="rounded-full px-4 py-2 text-[13px] font-bold"
            style={{
              backgroundColor: "var(--ed-cta-bg)",
              color: "var(--ed-cta-fg)",
            }}
          >
            Edit
          </button>
        </div>
      </div>
    </div>
  )
}

function PeekRow({
  icon,
  children,
}: {
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="shrink-0" style={{ color: "var(--ed-ink-soft)" }}>
        {icon}
      </span>
      <span className="min-w-0">{children}</span>
    </div>
  )
}
