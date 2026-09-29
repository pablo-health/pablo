// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's messages: what clients wrote through the portal, and the
 * practice's answers.
 *
 * Two views of the same inbox. **Conversations** groups by thread, one row
 * each, unread first. **Messages** lists every message a client sent on its
 * own row, newest first, so nothing is folded out of sight inside a thread.
 * Either one opens the conversation beside the list (or in place of it, on a
 * narrow screen), which is where the practice replies.
 */

"use client"

import Link from "next/link"
import { useState } from "react"
import { MessageSquare } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { formatInUserTimeZone, useUserTimeZone } from "@/hooks/usePreferences"
import { useInboxMessages, useInboxThreads } from "@/hooks/useMessageInbox"
import { usePortalSettings } from "@/hooks/usePortalSettings"
import type { ThreadStatusFilter } from "@/lib/api/messageInbox"
import { ThreadView } from "./ThreadView"

type View = "conversations" | "messages"

interface Selected {
  threadId: string
  patientId: string
  patientName: string
}

const WHEN: Intl.DateTimeFormatOptions = {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
}

export function MessagesInbox() {
  const { data: portal, isLoading: portalLoading } = usePortalSettings()

  // Wait for the practice's answer before loading anything: listing messages
  // and then replacing them with "turned off" would show what it then denies.
  // A deployment without the setting (an error, not loading) carries on.
  if (portalLoading) {
    return (
      <div className="space-y-2" data-testid="messages-loading">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    )
  }

  // When clients cannot reach Messages — the whole portal is off, or the
  // practice turned the Messages part off — what they already sent is still
  // the practice's to read, so the lists stay. What goes is replying: a reply
  // would land somewhere the client cannot open.
  const off =
    portal?.enabled === false
      ? "Your client portal is off."
      : portal?.modules?.messaging === false
        ? "Messages are turned off in your client portal."
        : null
  const repliesOffNote =
    portal?.enabled === false
      ? "Turn the client portal back on to reply."
      : portal?.modules?.messaging === false
        ? "Turn Messages back on to reply."
        : null

  return (
    <div className="space-y-3">
      {off && (
        <div
          className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3"
          data-testid="messages-portal-off"
        >
          <p className="text-sm text-neutral-800">{off}</p>
          <Link href="/dashboard/settings/portal" className="text-sm font-medium underline">
            Client portal settings
          </Link>
        </div>
      )}
      <InboxPanes repliesOffNote={repliesOffNote} />
    </div>
  )
}

function InboxPanes({ repliesOffNote }: { repliesOffNote: string | null }) {
  const [view, setView] = useState<View>("conversations")
  const [selected, setSelected] = useState<Selected | null>(null)

  return (
    <div className="grid min-h-[70vh] overflow-hidden rounded-2xl border border-neutral-200 bg-card md:grid-cols-[380px_1fr]">
      <section className={`min-h-0 border-neutral-200 md:border-r ${selected ? "hidden md:block" : ""}`}>
        <div className="flex gap-1 border-b border-neutral-200 p-2" role="tablist" aria-label="View">
          <ViewTab label="Conversations" active={view === "conversations"} onClick={() => setView("conversations")} />
          <ViewTab label="Messages" active={view === "messages"} onClick={() => setView("messages")} />
        </div>
        {view === "conversations" ? (
          <Conversations selectedId={selected?.threadId ?? null} onOpen={setSelected} />
        ) : (
          <EveryMessage selectedId={selected?.threadId ?? null} onOpen={setSelected} />
        )}
      </section>
      <section className={`min-h-0 ${selected ? "" : "hidden md:block"}`}>
        {selected ? (
          <ThreadView
            key={selected.threadId}
            threadId={selected.threadId}
            patientId={selected.patientId}
            patientName={selected.patientName}
            onBack={() => setSelected(null)}
            repliesOffNote={repliesOffNote}
          />
        ) : (
          <div className="flex h-full items-center justify-center p-8 text-sm text-neutral-500">
            Choose a message to read it.
          </div>
        )}
      </section>
    </div>
  )
}

function ViewTab({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`flex-1 rounded-lg px-3 py-1.5 text-sm font-medium ${
        active ? "bg-primary-50 text-primary-700" : "text-neutral-600 hover:bg-neutral-100"
      }`}
    >
      {label}
    </button>
  )
}

interface ListProps {
  selectedId: string | null
  onOpen: (selected: Selected) => void
}

