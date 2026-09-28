// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The messaging tile's line on the portal home screen: how many messages
 * the patient has not read yet.
 *
 * Reads the thread list under the same query key as the messaging section,
 * so opening it draws from what the tile just fetched. The count is the
 * server's per-thread `unread_count`, summed; a thread that carries none
 * counts as nothing unread rather than as a guess.
 *
 * Renders nothing while loading or on a failed fetch.
 */

"use client"

import { useQuery } from "@tanstack/react-query"
import { listThreads } from "@/lib/api/patientMessages"
import { keys } from "./PortalMessaging"

export function messagingSummaryLine(unread: number): string {
  return unread === 0 ? "No new messages" : `${unread} unread`
}

export function MessagingSummary({ sessionToken }: { sessionToken: string }) {
  const threads = useQuery({
    queryKey: keys.threads(sessionToken),
    queryFn: () => listThreads(sessionToken),
  })
  if (!threads.data) return null
  const unread = threads.data.data.reduce((sum, thread) => sum + (thread.unread_count ?? 0), 0)
  return <>{messagingSummaryLine(unread)}</>
}
