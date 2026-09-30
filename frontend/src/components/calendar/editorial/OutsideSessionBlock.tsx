// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { format, isSameDay } from "date-fns"
import { HelpCircle } from "lucide-react"
import type { OutsideSession } from "@/lib/api/outsideSessions"
import { minutesSinceMidnight } from "./dateUtils"

interface OutsideSessionLayerProps {
  day: Date
  sessions: OutsideSession[]
  /** First hour the grid shows; blocks are positioned from it. */
  dayStart: number
  rowHeightPx: number
  onOpen: (session: OutsideSession) => void
}

/**
 * Events on the clinician's own calendar that may be sessions, nobody having
 * said who they are with yet. Read-only: they are not appointments, so they
 * can't be dragged or edited, only answered.
 */
export function OutsideSessionLayer({
  day,
  sessions,
  dayStart,
  rowHeightPx,
  onOpen,
}: OutsideSessionLayerProps) {
  return (
    <>
      {sessions
        .filter((session) => isSameDay(new Date(session.start_at), day))
        .map((session) => {
          const startMin = minutesSinceMidnight(session.start_at)
          const endMin = minutesSinceMidnight(session.end_at)
          const top = ((startMin - dayStart * 60) / 60) * rowHeightPx
          const height = Math.max(((endMin - startMin) / 60) * rowHeightPx - 2, 20)
          return (
            <OutsideSessionBlock
              key={session.id}
              session={session}
              onOpen={onOpen}
              style={{ top, height, left: 2, right: 2 }}
            />
          )
        })}
    </>
  )
}

export function OutsideSessionBlock({
  session,
  onOpen,
  style,
}: {
  session: OutsideSession
  onOpen: (session: OutsideSession) => void
  style?: React.CSSProperties
}) {
  const start = new Date(session.start_at)
  return (
    <button
      type="button"
      data-testid="outside-session"
      onClick={(event) => {
        // The column underneath books a new appointment on click.
        event.stopPropagation()
        onOpen(session)
      }}
      className="absolute z-[5] flex flex-col overflow-hidden rounded-md border border-dashed px-2 py-1 text-left text-[11.5px]"
      style={{
        ...style,
        borderColor: "var(--ed-hairline-strong)",
        backgroundColor: "var(--ed-canvas)",
        color: "var(--ed-ink-muted)",
      }}
      aria-label={`${session.title} at ${format(start, "h:mm a")} — who is this?`}
    >
      <span className="truncate font-semibold">{session.title}</span>
      <span
        data-testid="outside-session-marker"
        className="mt-0.5 inline-flex items-center gap-1 text-[10.5px] font-semibold"
        style={{ color: "var(--ed-accent)" }}
      >
        <HelpCircle className="h-3 w-3 shrink-0" aria-hidden />
        Who is this?
      </span>
    </button>
  )
}