function Conversations({ selectedId, onOpen }: ListProps) {
  const [status, setStatus] = useState<ThreadStatusFilter>("open")
  const { data, isLoading, isError, refetch } = useInboxThreads(status)
  const timeZone = useUserTimeZone()

  return (
    <div>
      <div className="flex gap-3 px-4 pt-3 text-sm">
        {(["open", "closed"] as const).map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={status === option}
            onClick={() => setStatus(option)}
            className={status === option ? "font-semibold text-neutral-900" : "text-neutral-500"}
          >
            {option === "open" ? "Open" : "Closed"}
          </button>
        ))}
      </div>
      <ListBody isLoading={isLoading} isError={isError} onRetry={() => void refetch()} empty={data?.data.length === 0}>
        <ul data-testid="conversation-list">
          {data?.data.map((thread) => (
            <li key={thread.id}>
              <RowButton
                active={selectedId === thread.id}
                onClick={() =>
                  onOpen({ threadId: thread.id, patientId: thread.patient_id, patientName: thread.patient_name })
                }
                title={thread.patient_name}
                detail={thread.subject || "No subject"}
                when={formatInUserTimeZone(thread.last_message_at, timeZone, WHEN)}
                unread={thread.unread_count ?? 0}
                testId="conversation-row"
              />
            </li>
          ))}
        </ul>
      </ListBody>
    </div>
  )
}

function EveryMessage({ selectedId, onOpen }: ListProps) {
  const [unreadOnly, setUnreadOnly] = useState(false)
  const { data, isLoading, isError, refetch } = useInboxMessages(unreadOnly)
  const timeZone = useUserTimeZone()

  return (
    <div>
      <label className="flex items-center gap-2 px-4 pt-3 text-sm text-neutral-600">
        <input type="checkbox" checked={unreadOnly} onChange={(event) => setUnreadOnly(event.target.checked)} />
        Unread only
      </label>
      <ListBody isLoading={isLoading} isError={isError} onRetry={() => void refetch()} empty={data?.data.length === 0}>
        <ul data-testid="message-list">
          {data?.data.map((message) => (
            <li key={message.id}>
              <RowButton
                active={selectedId === message.thread_id}
                onClick={() =>
                  onOpen({
                    threadId: message.thread_id,
                    patientId: message.patient_id,
                    patientName: message.patient_name,
                  })
                }
                title={message.patient_name}
                detail={message.body}
                when={formatInUserTimeZone(message.created_at, timeZone, WHEN)}
                unread={message.unread ? 1 : 0}
                dot
                testId="message-row"
              />
            </li>
          ))}
        </ul>
      </ListBody>
    </div>
  )
}

interface ListBodyProps {
  isLoading: boolean
  isError: boolean
  empty: boolean
  onRetry: () => void
  children: React.ReactNode
}

function ListBody({ isLoading, isError, empty, onRetry, children }: ListBodyProps) {
  if (isLoading) {
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    )
  }
  if (isError) {
    return (
      <div className="p-6 text-center" role="alert">
        <p className="text-sm text-neutral-700">Messages didn&rsquo;t load.</p>
        <Button variant="outline" size="sm" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      </div>
    )
  }
  if (empty) {
    return (
      <div className="flex flex-col items-center gap-2 p-8 text-center text-sm text-neutral-500" data-testid="messages-empty">
        <MessageSquare className="h-6 w-6" aria-hidden="true" />
        Nothing here.
      </div>
    )
  }
  return <>{children}</>
}

interface RowButtonProps {
  active: boolean
  onClick: () => void
  title: string
  detail: string
  when: string
  unread: number
  /** A dot rather than a count, for a single message. */
  dot?: boolean
  testId: string
}

function RowButton({ active, onClick, title, detail, when, unread, dot, testId }: RowButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      data-testid={testId}
      data-unread={unread > 0 ? "true" : "false"}
      className={`flex w-full items-start gap-3 border-b border-neutral-100 px-4 py-3 text-left ${
        active ? "bg-primary-50" : "hover:bg-neutral-50"
      }`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className={`truncate text-sm ${unread > 0 ? "font-semibold text-neutral-900" : "text-neutral-800"}`}>
            {title}
          </span>
          <span className="shrink-0 text-xs text-neutral-500">{when}</span>
        </div>
        <p className="truncate text-sm text-neutral-600">{detail}</p>
      </div>
      {unread > 0 &&
        (dot ? (
          <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary-600" aria-label="Unread" />
        ) : (
          <span className="shrink-0 rounded-full bg-primary-600 px-2 py-0.5 text-xs font-semibold text-white" aria-label={`${unread} unread`}>
            {unread}
          </span>
        ))}
    </button>
  )
}
