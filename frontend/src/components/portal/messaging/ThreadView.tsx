// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One thread, oldest message first, with the composer under it.
 *
 * Two voices, not three. A message the practice sent automatically
 * arrives with sender `practice` rather than `clinician`; it is still
 * the practice talking, so it gets the practice's styling and is
 * labelled "Your practice" instead of a person.
 *
 * Opening the thread marks what the practice sent as read, once. The
 * guard is per thread id, so re-rendering the same thread does not send
 * a second call and moving between threads still marks each one.
 *
 * The thread's status is shown, but there is nothing here to close a
 * thread with. That lifecycle belongs to the practice side.
 */

"use client"

import { useEffect, useRef } from "react"
import { Button } from "@/components/ui/button"
import type {
  PatientMessage,
  PatientMessageThreadDetail,
} from "@/lib/api/patientMessages"
import { MessageComposer } from "./MessageComposer"

export interface ThreadViewProps {
  thread: PatientMessageThreadDetail
  onMarkRead: (threadId: string) => void
  onSend: (body: string) => Promise<unknown>
  sending: boolean
  slaText?: string | null
  sendError?: string | null
  onBack?: () => void
}

function senderLabel(sender: PatientMessage["sender"]): string {
  return sender === "patient" ? "You" : "Your practice"
}

function formatSent(value: string): string {
  return new Date(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function ThreadView({
  thread,
  onMarkRead,
  onSend,
  sending,
  slaText,
  sendError,
  onBack,
}: ThreadViewProps) {
  const markedThreadId = useRef<string | null>(null)

  useEffect(() => {
    if (markedThreadId.current === thread.id) return
    markedThreadId.current = thread.id
    onMarkRead(thread.id)
  }, [thread.id, onMarkRead])

  return (
    <div className="flex flex-col gap-4" data-testid="portal-messaging-thread-view">
      <div className="flex items-center justify-between gap-3">
        <div className="flex flex-col">
          <h2 className="text-base font-semibold text-neutral-900">
            {thread.subject?.trim() ? thread.subject : "Message"}
          </h2>
          <span
            data-testid="portal-messaging-thread-status"
            className="text-xs text-neutral-500"
          >
            {thread.status}
          </span>
        </div>
        {onBack && (
          <Button variant="ghost" data-testid="portal-messaging-back" onClick={onBack}>
            Back
          </Button>
        )}
      </div>

      <ul className="flex flex-col gap-3">
        {thread.messages.map((message) => {
          const fromPatient = message.sender === "patient"
          return (
            <li
              key={message.id}
              data-testid={`portal-messaging-message-${message.id}`}
              data-sender={fromPatient ? "patient" : "practice"}
              className={
                fromPatient
                  ? "self-end rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground"
                  : "self-start rounded-md bg-neutral-100 px-3 py-2 text-sm text-neutral-900"
              }
            >
              <p className="text-xs opacity-80">
                {senderLabel(message.sender)} · {formatSent(message.created_at)}
              </p>
              <p className="mt-1 whitespace-pre-wrap">{message.body}</p>
            </li>
          )
        })}
      </ul>

      <MessageComposer
        onSend={onSend}
        sending={sending}
        slaText={slaText}
        error={sendError}
      />
    </div>
  )
}
