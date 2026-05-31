// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AwaitingReviewPanel Component
 *
 * Dashboard surface listing sessions whose SOAP note has finished generating
 * and is waiting for clinician review (status `pending_review`). This is where
 * a note re-surfaces after the clinician dismissed the generating overlay —
 * generation ran to completion in the background, and the note shows up here so
 * it can be found and opened later instead of being lost.
 *
 * Renders nothing when there is nothing awaiting review, so it appears exactly
 * when the clinician has a note to act on.
 */

"use client"

import Link from "next/link"
import { useMemo } from "react"
import { FileText } from "lucide-react"
import { useSessionList } from "@/hooks/useSessions"
import { SessionStatusBadge } from "@/components/sessions/SessionStatusBadge"

// Show at most this many rows inline; the rest live on the Review worklist.
const MAX_ROWS = 5

function formatSessionDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

export function AwaitingReviewPanel() {
  const { data, isLoading } = useSessionList()

  const pending = useMemo(
    () =>
      (data?.data ?? [])
        .filter((s) => s.status === "pending_review")
        // Most recent session date first.
        .sort((a, b) => b.session_date.localeCompare(a.session_date)),
    [data],
  )

  // Quietly absent while loading or when there's nothing to review.
  if (isLoading || pending.length === 0) return null

  const rows = pending.slice(0, MAX_ROWS)
  const overflow = pending.length - rows.length

  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-display font-semibold text-neutral-900">
          Notes awaiting review
        </h2>
        <span className="text-sm text-neutral-500">{pending.length}</span>
      </div>
      <p className="text-sm text-neutral-600 mt-1 mb-4">
        Drafts ready for your review and signature.
      </p>

      <ul className="space-y-1">
        {rows.map((session) => (
          <li key={session.id}>
            <Link
              href={`/dashboard/sessions/${session.id}`}
              className="flex items-center justify-between rounded-md px-3 py-2 -mx-3 hover:bg-neutral-50 transition-colors"
            >
              <span className="flex items-center gap-2 min-w-0">
                <FileText
                  className="h-4 w-4 shrink-0 text-neutral-400"
                  aria-hidden="true"
                />
                <span className="truncate text-sm font-medium text-neutral-900">
                  {session.patient_name}
                </span>
                <span className="shrink-0 text-xs text-neutral-500">
                  {formatSessionDate(session.session_date)}
                </span>
              </span>
              <SessionStatusBadge
                status={session.status}
                sessionId={session.id}
                timestamp={session.note?.finalized_at ?? null}
              />
            </Link>
          </li>
        ))}
      </ul>

      {overflow > 0 && (
        <Link
          href="/dashboard/sessions"
          className="mt-3 inline-block text-sm font-medium text-primary-700 hover:text-primary-800"
        >
          View all {pending.length} →
        </Link>
      )}
    </div>
  )
}
