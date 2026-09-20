// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient's threads, most recently active first.
 *
 * Unread counts come from the list payload and are rendered as given.
 * Recomputing them here would mean deciding client-side what counts as
 * read, which is a fact the store already owns.
 *
 * With nothing to show, the list carries the expectation notice rather
 * than an empty box: the first thing a patient sees about messaging
 * should be what it is for.
 */

"use client"

import { Button } from "@/components/ui/button"
import type { PatientMessageThread } from "@/lib/api/patientMessages"
import { ExpectationNotice } from "./ExpectationNotice"

export interface ThreadListProps {
  threads: PatientMessageThread[]
  onOpenThread: (threadId: string) => void
  onStartThread: () => void
  slaText?: string | null
}

function formatActivity(value: string): string {
  return new Date(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function ThreadList({
  threads,
  onOpenThread,
  onStartThread,
  slaText,
}: ThreadListProps) {
  if (threads.length === 0) {
    return (
      <div className="flex flex-col gap-3" data-testid="portal-messaging-thread-list-empty">
        <p className="text-sm text-neutral-600">
          You haven&apos;t messaged your practice yet.
        </p>
        <ExpectationNotice slaText={slaText} />
        <Button data-testid="portal-messaging-start-thread" onClick={onStartThread}>
          Write a message
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3" data-testid="portal-messaging-thread-list">
      <ul className="flex flex-col gap-2">
        {threads.map((thread) => {
          const unread = thread.unread_count ?? 0
          return (
            <li key={thread.id}>
              <button
                type="button"
                data-testid={`portal-messaging-thread-${thread.id}`}
                onClick={() => onOpenThread(thread.id)}
                className="flex w-full items-center justify-between gap-3 rounded-md border border-neutral-200 bg-white p-3 text-left hover:bg-neutral-50"
              >
                <span className="flex flex-col">
                  <span className="text-sm font-medium text-neutral-900">
                    {thread.subject?.trim() ? thread.subject : "Message"}
                  </span>
                  <span className="text-xs text-neutral-500">
                    {formatActivity(thread.last_message_at)}
                  </span>
                </span>
                {unread > 0 && (
                  <span
                    data-testid={`portal-messaging-unread-${thread.id}`}
                    aria-label={`${unread} unread`}
                    className="rounded-full bg-primary px-2 py-0.5 text-xs font-medium text-primary-foreground"
                  >
                    {unread}
                  </span>
                )}
              </button>
            </li>
          )
        })}
      </ul>
      <Button
        variant="outline"
        data-testid="portal-messaging-start-thread"
        onClick={onStartThread}
      >
        Write a message
      </Button>
    </div>
  )
}
