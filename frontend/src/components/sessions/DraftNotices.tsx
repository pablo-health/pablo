// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * DraftNotices
 *
 * Mounted once in the dashboard shell, so a draft that lands while the
 * clinician is anywhere in the app is announced where they are. Each notice
 * links to the session; it stays until followed or dismissed, because a
 * clinician between sessions may not look up for a while.
 *
 * A failed draft links to the session rather than offering a retry: there is
 * no way to ask for the draft again yet, and the session is where its
 * transcript and status are.
 */

"use client"

import Link from "next/link"
import { AlertCircle, CheckCircle2, X } from "lucide-react"
import { useDraftNotices } from "@/hooks/useDraftNotices"
import type { DraftNotice } from "@/lib/draftNotices"

export function DraftNotices() {
  const { notices, dismiss } = useDraftNotices()

  // The live region is always present so a screen reader announces a notice
  // the moment one is added to it.
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[calc(100%-2rem)] max-w-sm flex-col gap-2"
    >
      {notices.map((notice) => (
        <DraftNoticeCard
          key={notice.sessionId}
          notice={notice}
          onDismiss={() => dismiss(notice.sessionId)}
        />
      ))}
    </div>
  )
}

function DraftNoticeCard({
  notice,
  onDismiss,
}: {
  notice: DraftNotice
  onDismiss: () => void
}) {
  const ready = notice.outcome === "ready"
  const href = `/dashboard/sessions/${notice.sessionId}`

  return (
    <div
      role="status"
      data-testid="draft-notice"
      className="pointer-events-auto flex items-start gap-3 rounded-lg border border-neutral-200 bg-white px-4 py-3 shadow-lg"
    >
      {ready ? (
        <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-primary-600" aria-hidden="true" />
      ) : (
        <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" aria-hidden="true" />
      )}
      <div className="flex-1 space-y-1 text-sm">
        <p className="font-medium text-neutral-900">
          {ready
            ? `Draft ready: ${notice.sessionLabel} session`
            : `Couldn't draft the ${notice.sessionLabel} session`}
        </p>
        <Link
          href={href}
          onClick={onDismiss}
          className="font-medium text-primary-700 underline-offset-2 hover:underline"
        >
          {ready ? "Review draft" : "Open session"}
        </Link>
      </div>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss"
        className="shrink-0 rounded-md p-1 text-neutral-500 transition-colors hover:bg-neutral-100 hover:text-neutral-900"
      >
        <X className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  )
}
