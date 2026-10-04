// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { format } from "date-fns"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import {
  useBookedOnItsOwn,
  useSeenBookedOnItsOwn,
  useUndoBookedOnItsOwn,
} from "@/hooks/useOutsideSessions"
import { NoticeButton } from "./GoogleChangeNotice"

/**
 * The sessions Pablo booked on its own because an event's title named the
 * client, above the grid until the clinician has looked.
 *
 * The first read after connecting a calendar can book a dozen at once, and
 * none of them asked, so each is listed with its client and time and can be
 * undone. Undo is the ordinary cancel: the event stays answered, so the next
 * read leaves it cancelled. OK clears the list; the sessions stay booked.
 */
export function BookedOnItsOwnNotice() {
  const people = usePeopleTerm()
  const { data } = useBookedOnItsOwn()
  const undo = useUndoBookedOnItsOwn()
  const seen = useSeenBookedOnItsOwn()
  const [open, setOpen] = useState(true)
  const sessions = data?.sessions ?? []
  if (sessions.length === 0) return null
  const pending = undo.isPending || seen.isPending

  return (
    <div
      role="status"
      data-testid="booked-on-its-own"
      className="flex flex-col gap-2 rounded-lg px-4 py-2.5 text-sm"
      style={{
        backgroundColor: "var(--ed-canvas-elev)",
        color: "var(--ed-ink)",
        border: "1px solid var(--ed-hairline-strong)",
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <p className="mr-auto font-medium">
          {sessions.length === 1
            ? "Pablo booked 1 session from your calendar"
            : `Pablo booked ${sessions.length} sessions from your calendar`}
        </p>
        <NoticeButton disabled={pending} onClick={() => setOpen((o) => !o)}>
          {open ? "Hide" : "Show"}
        </NoticeButton>
        <NoticeButton
          disabled={pending}
          onClick={() => seen.mutate(sessions.map((s) => s.appointment_id))}
        >
          OK
        </NoticeButton>
      </div>
      {open && (
        <>
          <p className="text-xs" style={{ color: "var(--ed-ink-muted)" }}>
            Each title had a {people.one}&rsquo;s full name.
          </p>
          <ul aria-label="Sessions Pablo booked" className="flex flex-col gap-1.5">
            {sessions.map((session) => (
              <li
                key={session.appointment_id}
                data-testid="booked-on-its-own-row"
                className="flex flex-wrap items-center gap-2"
              >
                <span className="mr-auto">
                  <span className="font-medium">{session.client_name}</span>,{" "}
                  {format(new Date(session.start_at), "EEE MMM d, h:mm a")}
                </span>
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => undo.mutate(session.appointment_id)}
                  aria-label={`Undo ${session.client_name}, ${format(new Date(session.start_at), "EEE MMM d, h:mm a")}`}
                  className="rounded-full px-3 py-1 text-[12.5px] font-bold disabled:opacity-50"
                  style={{ border: "1px solid currentColor" }}
                >
                  Undo
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
