// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One conversation, as the practice reads and answers it.
 *
 * Opening a thread marks it read for the practice — the mark every unread
 * count is measured from — once its messages have loaded, so a thread that
 * failed to load is not quietly marked seen. A reply goes into the same
 * thread the client reads in the portal; answering a closed thread reopens
 * it, which is the server's rule, not this screen's.
 */

"use client"

import Link from "next/link"
import { useEffect, useRef, useState } from "react"
import { ArrowLeft, Paperclip } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { formatInUserTimeZone, useUserTimeZone } from "@/hooks/usePreferences"
import {
  useCloseThread,
  useMarkThreadRead,
  useReopenThread,
  useReplyToThread,
  useThread,
} from "@/hooks/useMessageInbox"
import { getPatientDocumentDownloadUrl } from "@/lib/api/patientDocuments"
import type { MessageAttachment, ThreadMessage } from "@/lib/api/messageInbox"

const WHEN: Intl.DateTimeFormatOptions = {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
}

interface ThreadViewProps {
  threadId: string
  patientId: string
  patientName: string
  /** Back to the list, on a screen too narrow to show both. */
  onBack: () => void
  /**
   * Set when the client cannot reach Messages now, so there is nowhere for a
   * reply to go: what to do about it, shown in place of the reply box.
   */
  repliesOffNote?: string | null
}

export function ThreadView({
  threadId,
  patientId,
  patientName,
  onBack,
  repliesOffNote = null,
}: ThreadViewProps) {
  const { data: thread, isLoading, isError, refetch } = useThread(threadId)
  const markRead = useMarkThreadRead()
  const markedFor = useRef<string | null>(null)

  // Marked read once per thread AND per message count: a message that arrives
  // while the thread is open is seen too, rather than holding the badge up
  // until the thread is closed and reopened.
  useEffect(() => {
    const key = thread ? `${thread.id}:${thread.messages.length}` : null
    if (thread && key !== null && markedFor.current !== key) {
      markedFor.current = key
      markRead.mutate(thread.id)
    }
  }, [thread, markRead])

  if (isLoading) {
    return (
      <div className="space-y-3 p-4" data-testid="thread-loading">
        <Skeleton className="h-6 w-1/3" />
        <Skeleton className="h-20 w-full" />
      </div>
    )
  }
  if (isError || !thread) {
    return (
      <div className="p-6 text-center" role="alert">
        <p className="text-sm text-neutral-700">This conversation didn&rsquo;t load.</p>
        <Button variant="outline" size="sm" className="mt-3" onClick={() => void refetch()}>
          Try again
        </Button>
      </div>
    )
  }

  const closed = thread.status === "closed"

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="thread-view">
      <header className="flex items-start justify-between gap-3 border-b border-neutral-200 p-4">
        <div className="min-w-0">
          <button
            type="button"
            onClick={onBack}
            className="mb-2 inline-flex items-center gap-1 text-sm text-neutral-600 md:hidden"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" /> All messages
          </button>
          <h2 className="truncate text-lg font-semibold text-neutral-900">{patientName}</h2>
          <p className="truncate text-sm text-neutral-600">{thread.subject || "No subject"}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Link
            href={`/dashboard/patients/${patientId}`}
            className="text-sm font-medium text-primary-700 underline"
          >
            Open chart
          </Link>
          <CloseOrReopen threadId={thread.id} closed={closed} />
        </div>
      </header>

      <ol className="flex-1 space-y-3 overflow-y-auto p-4" data-testid="thread-messages">
        {thread.messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
      </ol>

      {repliesOffNote ? (
        <p className="border-t border-neutral-200 p-4 text-sm text-neutral-600" data-testid="thread-replies-off">
          {repliesOffNote}{" "}
          <Link href="/dashboard/settings/portal" className="font-medium underline">
            Client portal settings
          </Link>
        </p>
      ) : (
        <ReplyBox threadId={thread.id} closed={closed} />
      )}
    </div>
  )
}

function MessageBubble({ message }: { message: ThreadMessage }) {
  const timeZone = useUserTimeZone()
  const fromClient = message.sender === "patient"
  return (
    <li
      className={`max-w-[85%] rounded-xl px-3 py-2 ${
        fromClient ? "bg-neutral-100 text-neutral-900" : "ml-auto bg-primary-50 text-neutral-900"
      }`}
      data-testid={fromClient ? "thread-message-client" : "thread-message-practice"}
    >
      <p className="whitespace-pre-wrap text-sm">{message.body}</p>
      {message.attachments.length > 0 && (
        <ul className="mt-2 space-y-1">
          {message.attachments.map((attachment) => (
            <AttachmentLink key={attachment.document_id} attachment={attachment} />
          ))}
        </ul>
      )}
      <p className="mt-1 text-xs text-neutral-500">
        {fromClient ? "Client" : "You"} · {formatInUserTimeZone(message.created_at, timeZone, WHEN)}
      </p>
    </li>
  )
}

function AttachmentLink({ attachment }: { attachment: MessageAttachment }) {
  async function open() {
    const url = await getPatientDocumentDownloadUrl(attachment.document_id, undefined, "inline")
    window.open(url, "_blank", "noopener")
  }
  return (
    <li>
      <button
        type="button"
        onClick={() => void open()}
        className="inline-flex items-center gap-1 text-sm text-primary-700 underline"
      >
        <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
        {attachment.filename}
      </button>
    </li>
  )
}

function CloseOrReopen({ threadId, closed }: { threadId: string; closed: boolean }) {
  const close = useCloseThread(threadId)
  const reopen = useReopenThread(threadId)
  const busy = close.isPending || reopen.isPending
  return closed ? (
    <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => reopen.mutate()}>
      Reopen
    </Button>
  ) : (
    <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => close.mutate()}>
      Close
    </Button>
  )
}

function ReplyBox({ threadId, closed }: { threadId: string; closed: boolean }) {
  const reply = useReplyToThread(threadId)
  const [draft, setDraft] = useState("")
  const empty = draft.trim().length === 0

  function send() {
    reply.mutate(draft.trim(), { onSuccess: () => setDraft("") })
  }

  return (
    <div className="border-t border-neutral-200 p-4">
      <label htmlFor="thread-reply" className="sr-only">
        Reply
      </label>
      <Textarea
        id="thread-reply"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder={closed ? "Replying reopens this conversation" : "Write a reply"}
        rows={3}
        data-testid="thread-reply-input"
      />
      {reply.isError && (
        <p className="mt-2 text-sm text-red-700" role="alert">
          Your reply wasn&rsquo;t sent. Try again.
        </p>
      )}
      <div className="mt-2 flex justify-end">
        <Button type="button" onClick={send} disabled={empty || reply.isPending} data-testid="thread-reply-send">
          Send
        </Button>
      </div>
    </div>
  )
}
