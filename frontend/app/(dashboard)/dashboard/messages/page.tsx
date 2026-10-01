// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect } from "react"
import { useThread } from "@/hooks/useMessageInbox"

const INBOX_MESSAGES = "/dashboard/inbox?filter=messages"

/**
 * Messages lives in the Inbox now, one item per client message. This address
 * stays for old links: it goes to the Inbox's Messages filter, and a link to
 * one conversation (`?thread=`) goes to that client's latest message in it.
 */
function MessagesRedirect() {
  const router = useRouter()
  const threadId = useSearchParams().get("thread")
  const { data: thread, isError } = useThread(threadId)

  useEffect(() => {
    if (!threadId || isError) {
      router.replace(INBOX_MESSAGES)
      return
    }
    if (!thread) return
    const latest = [...thread.messages].reverse().find((message) => message.sender === "patient")
    router.replace(latest ? `${INBOX_MESSAGES}&item=${encodeURIComponent(latest.id)}` : INBOX_MESSAGES)
  }, [threadId, thread, isError, router])

  return null
}

export default function MessagesPage() {
  return (
    <Suspense fallback={null}>
      <MessagesRedirect />
    </Suspense>
  )
}
