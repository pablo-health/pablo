// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  closeThread,
  getThread,
  getUnreadThreadCount,
  listInboxMessages,
  listInboxThreads,
  markThreadRead,
  reopenThread,
  replyToThread,
  type InboxMessageList,
  type InboxThreadList,
  type ThreadDetail,
  type ThreadMessage,
  type ThreadStatusFilter,
} from "@/lib/api/messageInbox"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export const messageInboxKeys = {
  all: ["messageInbox"] as const,
  threads: (status: ThreadStatusFilter) => [...messageInboxKeys.all, "threads", status] as const,
  messages: (unreadOnly: boolean) => [...messageInboxKeys.all, "messages", unreadOnly] as const,
  thread: (threadId: string) => [...messageInboxKeys.all, "thread", threadId] as const,
  unread: () => [...messageInboxKeys.all, "unread"] as const,
}

/** How often the badge and the lists look for new messages. */
const POLL_MS = 60_000

export function useInboxThreads(status: ThreadStatusFilter) {
  return useAuthQuery<InboxThreadList>({
    queryKey: messageInboxKeys.threads(status),
    queryFn: () => listInboxThreads(status),
    refetchInterval: POLL_MS,
  })
}

export function useInboxMessages(unreadOnly: boolean) {
  return useAuthQuery<InboxMessageList>({
    queryKey: messageInboxKeys.messages(unreadOnly),
    queryFn: () => listInboxMessages(unreadOnly),
    refetchInterval: POLL_MS,
  })
}

/** The nav badge. `retry: false`: a deployment without the portal answers
 * 404, and the badge simply does not show. */
export function useUnreadThreadCount(enabled = true) {
  return useAuthQuery<{ threads_with_unread: number }>({
    queryKey: messageInboxKeys.unread(),
    queryFn: () => getUnreadThreadCount(),
    refetchInterval: POLL_MS,
    retry: false,
    enabled,
  })
}

export function useThread(threadId: string | null) {
  return useAuthQuery<ThreadDetail>({
    queryKey: messageInboxKeys.thread(threadId ?? ""),
    queryFn: () => getThread(threadId as string),
    enabled: threadId !== null,
  })
}

/** Every write re-reads the whole inbox: counts, order and status all move. */
const EVERYTHING = [messageInboxKeys.all]

export function useReplyToThread(threadId: string) {
  return useAuthMutation<ThreadMessage, string>({
    mutationFn: (body) => replyToThread(threadId, body),
    invalidateKeys: EVERYTHING,
  })
}

export function useMarkThreadRead() {
  return useAuthMutation<unknown, string>({
    mutationFn: (threadId) => markThreadRead(threadId),
    invalidateKeys: EVERYTHING,
  })
}

export function useCloseThread(threadId: string) {
  return useAuthMutation<unknown, void>({
    mutationFn: () => closeThread(threadId),
    invalidateKeys: EVERYTHING,
  })
}

export function useReopenThread(threadId: string) {
  return useAuthMutation<unknown, void>({
    mutationFn: () => reopenThread(threadId),
    invalidateKeys: EVERYTHING,
  })
}
