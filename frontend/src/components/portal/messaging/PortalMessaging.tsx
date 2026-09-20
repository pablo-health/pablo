// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Messaging in the patient portal: the list, one thread, and writing.
 *
 * The whole surface hangs off the patient session token it is handed.
 * Nothing here goes looking for a signed-in user, because on this
 * surface there isn't one.
 *
 * After a send, the affected queries are refetched rather than patched
 * in place. The server decides message ids and timestamps and there is
 * no idempotency key to reconcile an optimistic entry against, so a
 * refetch is both simpler and the only version that is certainly true.
 *
 * Reply-time text is a query of its own, allowed to fail quietly: a
 * deployment that does not serve it, or an error fetching it, both land
 * on the notice's own default rather than blocking the conversation.
 */

"use client"

import { useCallback, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  getMessagingSettings,
  getThread,
  listThreads,
  markThreadRead,
  sendMessage,
  startThread,
} from "@/lib/api/patientMessages"
import { NewThreadFlow } from "./NewThreadFlow"
import { ThreadList } from "./ThreadList"
import { ThreadView } from "./ThreadView"

const SEND_FAILED = "That didn't send. Try again."
const LOAD_FAILED = "Messages aren't loading right now. Try again in a moment."

const keys = {
  threads: (token: string) => ["patient-messages", "threads", token] as const,
  thread: (token: string, id: string) =>
    ["patient-messages", "thread", token, id] as const,
  settings: (token: string) => ["patient-messages", "settings", token] as const,
}

export interface PortalMessagingProps {
  sessionToken: string
}

export function PortalMessaging({ sessionToken }: PortalMessagingProps) {
  const queryClient = useQueryClient()
  const [openThreadId, setOpenThreadId] = useState<string | null>(null)
  const [composing, setComposing] = useState(false)

  const threads = useQuery({
    queryKey: keys.threads(sessionToken),
    queryFn: () => listThreads(sessionToken),
  })

  const settings = useQuery({
    queryKey: keys.settings(sessionToken),
    queryFn: () => getMessagingSettings(sessionToken),
    retry: false,
  })
  const slaText = settings.data?.sla_text ?? null

  const thread = useQuery({
    queryKey: keys.thread(sessionToken, openThreadId ?? ""),
    queryFn: () => getThread(sessionToken, openThreadId as string),
    enabled: openThreadId !== null,
  })

  const invalidateThreads = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: keys.threads(sessionToken) })
  }, [queryClient, sessionToken])

  const markRead = useMutation({
    mutationFn: (threadId: string) => markThreadRead(sessionToken, threadId),
    onSuccess: invalidateThreads,
  })

  const send = useMutation({
    mutationFn: (body: string) =>
      sendMessage(sessionToken, openThreadId as string, body),
    onSuccess: () => {
      if (openThreadId) {
        void queryClient.invalidateQueries({
          queryKey: keys.thread(sessionToken, openThreadId),
        })
      }
      invalidateThreads()
    },
  })

  const start = useMutation({
    mutationFn: (input: { subject: string | null; body: string }) =>
      startThread(sessionToken, input),
    onSuccess: (created) => {
      invalidateThreads()
      setComposing(false)
      setOpenThreadId(created.id)
    },
  })

  // `mutate` and `reset` are stable across renders; the mutation object is
  // not. Depending on the object would rebuild these callbacks every render
  // and restart the effects that take them.
  const markReadMutate = markRead.mutate
  const sendReset = send.reset

  const handleMarkRead = useCallback(
    (threadId: string) => {
      markReadMutate(threadId)
    },
    [markReadMutate],
  )

  const handleOpenThread = useCallback((threadId: string) => {
    setComposing(false)
    setOpenThreadId(threadId)
  }, [])

  const handleBack = useCallback(() => {
    setOpenThreadId(null)
    sendReset()
  }, [sendReset])

  if (composing) {
    return (
      <NewThreadFlow
        onStart={(input) => start.mutateAsync(input)}
        starting={start.isPending}
        slaText={slaText}
        error={start.isError ? SEND_FAILED : null}
        onCancel={() => {
          start.reset()
          setComposing(false)
        }}
      />
    )
  }

  if (openThreadId !== null) {
    if (thread.isError) {
      return (
        <p data-testid="portal-messaging-error" className="text-sm text-neutral-600">
          {LOAD_FAILED}
        </p>
      )
    }
    if (!thread.data) {
      return (
        <p data-testid="portal-messaging-loading" className="text-sm text-neutral-600">
          Loading…
        </p>
      )
    }
    return (
      <ThreadView
        thread={thread.data}
        onMarkRead={handleMarkRead}
        onSend={(body) => send.mutateAsync(body)}
        sending={send.isPending}
        slaText={slaText}
        sendError={send.isError ? SEND_FAILED : null}
        onBack={handleBack}
      />
    )
  }

  if (threads.isError) {
    return (
      <p data-testid="portal-messaging-error" className="text-sm text-neutral-600">
        {LOAD_FAILED}
      </p>
    )
  }

  if (!threads.data) {
    return (
      <p data-testid="portal-messaging-loading" className="text-sm text-neutral-600">
        Loading…
      </p>
    )
  }

  return (
    <ThreadList
      threads={threads.data.data}
      onOpenThread={handleOpenThread}
      onStartThread={() => setComposing(true)}
      slaText={slaText}
    />
  )
}
