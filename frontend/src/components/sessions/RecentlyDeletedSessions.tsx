// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Recently Deleted Sessions (PABLO-ocvu)
 *
 * A section under the chart's notes: sessions deleted on their own and
 * still inside the 30-day undo window, each with a Restore action. Mirrors
 * RecentlyDeletedPatients, scoped to one chart. Renders nothing when there
 * is nothing to undo, so a chart with no deletions never carries an empty
 * "Recently deleted" box.
 *
 * Sessions that went with a deleted client are not listed here: they come
 * back with the client, from the Recently deleted tab on the clients page.
 * The backend refuses them with PATIENT_DELETED, which the toast explains.
 *
 * Same wording rules as the patients view (THERAPY-nyb): "deleted" inside
 * the window, never "deleted forever"; show the days remaining so the
 * deadline is visible.
 */

import { Undo2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/Toast"
import { useRecentlyDeletedSessions, useRestoreSession } from "@/hooks/useSessions"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { ApiError } from "@/lib/api/client"
import type { SessionResponse } from "@/types/sessions"

const MS_PER_DAY = 1000 * 60 * 60 * 24

/**
 * Days between now and `deadlineIso`, never below 0. Past-window rows do
 * not come back from the API; this only guards clock skew.
 */
function daysRemaining(deadlineIso: string | null | undefined): number {
  if (!deadlineIso) return 0
  const deadline = new Date(deadlineIso).getTime()
  if (Number.isNaN(deadline)) return 0
  const diff = deadline - Date.now()
  if (diff <= 0) return 0
  return Math.max(1, Math.ceil(diff / MS_PER_DAY))
}

function formatSessionDate(dateIso: string): string {
  const date = new Date(dateIso)
  if (Number.isNaN(date.getTime())) return ""
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
}

interface RecentlyDeletedSessionsProps {
  patientId: string
}

export function RecentlyDeletedSessions({ patientId }: RecentlyDeletedSessionsProps) {
  const people = usePeopleTerm()
  const { showToast } = useToast()
  const { readOnly } = useReadOnlyMode()
  const { data } = useRecentlyDeletedSessions(patientId)
  const restore = useRestoreSession()

  const sessions: SessionResponse[] = data?.data ?? []
  if (sessions.length === 0) return null

  const handleRestore = async (session: SessionResponse) => {
    try {
      await restore.mutateAsync(session.id)
      showToast(`Restored session ${session.session_number}.`, "success")
    } catch (err) {
      // The session went with its client; it comes back with the client.
      const message =
        err instanceof ApiError && err.code === "PATIENT_DELETED"
          ? `Restore the ${people.one} first; this session comes back with them.`
          : "Could not restore the session. Please try again."
      showToast(message, "error")
    }
  }

  return (
    <section
      aria-labelledby="recently-deleted-sessions-heading"
      className="mt-4 border-t border-neutral-100 pt-3"
    >
      <h3
        id="recently-deleted-sessions-heading"
        className="mb-2 text-sm font-semibold text-neutral-700"
      >
        Recently deleted
      </h3>
      <ul className="space-y-2">
        {sessions.map((session) => {
          const remaining = daysRemaining(session.restore_deadline)
          return (
            <li
              key={session.id}
              className="flex items-center justify-between gap-3 rounded-lg border border-neutral-100 px-3 py-2.5"
            >
              <span className="flex min-w-0 items-center gap-2">
                <span className="text-sm font-medium text-neutral-900">
                  Session {session.session_number}
                </span>
                <span className="text-xs text-neutral-500">
                  {formatSessionDate(session.session_date)}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-3">
                <span className="text-xs text-neutral-500">
                  {remaining === 1 ? "1 day remaining" : `${remaining} days remaining`}
                </span>
                {!readOnly && (
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Restore session ${session.session_number}`}
                    disabled={restore.isPending}
                    onClick={() => handleRestore(session)}
                  >
                    <Undo2 className="w-4 h-4 mr-2" />
                    Restore
                  </Button>
                )}
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
