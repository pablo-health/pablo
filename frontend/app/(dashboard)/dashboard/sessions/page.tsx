// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Review Worklist Page
 *
 * Cross-patient queue of sessions that still need clinician attention before
 * they are finalized (in-flight SOAP generation, pending review, or failed).
 * Finalized and cancelled sessions are archived under the patient chart, not
 * here. This is the end-of-day "what's left to sign off" view.
 */

"use client"

import { CheckCircle2 } from "lucide-react"
import { SessionsTable } from "@/components/sessions/SessionsTable"
import type { SessionStatus } from "@/types/sessions"

// Sessions in one of these states are awaiting clinician action: still
// processing, ready for review, or failed and needing a retry. Terminal
// states (finalized, cancelled) and not-yet-recorded states live elsewhere.
const REVIEW_STATUSES: ReadonlySet<SessionStatus> = new Set([
  "queued",
  "processing",
  "pending_review",
  "failed",
])

export default function ReviewPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-display font-bold text-neutral-900">
          Review
        </h1>
        <p className="text-neutral-600 mt-2">
          Sessions waiting for your review before they&apos;re finalized.
        </p>
      </div>

      <SessionsTable
        filter={(session) => REVIEW_STATUSES.has(session.status)}
        emptyState={
          <div className="flex flex-col items-center gap-3">
            <CheckCircle2 className="h-10 w-10 text-secondary-500" />
            <p className="text-neutral-700 font-medium">You&apos;re all caught up.</p>
            <p className="text-sm text-neutral-500">
              No sessions are waiting for review. New notes start from a
              patient&apos;s chart.
            </p>
          </div>
        }
      />
    </div>
  )
}
