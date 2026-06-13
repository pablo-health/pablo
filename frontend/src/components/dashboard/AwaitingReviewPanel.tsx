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
import { FileText } from "lucide-react"
import { useDashboardSummary } from "@/hooks/useDashboard"
import { useUserTimeZone, formatInUserTimeZone } from "@/hooks/usePreferences"
import { SessionStatusBadge } from "@/components/sessions/SessionStatusBadge"

export function AwaitingReviewPanel() {
  const { data, isLoading } = useDashboardSummary()
  const timeZone = useUserTimeZone()

  // The server returns the inline rows (already sorted, newest first) plus the
  // true total over the full set — so the count is correct even when there are
  // more pending reviews than fit inline.
  const rows = data?.awaiting_review ?? []
  const total = data?.awaiting_review_total ?? 0

  // Quietly absent while loading or when there's nothing to review.
  if (isLoading || total === 0) return null

  const overflow = total - rows.length

  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-display font-semibold text-neutral-900">
          Notes awaiting review
        </h2>
        <span className="text-sm text-neutral-500">{total}</span>
      </div>
      <p className="text-sm text-neutral-600 mt-1 mb-4">
        Drafts ready for your review and signature.
      </p>

      <ul className="space-y-1">
        {rows.map((session) => (
          <li key={session.session_id}>
            <Link
              href={`/dashboard/sessions/${session.session_id}`}
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
                  {formatInUserTimeZone(session.session_date, timeZone, {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  })}
                </span>
              </span>
              <SessionStatusBadge
                status={session.status}
                sessionId={session.session_id}
                timestamp={session.note_finalized_at}
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
          View all {total} →
        </Link>
      )}
    </div>
  )
}
